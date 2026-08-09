import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from csv_power_tool.quality import (
    QualityError,
    QualityProfiler,
    apply_repairs,
    profile_rows,
)
from csv_power_tool.workflow import build_workflow, operation_types


MODULE_PATH = Path(__file__).resolve().parents[1] / "CSV_Consolidator.py"
SPEC = importlib.util.spec_from_file_location("csv_consolidator_quality", MODULE_PATH)
csv_consolidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(csv_consolidator)

CSVEngine = csv_consolidator.CSVEngine
ProcessingConfig = csv_consolidator.ProcessingConfig
QualityPanel = csv_consolidator.QualityPanel


class QualityModelTests(unittest.TestCase):
    def test_profile_reports_facets_types_nulls_and_numeric_summary(self):
        report = profile_rows(
            [
                {"id": "001", "status": "ok", "amount": "2"},
                {"id": "002", "status": "ok", "amount": "3.5"},
                {"id": "001", "status": "", "amount": "bad"},
                {"id": "003", "status": "NULL", "amount": "4"},
            ],
            facet_limit=2,
        )

        columns = {column["name"]: column for column in report["columns"]}
        self.assertEqual(report["rows_scanned"], 4)
        self.assertEqual(columns["id"]["inferred_type"], "integer")
        self.assertEqual(columns["id"]["unique_count"], 3)
        self.assertEqual(columns["id"]["duplicate_count"], 1)
        self.assertEqual(columns["status"]["blank_count"], 1)
        self.assertEqual(columns["status"]["null_count"], 1)
        self.assertEqual(columns["status"]["facets"][0], {"value": "ok", "count": 2})
        self.assertEqual(columns["amount"]["numeric"]["max"], 4.0)

    def test_profiler_caps_distinct_storage_and_marks_approximation(self):
        profiler = QualityProfiler(facet_limit=2, max_distinct_values=2)
        for index in range(10):
            profiler.add_row({"value": str(index)})

        column = profiler.report()["columns"][0]
        self.assertFalse(column["unique_count_exact"])
        self.assertLessEqual(len(column["facets"]), 2)
        self.assertTrue(column["facets_truncated"])

    def test_repairs_are_explicit_atomic_and_keep_raw_text(self):
        original = [{"id": "001", "name": "Alice"}, {"id": "002", "name": "Bob"}]
        repaired, report = apply_repairs(
            original,
            [{
                "row": 1,
                "column": "name",
                "replacement": "Alicia",
                "expected_old": "Alice",
                "reason": "reviewed spelling correction",
            }],
        )

        self.assertEqual(original[0]["id"], "001")
        self.assertEqual(repaired[0]["name"], "Alicia")
        self.assertEqual(report["edit_count"], 1)
        self.assertEqual(report["edits"][0]["before"], "Alice")
        self.assertEqual(report["edits"][0]["value_type"], "text")
        with self.assertRaisesRegex(QualityError, "expected"):
            apply_repairs(
                original,
                [{"row": 1, "column": "name", "value": "Alicia", "expected_old": "wrong"}],
            )

    def test_profile_filter_inspection_and_gui_summary_contract(self):
        report = profile_rows(
            [
                {"status": "ok", "id": "001"},
                {"status": "hold", "id": "002"},
            ],
        )
        summary = QualityPanel.format_profile(report, "ok")
        self.assertIn("status", summary)
        self.assertNotIn("id |", summary)


