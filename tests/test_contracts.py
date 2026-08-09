import codecs
import csv
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from csv_power_tool.core import EngineService, ProcessRequest


MODULE_PATH = Path(__file__).resolve().parents[1] / "CSV_Consolidator.py"
SPEC = importlib.util.spec_from_file_location("csv_consolidator_contracts", MODULE_PATH)
csv_consolidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(csv_consolidator)

CSVEngine = csv_consolidator.CSVEngine
ProcessingConfig = csv_consolidator.ProcessingConfig


class ParserContractTests(unittest.TestCase):
    def test_bom_dialect_and_leading_zero_values_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "semicolon.csv"
            output_path = root / "output.csv"
            input_path.write_bytes(
                codecs.BOM_UTF8 + 'id;name\n0007;"A;B"\n'.encode("utf-8")
            )

            engine = CSVEngine(ProcessingConfig(dedupe_enabled=False, run_manifest_enabled=False))
            stats = engine.process([input_path], output_path)

            self.assertEqual(stats.final_row_count, 1)
            self.assertEqual(stats.input_diagnostics[str(input_path.resolve())]["delimiter"], ";")
            with output_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [{"id": "0007", "name": "A;B"}])

    def test_cp1252_text_is_decoded_without_data_loss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "windows.csv"
            output_path = root / "output.csv"
            input_path.write_bytes("id,name\n1,Café\n".encode("cp1252"))

            stats = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, run_manifest_enabled=False)
            ).process([input_path], output_path)

            self.assertEqual(stats.final_row_count, 1)
            self.assertIn("Café", output_path.read_text(encoding="utf-8"))

    def test_csv_parser_round_trip_property_for_quoted_values(self):
        values = ["", "001", "comma,value", 'quote "value"', "line one\nline two", "plain"]
        generator = random.Random(20260808)
        records = [
            {"id": f"{index:04d}", "value": generator.choice(values)}
            for index in range(40)
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "generated.csv"
            output_path = root / "round-trip.csv"
            with input_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["id", "value"])
                writer.writeheader()
                writer.writerows(records)

            stats = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, run_manifest_enabled=False)
            ).process([input_path], output_path)

            with output_path.open(newline="", encoding="utf-8") as handle:
                round_tripped = list(csv.DictReader(handle))
            self.assertEqual(stats.final_row_count, len(records))
            self.assertEqual(round_tripped, records)

    def test_xlsx_active_sheet_and_formula_fixture(self):
        import openpyxl

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "workbook.xlsx"
            output_path = root / "output.csv"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.title = "Data"
            sheet.append(["id", "computed"])
            sheet.append(["0007", "=1+2"])
            workbook.create_sheet("Second sheet").append(["ignored"])
            workbook.save(input_path)

            stats = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, run_manifest_enabled=False)
            ).process([input_path], output_path)

            self.assertEqual(stats.final_row_count, 1)
            with output_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [{"id": "0007", "computed": ""}])

    def test_parquet_schema_and_null_fixture(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.parquet"
            output_path = root / "output.csv"
            pq.write_table(
                pa.table({"id": ["0008"], "amount": pa.array([None], type=pa.int64())}),
                input_path,
            )

            stats = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, run_manifest_enabled=False)
            ).process([input_path], output_path)

            self.assertEqual(stats.final_row_count, 1)
            with output_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [{"id": "0008", "amount": ""}])

    def test_cancellation_discards_partial_streaming_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            output_path = root / "output.csv"
            input_path.write_text("id\n1\n2\n", encoding="utf-8")
            output_path.write_text("previous\n", encoding="utf-8")
            engine = None

            def cancel_on_stream(progress, status):
                if status.startswith("Streaming"):
                    engine.cancel()

            engine = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, run_manifest_enabled=False),
                progress_callback=cancel_on_stream,
            )
            stats = engine.process([input_path], output_path)

            self.assertTrue(stats.cancelled)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "previous\n")
            self.assertEqual(list(root.glob(".*output.csv.*")), [])

    def test_engine_service_boundary_accepts_an_injected_engine(self):
        calls = {}

        class FakeEngine:
            def process(self, input_files, output_file):
                calls["input_files"] = input_files
                calls["output_file"] = output_file
                return "fake-stats"

        def factory(config, progress_callback=None, log_callback=None):
            calls["config"] = config
            calls["callbacks"] = (progress_callback, log_callback)
            return FakeEngine()

        config = object()
        request = ProcessRequest.from_paths(["one.csv", Path("two.csv")], "output.csv", config)
        result = EngineService(factory).process(request)

        self.assertEqual(result, "fake-stats")
        self.assertEqual(calls["config"], config)
        self.assertEqual(calls["input_files"], [Path("one.csv"), Path("two.csv")])
        self.assertEqual(calls["output_file"], Path("output.csv"))


