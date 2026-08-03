import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "CSV_Consolidator.py"
SPEC = importlib.util.spec_from_file_location("csv_consolidator", MODULE_PATH)
csv_consolidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(csv_consolidator)

CSVEngine = csv_consolidator.CSVEngine
ProcessingConfig = csv_consolidator.ProcessingConfig
ConfigHistory = csv_consolidator.ConfigHistory
PreviewPanel = csv_consolidator.PreviewPanel


class CSVEngineTests(unittest.TestCase):
    def test_between_supports_dates_and_timezones(self):
        self.assertTrue(CSVEngine._between("2024-02-15", "2024-02-01..2024-03-01"))
        self.assertTrue(CSVEngine._between("2024-02-15T08:00:00-05:00", "2024-02-15T12:00:00Z..2024-02-15T14:00:00Z"))
        self.assertFalse(CSVEngine._between("2024-04-01", "2024-02-01..2024-03-01"))

    def test_fuzzy_filter_uses_threshold(self):
        self.assertTrue(CSVEngine._fuzzy_match("Acme Incorporated", "acme inc|70"))
        self.assertFalse(CSVEngine._fuzzy_match("Contoso", "acme inc|95"))

    def test_fuzzy_dedup_keeps_similar_rows(self):
        config = ProcessingConfig(
            dedupe_enabled=True,
            dedupe_columns=["name"],
            dedupe_fuzzy_enabled=True,
            dedupe_fuzzy_threshold=80,
        )
        engine = CSVEngine(config)
        rows = [{"name": "Acme Inc"}, {"name": "Acme Incorporated"}, {"name": "Contoso"}]

        result = engine._deduplicate(rows, ["name"])

        self.assertEqual([row["name"] for row in result], ["Acme Inc", "Contoso"])
        self.assertEqual(engine.stats.duplicates_removed, 1)

    def test_dedupe_preview_reports_rows_that_would_be_dropped(self):
        config = ProcessingConfig(dedupe_columns=["id"], dedupe_keep="last")
        report = CSVEngine(config).preview_duplicates(
            [{"id": "1", "value": "old"}, {"id": "1", "value": "new"}, {"id": "2", "value": "ok"}],
            ["id", "value"],
        )

        self.assertEqual(report["duplicate_count"], 1)
        self.assertEqual(report["group_count"], 1)
        self.assertEqual(report["groups"][0]["kept_index"], 1)
        self.assertEqual(report["groups"][0]["dropped_indexes"], [0])

    def test_config_history_undo_redo_truncates_redo_branch(self):
        history = ConfigHistory({"mode": "all"})
        history.record({"mode": "select"})
        history.record({"mode": "exclude"})

        self.assertEqual(history.undo(), {"mode": "select"})
        self.assertEqual(history.undo(), {"mode": "all"})
        self.assertEqual(history.redo(), {"mode": "select"})
        history.record({"mode": "all"})
        self.assertFalse(history.can_redo)

    def test_preview_formatter_is_column_aligned_and_capped(self):
        text = PreviewPanel.format_preview(
            ["id", "name"], [{"id": "1", "name": "A\nB"}]
        )

        self.assertIn("id | name", text)
        self.assertIn("1  | A B", text)

    def test_dedup_can_aggregate_duplicate_rows(self):
        config = ProcessingConfig(
            dedupe_enabled=True,
            dedupe_columns=["sku"],
            dedupe_aggregate_mode="sum",
        )
        engine = CSVEngine(config)
        rows = [{"sku": "A", "qty": "2"}, {"sku": "A", "qty": "3"}]

        result = engine._deduplicate(rows, ["sku", "qty"])

        self.assertEqual(result, [{"sku": "A", "qty": "5"}])
        self.assertEqual(engine.stats.duplicates_removed, 1)

    def test_compute_transform_columns_are_written(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_text("qty,price\n2,3\n", encoding="utf-8")
            config = ProcessingConfig(
                dedupe_enabled=False,
                column_transforms=[("total", "compute", "{qty} * {price}")],
            )

            stats = CSVEngine(config).process([input_path], output_path)

            self.assertEqual(stats.final_row_count, 1)
            with output_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["total"], "6")

    def test_streaming_path_filters_without_accumulating(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_text("name,date\nA,2024-02-15\nB,2024-04-01\n", encoding="utf-8")
            config = ProcessingConfig(
                dedupe_enabled=False,
                sort_enabled=False,
                filters=[("date", "between", "2024-02-01..2024-03-01")],
            )

            stats = CSVEngine(config).process([input_path], output_path)

            self.assertEqual(stats.final_row_count, 1)
            self.assertEqual(stats.rows_filtered, 1)
            with output_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["name"], "A")

    def test_jsonl_round_trip_adds_source_and_column_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.jsonl"
            output_path = Path(temp_dir) / "output.jsonl"
            input_path.write_text(
                '{"id": 1, "email": "one@example.com"}\n'
                '{"id": 2, "email": "two@example.com"}\n',
                encoding="utf-8",
            )
            config = ProcessingConfig(
                dedupe_enabled=False,
                source_column="source",
                redact_sensitive=True,
            )

            stats = CSVEngine(config).process([input_path], output_path)

            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["id"] for row in rows], ["1", "2"])
            self.assertEqual(rows[0]["source"], "input.jsonl")
            self.assertEqual(rows[0]["email"], "[REDACTED]")
            self.assertEqual(stats.column_summary["id"]["distinct_count"], 2)

    def test_polars_backend_reads_text_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_text("id,name\n1,A\n2,B\n", encoding="utf-8")
            config = ProcessingConfig(dedupe_enabled=False, engine_backend="polars")

            stats = CSVEngine(config).process([input_path], output_path)

            self.assertEqual(stats.final_row_count, 2)
            self.assertEqual(output_path.read_text(encoding="utf-8").splitlines()[1], "1,A")

    def test_schema_report_contains_drift_samples_and_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.csv"
            second = Path(temp_dir) / "second.csv"
            report_path = Path(temp_dir) / "schema.json"
            first.write_text("id,name\n1,A\n", encoding="utf-8")
            second.write_text("id,email\n2,b@example.com\n", encoding="utf-8")

            report = CSVEngine(ProcessingConfig(dedupe_enabled=False)).write_schema_report(
                [first, second], report_path
            )

            self.assertEqual(report["union_columns"], ["id", "name", "email"])
            self.assertEqual(report["common_columns"], ["id"])
            self.assertEqual(report["files"][0]["samples"][0]["name"], "A")
            self.assertIn("delimiter_confidence", report["files"][0]["diagnostics"])
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["version"], 1)

    def test_unpivot_and_pivot(self):
        unpivot_config = ProcessingConfig(
            dedupe_enabled=False,
            unpivot_columns=["jan", "feb"],
            unpivot_name_column="month",
            unpivot_value_column="amount",
        )
        engine = CSVEngine(unpivot_config)
        rows, columns = engine._apply_reshape(
            [{"id": "A", "jan": "2", "feb": "3"}],
            ["id", "jan", "feb"],
        )
        self.assertEqual(columns, ["id", "month", "amount"])
        self.assertEqual(rows[1]["month"], "feb")

        pivot_config = ProcessingConfig(
            dedupe_enabled=False,
            pivot_index_columns=["id"],
            pivot_column="month",
            pivot_value_column="amount",
            pivot_aggregate="sum",
        )
        rows, columns = CSVEngine(pivot_config)._apply_reshape(
            [
                {"id": "A", "month": "jan", "amount": "2"},
                {"id": "A", "month": "jan", "amount": "3"},
                {"id": "A", "month": "feb", "amount": "4"},
            ],
            ["id", "month", "amount"],
        )
        self.assertEqual(columns, ["id", "jan", "feb"])
        self.assertEqual(rows, [{"id": "A", "jan": "5", "feb": "4"}])

    def test_join_rows_and_three_way_merge(self):
        joined, columns = CSVEngine.join_rows(
            [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}],
            [{"id": "1", "value": "10"}, {"id": "3", "value": "30"}],
            ["id"],
            "outer",
        )
        self.assertEqual(columns, ["id", "name", "value"])
        self.assertEqual(len(joined), 3)
        self.assertEqual(joined[2]["id"], "3")

        merged, conflicts, columns = CSVEngine.three_way_merge_rows(
            [{"id": "1", "value": "base"}],
            [{"id": "1", "value": "ours"}],
            [{"id": "1", "value": "theirs"}],
            ["id"],
            "mark",
        )
        self.assertEqual(columns, ["id", "value"])
        self.assertEqual(conflicts, [{"key": ["1"], "columns": ["value"]}])
        self.assertIn("<<<<<<< ours", merged[0]["value"])


if __name__ == "__main__":
    unittest.main()