class QualityEngineAndCliTests(unittest.TestCase):
    def test_engine_applies_repairs_before_output_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            output_path = root / "output.csv"
            repair_report = root / "repairs.json"
            input_path.write_text("id,name\n001,Alice\n002,Bob\n", encoding="utf-8")

            stats = CSVEngine(
                ProcessingConfig(
                    dedupe_enabled=False,
                    repair_edits=[
                        {
                            "row": 1,
                            "column": "name",
                            "value": "Alicia",
                            "expected_old": "Alice",
                            "reason": "reviewed correction",
                        }
                    ],
                    repair_report_path=str(repair_report),
                )
            ).process([input_path], output_path)

            self.assertFalse(stats.errors)
            with output_path.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.DictReader(handle))[0], {"id": "001", "name": "Alicia"})
            self.assertEqual(json.loads(repair_report.read_text())["edit_count"], 1)
            manifest = json.loads(Path(f"{output_path}.manifest.json").read_text())
            self.assertEqual(manifest["stats"]["repair_report"]["edit_count"], 1)

    def test_cli_profile_and_repair_contracts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            profile_path = root / "profile.json"
            edits_path = root / "edits.json"
            repair_report = root / "repair-report.json"
            output_path = root / "output.csv"
            input_path.write_text("id,name\n001,Alice\n002,Bob\n", encoding="utf-8")
            edits_path.write_text(
                json.dumps([{"row": 2, "column": "name", "value": "Robert", "expected_old": "Bob"}]),
                encoding="utf-8",
            )

            profile = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--inputs",
                    str(input_path),
                    "--profile",
                    str(profile_path),
                    "--quality-scan-rows",
                    "1",
                    "--no-manifest",
                ],
                cwd=MODULE_PATH.parent,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(profile.returncode, 0, profile.stderr)
            profile_payload = json.loads(profile_path.read_text())
            self.assertEqual(profile_payload["format"], "csv-power-tool-quality-profile")
            self.assertTrue(profile_payload["scan_truncated"])

            repaired = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--inputs",
                    str(input_path),
                    "--repair-edits",
                    str(edits_path),
                    "--repair-report",
                    str(repair_report),
                    "--output",
                    str(output_path),
                    "--no-dedupe",
                ],
                cwd=MODULE_PATH.parent,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertIn("Robert", output_path.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(repair_report.read_text())["edit_count"], 1)

    def test_profile_facet_filter_and_raw_row_inspection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            input_path.write_text("id,status\n001,ok\n002,hold\n", encoding="utf-8")
            engine = CSVEngine(ProcessingConfig())
            report = engine.profile(
                [input_path],
                filter_column="status",
                filter_value="ok",
            )
            self.assertEqual(report["rows_scanned"], 1)
            self.assertEqual(report["source_rows_scanned"], 2)
            self.assertEqual(report["facet_filter"], {"column": "status", "value": "ok"})

            input_path.write_text("id,name\n001, Alice \n", encoding="utf-8")
            inspection = engine.inspect_row([input_path], 1)
            self.assertTrue(inspection["raw_text_preserved"])
            self.assertEqual(inspection["values"][1]["raw"], " Alice ")

    def test_workflow_records_repair_operation_and_manifest_stats_shape(self):
        edits = [{"row": 1, "column": "name", "value": "Alicia"}]
        workflow = build_workflow(
            {"repair_edits": edits, "repair_report_path": "repairs.json"},
            ["input.csv"],
            "output.csv",
        )
        self.assertIn("quality-repair", operation_types(workflow))
        self.assertEqual(workflow["config"]["repair_edits"], edits)

    @unittest.skipUnless(os.environ.get("CSV_POWER_TOOL_EXE"), "clean packaged executable not supplied")
    def test_clean_packaged_quality_smoke(self):
        executable = Path(os.environ["CSV_POWER_TOOL_EXE"])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            profile_path = root / "profile.json"
            edits_path = root / "edits.json"
            output_path = root / "output.csv"
            input_path.write_text("id,name\n001,Alice\n", encoding="utf-8")
            edits_path.write_text(
                json.dumps([{"row": 1, "column": "name", "value": "Alicia", "expected_old": "Alice"}]),
                encoding="utf-8",
            )
            profile = subprocess.run(
                [str(executable), "--inputs", str(input_path), "--profile", str(profile_path), "--no-manifest"],
                cwd=executable.parent,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(profile.returncode, 0, profile.stderr)
            self.assertEqual(json.loads(profile_path.read_text())["format"], "csv-power-tool-quality-profile")
            repaired = subprocess.run(
                [
                    str(executable), "--inputs", str(input_path), "--repair-edits", str(edits_path),
                    "--output", str(output_path), "--no-dedupe", "--no-manifest",
                ],
                cwd=executable.parent,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertIn("Alicia", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
