import importlib.util
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "CSV_Consolidator.py"
SPEC = importlib.util.spec_from_file_location("csv_consolidator_sql_tests", MODULE_PATH)
csv_consolidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(csv_consolidator)

CSVEngine = csv_consolidator.CSVEngine
ProcessingConfig = csv_consolidator.ProcessingConfig
SQLQueryError = csv_consolidator.SQLQueryError


class SQLModeTests(unittest.TestCase):
    @staticmethod
    def _engine(config=None):
        return CSVEngine(config or ProcessingConfig(run_manifest_enabled=False))

    def test_all_supported_sql_adapters_expose_schema_and_limitations(self):
        import openpyxl
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            csv_path = root / "input.csv"
            tsv_path = root / "input.tsv"
            jsonl_path = root / "input.jsonl"
            xlsx_path = root / "input.xlsx"
            parquet_path = root / "input.parquet"
            csv_path.write_text("id,name\n1,A\n2,B\n", encoding="utf-8")
            tsv_path.write_text("id\tamount\n1\t10\n2\t20\n", encoding="utf-8")
            jsonl_path.write_text('{"id":1,"name":"A"}\n{"id":2,"name":"B"}\n', encoding="utf-8")

            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.title = "Data"
            sheet.append(["id", "amount"])
            sheet.append([1, 10])
            sheet.append([2, 20])
            workbook.create_sheet("Ignored").append(["not", "active"])
            workbook.save(xlsx_path)
            pq.write_table(
                pa.table({"id": pa.array([1, 2]), "amount": pa.array([10, 20])}),
                parquet_path,
            )

            cases = [
                (csv_path, "SELECT id, name FROM input_0 WHERE id = 2", "duckdb.read_csv"),
                (tsv_path, "SELECT id, amount FROM input_0 WHERE amount > 10", "duckdb.read_csv"),
                (jsonl_path, "SELECT id, name FROM input_0 WHERE id = 2", "duckdb.read_json"),
                (xlsx_path, "SELECT id, amount FROM input_0 WHERE amount > 10", "openpyxl-to-duckdb-csv"),
                (parquet_path, "SELECT id, amount FROM input_0 WHERE amount > 10", "duckdb.read_parquet"),
            ]
            for path, query, adapter in cases:
                with self.subTest(suffix=path.suffix):
                    rows, columns, report = self._engine().sql_query(
                        [path], query, return_report=True
                    )
                    self.assertEqual(columns, ["id", columns[1]])
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["id"], "2")
                    self.assertEqual(report["format"], "csv-power-tool-sql-report")
                    self.assertEqual(report["views"][0]["name"], "input_0")
                    self.assertEqual(report["views"][0]["adapter"], adapter)
                    self.assertTrue(report["views"][0]["schema"])
                    self.assertTrue(report["views"][0]["limitations"])

    def test_sql_join_materializes_multiple_views(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            left = root / "left.csv"
            right = root / "right.csv"
            left.write_text("id,name\n1,A\n2,B\n", encoding="utf-8")
            right.write_text("id,value\n1,10\n2,20\n", encoding="utf-8")

            rows, columns, report = self._engine().sql_query(
                [left, right],
                "SELECT l.id, l.name, r.value FROM input_0 l JOIN input_1 r USING (id)",
                return_report=True,
            )

            self.assertEqual(columns, ["id", "name", "value"])
            self.assertEqual(rows, [
                {"id": "1", "name": "A", "value": "10"},
                {"id": "2", "name": "B", "value": "20"},
            ])
            self.assertEqual([view["name"] for view in report["views"]], ["input_0", "input_1"])

    def test_empty_inputs_are_queryable_and_malformed_sources_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            empty = root / "empty.csv"
            empty.write_text("", encoding="utf-8")
            rows, columns, report = self._engine().sql_query(
                [empty], "SELECT * FROM input_0", return_report=True
            )
            self.assertEqual(rows, [])
            self.assertEqual(columns, ["__empty_input"])
            self.assertTrue(report["views"][0]["empty"])

            malformed = root / "malformed.csv"
            malformed.write_text("id,name\n1,A\n2\n", encoding="utf-8")
            with self.assertRaises(SQLQueryError) as context:
                self._engine().sql_query([malformed], "SELECT * FROM input_0", return_report=True)
            self.assertEqual(context.exception.report["error"]["code"], "malformed_delimited_row")
            self.assertIn("line 3", str(context.exception))

            malformed_json = root / "malformed.jsonl"
            malformed_json.write_text('{"id":1}\nnot-json\n', encoding="utf-8")
            with self.assertRaises(SQLQueryError) as json_context:
                self._engine().sql_query([malformed_json], "SELECT * FROM input_0", return_report=True)
            self.assertEqual(json_context.exception.report["error"]["code"], "source_parse_error")

    def test_sql_result_row_and_cell_limits_are_actionable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.csv"
            source.write_text("id\n1\n2\n", encoding="utf-8")

            with self.assertRaises(SQLQueryError) as row_context:
                self._engine(ProcessingConfig(sql_max_rows=1)).sql_query(
                    [source], "SELECT * FROM input_0", return_report=True
                )
            self.assertEqual(row_context.exception.report["error"]["code"], "result_row_limit")
            self.assertIn("--sql-max-rows", str(row_context.exception))

            with self.assertRaises(SQLQueryError) as cell_context:
                self._engine(ProcessingConfig(sql_max_cell_bytes=3)).sql_query(
                    [source], "SELECT 'long' AS value", return_report=True
                )
            self.assertEqual(cell_context.exception.report["error"]["code"], "result_cell_limit")
            self.assertEqual(cell_context.exception.report["error"]["column"], "value")

    def test_sql_timeout_interrupts_long_running_query(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.csv"
            source.write_text("id\n1\n", encoding="utf-8")
            with self.assertRaises(SQLQueryError) as context:
                self._engine(ProcessingConfig(sql_timeout_seconds=0.05)).sql_query(
                    [source],
                    "SELECT sum(i) FROM range(1000000000000) t(i)",
                    return_report=True,
                )
            self.assertEqual(context.exception.report["error"]["code"], "query_timeout")

    def test_engine_cancellation_interrupts_sql_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.csv"
            source.write_text("id\n1\n", encoding="utf-8")
            engine = self._engine(ProcessingConfig(sql_timeout_seconds=30.0))
            result = {}

            def run_query():
                try:
                    engine.sql_query(
                        [source],
                        "SELECT sum(i) FROM range(1000000000000) t(i)",
                        return_report=True,
                    )
                except SQLQueryError as exc:
                    result["error"] = exc

            worker = threading.Thread(target=run_query)
            worker.start()
            time.sleep(0.1)
            engine.cancel()
            worker.join(10)

            self.assertFalse(worker.is_alive())
            self.assertEqual(result["error"].report["error"]["code"], "query_cancelled")
            self.assertTrue(engine.stats.cancelled)

    def test_read_only_and_multiple_statement_policies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input.csv"
            source.write_text("id\n1\n", encoding="utf-8")
            for query, code in [
                ("DELETE FROM input_0", "read_only_required"),
                ("SELECT * FROM input_0; SELECT 1", "multiple_statements"),
            ]:
                with self.subTest(query=query), self.assertRaises(SQLQueryError) as context:
                    self._engine().sql_query([source], query, return_report=True)
                self.assertEqual(context.exception.report["error"]["code"], code)


class SQLCLIContractTests(unittest.TestCase):
    @staticmethod
    def run_cli(*arguments, timeout=30):
        return subprocess.run(
            [os.environ.get("PYTHON", "python"), str(MODULE_PATH), *arguments],
            cwd=MODULE_PATH.parent,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_cli_sql_report_manifest_and_bounded_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.tsv"
            output = root / "output.csv"
            report_path = root / "sql-report.json"
            source.write_text("id\tname\n1\tA\n2\tB\n", encoding="utf-8")

            result = self.run_cli(
                "--inputs", str(source), "--output", str(output),
                "--sql", "SELECT * FROM input_0 WHERE id = 2",
                "--sql-report", str(report_path),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(encoding="utf-8").splitlines(), ["id,name", "2,B"])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["format"], "csv-power-tool-sql-report")
            self.assertEqual(report["views"][0]["format"], "tsv")
            manifest = json.loads(Path(f"{output}.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["stats"]["sql_report"]["result"]["row_count"], 1)

            output.write_text("previous\n", encoding="utf-8")
            bounded = self.run_cli(
                "--inputs", str(source), "--output", str(output),
                "--sql", "SELECT * FROM input_0",
                "--sql-max-rows", "1", "--sql-report", str(report_path),
                "--errors-json", str(root / "errors.json"), "--no-manifest",
            )
            self.assertEqual(bounded.returncode, 3, bounded.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), "previous\n")
            failed_report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(failed_report["error"]["code"], "result_row_limit")
            self.assertTrue(json.loads((root / "errors.json").read_text(encoding="utf-8"))["errors"])

    @unittest.skipUnless(
        os.environ.get("CSV_POWER_TOOL_EXE"),
        "set CSV_POWER_TOOL_EXE to run the clean packaged SQL smoke test",
    )
    def test_clean_packaged_sql_smoke(self):
        executable = Path(os.environ["CSV_POWER_TOOL_EXE"])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.csv"
            output = root / "output.csv"
            report = root / "sql-report.json"
            source.write_text("id,name\n1,Packaged SQL\n", encoding="utf-8")
            result = subprocess.run(
                [
                    str(executable), "--inputs", str(source), "--output", str(output),
                    "--sql", "SELECT * FROM input_0", "--sql-report", str(report),
                    "--no-manifest",
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Packaged SQL", output.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(report.read_text(encoding="utf-8"))["format"],
                "csv-power-tool-sql-report",
            )


if __name__ == "__main__":
    unittest.main()