class CLIContractTests(unittest.TestCase):
    @staticmethod
    def run_cli(*arguments, timeout=30, input_text=None):
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), *arguments],
            cwd=MODULE_PATH.parent,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )

    def test_cli_success_logs_to_stderr_and_returns_zero(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            output_path = root / "output.csv"
            input_path.write_text("id,name\n1,Alice\n", encoding="utf-8")

            result = self.run_cli(
                "--inputs", str(input_path), "--output", str(output_path),
                "--no-dedupe", "--no-manifest",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertIn("Results:", result.stderr)
            self.assertIn("Alice", output_path.read_text(encoding="utf-8"))

    def test_cli_malformed_input_returns_three_and_preserves_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "malformed.csv"
            output_path = root / "output.csv"
            input_path.write_text("id,name\n1,A\n2\n", encoding="utf-8")
            output_path.write_text("previous\n", encoding="utf-8")

            result = self.run_cli(
                "--inputs", str(input_path), "--output", str(output_path), "--no-manifest"
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "previous\n")
            self.assertIn("Input validation failed", result.stderr)

    def test_cli_help_contract_includes_safety_options(self):
        result = self.run_cli("--help")
        repeat = self.run_cli("--help")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, repeat.stdout)
        self.assertIn("--invalid-row-policy", result.stdout)
        self.assertIn("--collision-policy", result.stdout)
        self.assertIn("--no-manifest", result.stdout)
        self.assertIn("--profile", result.stdout)
        self.assertIn("--repair-edits", result.stdout)
        self.assertIn("--join-report", result.stdout)
        self.assertIn("--conflict-resolution", result.stdout)

    def test_cli_dry_run_and_workflow_replay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            output_path = root / "output.csv"
            workflow_path = root / "workflow.json"
            input_path.write_text("id,name\n1,Replay\n", encoding="utf-8")

            dry_run = self.run_cli(
                "--inputs", str(input_path), "--output", str(output_path), "--dry-run",
                "--save-workflow", str(workflow_path), "--no-manifest",
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertEqual(dry_run.stderr, "")
            self.assertIn('"format": "csv-power-tool-workflow"', dry_run.stdout)
            self.assertFalse(output_path.exists())
            self.assertTrue(workflow_path.exists())

            replay = self.run_cli("--replay", str(workflow_path), "--no-manifest")
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertIn("Replay", output_path.read_text(encoding="utf-8"))

    def test_cli_join_report_and_anti_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            left_path = root / "left.csv"
            right_path = root / "right.csv"
            output_path = root / "joined.csv"
            report_path = root / "join-report.json"
            left_path.write_text("id,name\n1,A\n2,B\n", encoding="utf-8")
            right_path.write_text("id,value\n1,10\n3,30\n", encoding="utf-8")

            result = self.run_cli(
                "--inputs", str(left_path), str(right_path), "--join-on", "id",
                "--join-type", "outer", "--join-report", str(report_path),
                "--output", str(output_path), "--no-dedupe",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["format"], "csv-power-tool-join-report")
            self.assertEqual(report["stages"][0]["unmatched_right_rows"], 1)
            self.assertEqual(report["stages"][0]["cardinality"], "one-to-one")
            self.assertIn("3,,30", output_path.read_text(encoding="utf-8"))
            manifest = json.loads(Path(f"{output_path}.manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["stats"]["join_report"]["conflict_count"], 0)

            anti_output = root / "anti.csv"
            anti = self.run_cli(
                "--inputs", str(left_path), str(right_path), "--join-on", "id",
                "--join-type", "anti", "--output", str(anti_output),
                "--no-manifest", "--no-dedupe",
            )
            self.assertEqual(anti.returncode, 0, anti.stderr)
            self.assertIn("2,B", anti_output.read_text(encoding="utf-8"))
            self.assertNotIn("1,A", anti_output.read_text(encoding="utf-8"))

    def test_cli_three_way_default_is_safe_and_explicit_mark_writes_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base_path = root / "base.csv"
            ours_path = root / "ours.csv"
            theirs_path = root / "theirs.csv"
            output_path = root / "merged.csv"
            report_path = root / "merge-report.json"
            base_path.write_text("id,value\n1,base\n", encoding="utf-8")
            ours_path.write_text("id,value\n1,ours\n", encoding="utf-8")
            theirs_path.write_text("id,value\n1,theirs\n", encoding="utf-8")
            output_path.write_text("previous\n", encoding="utf-8")

            blocked = self.run_cli(
                "--three-way-base", str(base_path), "--three-way-ours", str(ours_path),
                "--three-way-theirs", str(theirs_path), "--key-columns", "id",
                "--merge-report", str(report_path), "--output", str(output_path),
                "--no-manifest",
            )
            self.assertEqual(blocked.returncode, 3, blocked.stderr)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "previous\n")
            blocked_report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(blocked_report["requires_explicit_resolution"])

            marked = self.run_cli(
                "--three-way-base", str(base_path), "--three-way-ours", str(ours_path),
                "--three-way-theirs", str(theirs_path), "--key-columns", "id",
                "--conflict-resolution", "mark", "--merge-report", str(report_path),
                "--output", str(output_path), "--no-manifest",
            )
            self.assertEqual(marked.returncode, 0, marked.stderr)
            self.assertIn("<<<<<<< ours", output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8"))["resolution_policy"],
                "mark",
            )

    def test_cli_stdin_stdout_and_machine_readable_contracts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_path = root / "output.csv"
            stats_path = root / "stats.json"
            errors_path = root / "errors.json"
            streamed = self.run_cli(
                "--inputs", "-", "--stdin-format", "csv", "--output", str(output_path),
                "--no-dedupe", "--no-manifest", "--stats-json", str(stats_path),
                "--errors-json", str(errors_path), input_text="id,name\n001,Pipe\n",
            )
            self.assertEqual(streamed.returncode, 0, streamed.stderr)
            self.assertEqual(streamed.stdout, "")
            self.assertIn("001,Pipe", output_path.read_text(encoding="utf-8"))
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            errors = json.loads(errors_path.read_text(encoding="utf-8"))
            self.assertEqual(stats["format"], "csv-power-tool-cli-stats")
            self.assertEqual(stats["stats"]["final_row_count"], 1)
            self.assertEqual(errors["format"], "csv-power-tool-cli-errors")
            self.assertEqual(errors["errors"], [])
            self.assertEqual(errors["warnings"], [])

            stdout_stats = root / "stdout-stats.json"
            stdout_errors = root / "stdout-errors.json"
            piped = self.run_cli(
                "--inputs", str(output_path), "--output", "-", "--stdout-format", "csv",
                "--no-dedupe", "--no-manifest", "--stats-json", str(stdout_stats),
                "--errors-json", str(stdout_errors),
            )
            self.assertEqual(piped.returncode, 0, piped.stderr)
            self.assertIn("id,name", piped.stdout)
            self.assertIn("001,Pipe", piped.stdout)
            self.assertIn("Results:", piped.stderr)
            self.assertEqual(json.loads(stdout_stats.read_text())["exit_code"], 0)

            empty_input = root / "empty.csv"
            empty_output = root / "empty-output.csv"
            empty_input.write_text("id,name\n", encoding="utf-8")
            empty = self.run_cli(
                "--inputs", str(empty_input), "--output", str(empty_output),
                "--no-dedupe", "--no-manifest",
            )
            self.assertEqual(empty.returncode, 0, empty.stderr)
            self.assertEqual(empty_output.read_text(encoding="utf-8"), "id,name\n")

            jsonl_pipe = self.run_cli(
                "--inputs", "-", "--stdin-format", "jsonl", "--output", "-",
                "--stdout-format", "jsonl", "--no-dedupe", "--no-manifest",
                input_text='{"id":"001","name":"Json pipe"}\n',
            )
            self.assertEqual(jsonl_pipe.returncode, 0, jsonl_pipe.stderr)
            self.assertEqual(json.loads(jsonl_pipe.stdout), {"id": "001", "name": "Json pipe"})

    def test_cli_warning_and_failure_artifacts_are_stable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "malformed.csv"
            output_path = root / "output.csv"
            warning_errors = root / "warning-errors.json"
            failure_errors = root / "failure-errors.json"
            input_path.write_text("id,name\n1,A\n2\n", encoding="utf-8")

            warning = self.run_cli(
                "--inputs", str(input_path), "--output", str(output_path),
                "--invalid-row-policy", "warn", "--no-dedupe", "--no-manifest",
                "--errors-json", str(warning_errors),
            )
            self.assertEqual(warning.returncode, 0, warning.stderr)
            self.assertTrue(json.loads(warning_errors.read_text())["warnings"])

            output_path.write_text("previous\n", encoding="utf-8")
            failure = self.run_cli(
                "--inputs", str(input_path), "--output", str(output_path),
                "--no-dedupe", "--no-manifest", "--errors-json", str(failure_errors),
            )
            self.assertEqual(failure.returncode, 3, failure.stderr)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "previous\n")
            failure_payload = json.loads(failure_errors.read_text())
            self.assertEqual(failure_payload["exit_code"], 3)
            self.assertTrue(failure_payload["errors"])

    def test_bounded_performance_smoke(self):
        row_count = 5_000
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "large.csv"
            output_path = root / "output.csv"
            with input_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "value"])
                writer.writerows((index, f"value-{index}") for index in range(row_count))

            started = time.perf_counter()
            stats = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, run_manifest_enabled=False)
            ).process([input_path], output_path)
            elapsed = time.perf_counter() - started

            self.assertEqual(stats.final_row_count, row_count)
            self.assertLess(elapsed, 30.0, f"bounded performance smoke took {elapsed:.2f}s")

    @unittest.skipUnless(
        os.environ.get("CSV_POWER_TOOL_EXE"),
        "set CSV_POWER_TOOL_EXE to run the clean packaged smoke test",
    )
    def test_clean_packaged_cli_smoke(self):
        executable = Path(os.environ["CSV_POWER_TOOL_EXE"])
        self.assertTrue(executable.is_file())

        version = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=90,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn("CSV Power Tool v", version.stdout)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            output_path = root / "output.csv"
            input_path.write_text("id,name\n1,Packaged\n", encoding="utf-8")
            result = subprocess.run(
                [
                    str(executable), "--inputs", str(input_path), "--output", str(output_path),
                    "--no-dedupe", "--no-manifest",
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Packaged", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
