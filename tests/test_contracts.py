import codecs
import csv
import importlib.util
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
    def run_cli(*arguments, timeout=30):
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), *arguments],
            cwd=MODULE_PATH.parent,
            capture_output=True,
            text=True,
            timeout=timeout,
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

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("--invalid-row-policy", result.stdout)
        self.assertIn("--collision-policy", result.stdout)
        self.assertIn("--no-manifest", result.stdout)

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
