import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from csv_power_tool.schema import (
    SchemaError,
    infer_schema,
    load_schema,
    normalize_column_mapping,
    normalize_schema,
    parse_column_mapping_assignments,
    validate_column_mapping,
    validate_rows,
    validation_report,
    write_schema,
)


MODULE_PATH = Path(__file__).resolve().parents[1] / "CSV_Consolidator.py"
SPEC = importlib.util.spec_from_file_location("csv_consolidator_schema", MODULE_PATH)
csv_consolidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(csv_consolidator)

CSVEngine = csv_consolidator.CSVEngine
ProcessingConfig = csv_consolidator.ProcessingConfig


CONTRACT = {
    "$schema": "https://specs.frictionlessdata.io/table-schema/",
    "fields": [
        {
            "name": "id",
            "type": "integer",
            "constraints": {"required": True, "unique": True},
        },
        {"name": "name", "type": "string", "constraints": {"required": True}},
    ],
    "primaryKey": ["id"],
}


class SchemaContractTests(unittest.TestCase):
    def test_column_mapping_is_normalized_and_rejects_collisions(self):
        self.assertEqual(
            normalize_column_mapping({" id ": " record_id "}),
            {"id": "record_id"},
        )
        self.assertEqual(
            parse_column_mapping_assignments(["id=record_id", "name=display_name"]),
            {"id": "record_id", "name": "display_name"},
        )
        with self.assertRaisesRegex(SchemaError, "target"):
            normalize_column_mapping({"id": "value", "name": "value"})
        with self.assertRaisesRegex(SchemaError, "unknown"):
            validate_column_mapping({"missing": "value"}, ["id"])

    def test_engine_mapping_is_user_visible_and_collision_safe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            output_path = root / "output.csv"
            manifest_path = root / "manifest.json"
            schema_path = root / "schema.json"
            input_path.write_text("id,name\n001,A\n", encoding="utf-8")

            config = ProcessingConfig(
                dedupe_enabled=False,
                column_mapping={"id": "record_id"},
                run_manifest_path=str(manifest_path),
            )
            stats = CSVEngine(config).process([input_path], output_path)

            self.assertFalse(stats.errors)
            self.assertEqual(output_path.read_text(encoding="utf-8").splitlines(), [
                "record_id,name",
                "001,A",
            ])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"]["column_mapping"], {"id": "record_id"})

            report = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, column_mapping={"id": "record_id"})
            ).write_schema_report([input_path], schema_path)
            self.assertEqual(report["column_mapping"], {"id": "record_id"})
            self.assertEqual(report["output_columns"], ["record_id", "name"])

            collision_stats = CSVEngine(
                ProcessingConfig(
                    dedupe_enabled=False,
                    column_mapping={"id": "name"},
                )
            ).process([input_path], root / "collision.csv")
            self.assertTrue(any("collide" in error for error in collision_stats.errors))
            self.assertFalse((root / "collision.csv").exists())

    def test_cli_rename_option_writes_mapped_header(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            output_path = root / "output.csv"
            input_path.write_text("id,name\n001,A\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--inputs", str(input_path),
                    "--rename", "id=record_id",
                    "--no-dedupe",
                    "--no-manifest",
                    "--output", str(output_path),
                ],
                cwd=MODULE_PATH.parent,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output_path.read_text(encoding="utf-8").splitlines()[0], "record_id,name")

    def test_schema_round_trip_and_unsupported_features(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "contract.json"
            write_schema(path, CONTRACT)
            self.assertEqual(load_schema(path), normalize_schema(CONTRACT))

        with self.assertRaisesRegex(SchemaError, "Unsupported Table Schema feature"):
            normalize_schema({**CONTRACT, "foreignKeys": []})

    def test_inferred_schema_exports_common_scalar_types(self):
        schema = infer_schema(
            [
                {"id": "1", "amount": "1.5", "active": "true"},
                {"id": "2", "amount": "2.5", "active": "false"},
            ],
            ["id", "amount", "active"],
        )

        fields = {field["name"]: field for field in schema["fields"]}
        self.assertEqual(fields["id"]["type"], "integer")
        self.assertEqual(fields["amount"]["type"], "number")
        self.assertEqual(fields["active"]["type"], "boolean")
        self.assertTrue(fields["id"]["constraints"]["unique"])

    def test_validation_report_has_row_column_rule_and_observed_context(self):
        rows = [
            {"id": "1", "name": "A"},
            {"id": "bad", "name": ""},
            {"id": "1", "name": "B"},
        ]

        valid_rows, report = validate_rows(rows, CONTRACT, "input.csv")
        public = validation_report([report], "strict", CONTRACT)

        self.assertEqual(valid_rows, [{"id": "1", "name": "A"}])
        self.assertEqual(report["invalid_row_count"], 2)
        self.assertEqual(public["error_count"], 4)
        self.assertFalse(any("_invalid_indexes" in item for item in public["files"]))
        self.assertTrue(
            all(set(error) == {"file", "row", "column", "cell", "rule", "observed_value"}
                for error in report["errors"])
        )

    def test_engine_modes_report_and_control_invalid_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            input_path.write_text("id,name\n1,A\nbad,\n1,B\n", encoding="utf-8")

            strict_output = root / "strict.csv"
            strict_output.write_text("previous\n", encoding="utf-8")
            strict_report = root / "strict-report.json"
            strict_stats = CSVEngine(
                ProcessingConfig(
                    dedupe_enabled=False,
                    run_manifest_enabled=False,
                    schema_contract=CONTRACT,
                    schema_validation_mode="strict",
                    schema_validation_report_path=str(strict_report),
                )
            ).process([input_path], strict_output)
            self.assertTrue(strict_stats.errors)
            self.assertEqual(strict_output.read_text(encoding="utf-8"), "previous\n")
            self.assertEqual(json.loads(strict_report.read_text())["error_count"], 4)

            advisory_output = root / "advisory.csv"
            advisory_report = root / "advisory-report.json"
            advisory_stats = CSVEngine(
                ProcessingConfig(
                    dedupe_enabled=False,
                    run_manifest_enabled=False,
                    schema_contract=CONTRACT,
                    schema_validation_mode="advisory",
                    schema_validation_report_path=str(advisory_report),
                )
            ).process([input_path], advisory_output)
            self.assertFalse(advisory_stats.errors)
            self.assertGreater(len(advisory_stats.warnings), 0)
            self.assertEqual(advisory_stats.final_row_count, 3)
            self.assertEqual(json.loads(advisory_report.read_text())["mode"], "advisory")

            quarantine_output = root / "quarantine.csv"
            quarantine_path = root / "rejected.jsonl"
            quarantine_stats = CSVEngine(
                ProcessingConfig(
                    dedupe_enabled=False,
                    run_manifest_enabled=False,
                    schema_contract=CONTRACT,
                    schema_validation_mode="quarantine",
                    quarantine_path=str(quarantine_path),
                )
            ).process([input_path], quarantine_output)
            self.assertFalse(quarantine_stats.errors)
            self.assertEqual(quarantine_stats.quarantined_rows, 2)
            self.assertEqual(quarantine_stats.final_row_count, 1)
            self.assertEqual(len(quarantine_path.read_text(encoding="utf-8").splitlines()), 2)
            with quarantine_output.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [{"id": "1", "name": "A"}])

    def test_cli_validation_only_returns_contract_failure_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            contract_path = root / "contract.json"
            input_path.write_text("id,name\ninvalid,A\n", encoding="utf-8")
            write_schema(contract_path, CONTRACT)

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--inputs",
                    str(input_path),
                    "--schema-contract",
                    str(contract_path),
                    "--validate-only",
                    "--no-manifest",
                ],
                cwd=MODULE_PATH.parent,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("Schema validation:", result.stderr)

    def test_cli_export_schema_can_run_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            schema_path = root / "contract.json"
            input_path.write_text("id,active\n1,true\n2,false\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--inputs",
                    str(input_path),
                    "--export-schema",
                    str(schema_path),
                    "--no-manifest",
                ],
                cwd=MODULE_PATH.parent,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            exported = load_schema(schema_path)
            fields = {field["name"]: field for field in exported["fields"]}
            self.assertEqual(fields["id"]["type"], "integer")
            self.assertEqual(fields["active"]["type"], "boolean")

    @unittest.skipUnless(
        os.environ.get("CSV_POWER_TOOL_EXE"),
        "set CSV_POWER_TOOL_EXE to run the clean packaged schema smoke test",
    )
    def test_clean_packaged_schema_quarantine_smoke(self):
        executable = Path(os.environ["CSV_POWER_TOOL_EXE"])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            contract_path = root / "contract.json"
            output_path = root / "output.csv"
            quarantine_path = root / "rejected.jsonl"
            report_path = root / "validation.json"
            input_path.write_text("id,name\n1,A\nbad,\n1,B\n", encoding="utf-8")
            write_schema(contract_path, CONTRACT)

            result = subprocess.run(
                [
                    str(executable),
                    "--inputs",
                    str(input_path),
                    "--schema-contract",
                    str(contract_path),
                    "--validation-mode",
                    "quarantine",
                    "--quarantine",
                    str(quarantine_path),
                    "--validation-report",
                    str(report_path),
                    "--output",
                    str(output_path),
                    "--no-dedupe",
                    "--no-manifest",
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            with output_path.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [{"id": "1", "name": "A"}])
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["mode"], "quarantine")
            self.assertEqual(report["error_count"], 4)
            self.assertEqual(len(quarantine_path.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
