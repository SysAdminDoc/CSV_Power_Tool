import json
import tempfile
import unittest
from pathlib import Path

from csv_power_tool.workflow import (
    WorkflowError,
    append_history,
    build_workflow,
    canonical_json,
    extract_config,
    load_workflow,
    normalize_workflow,
    operation_types,
    write_workflow,
)


class WorkflowDocumentTests(unittest.TestCase):
    def test_workflow_serialization_and_hash_are_deterministic(self):
        config = {
            "dedupe_enabled": False,
            "filters": [["id", "contains", "1"]],
            "output_collision_policy": "backup",
        }

        first = build_workflow(config, ["input.csv"], "output.csv", "3.2.0")
        second = build_workflow(config, ["input.csv"], "output.csv", "3.2.0")

        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["workflow_sha256"], second["workflow_sha256"])
        self.assertEqual(operation_types(first)[0], "input-selection")

    def test_legacy_config_migrates_and_round_trips(self):
        legacy = {"dedupe_enabled": False, "output_delimiter": ";"}

        document = normalize_workflow(legacy, "3.2.0")

        self.assertEqual(document["metadata"]["migrated_from"], "legacy-config")
        self.assertEqual(extract_config(document), legacy)

    def test_tampered_workflow_is_rejected(self):
        document = build_workflow({"dedupe_enabled": False}, ["input.csv"], "output.csv", "3.2.0")
        document["config"]["dedupe_enabled"] = True

        with self.assertRaisesRegex(WorkflowError, "identity hash"):
            normalize_workflow(document)

    def test_history_is_bounded_and_records_changed_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "history.json"
            first = build_workflow({"dedupe_enabled": False}, ["one.csv"], "out.csv", "3.2.0")
            second = build_workflow({"dedupe_enabled": True}, ["one.csv"], "out.csv", "3.2.0")
            third = build_workflow({"dedupe_enabled": False}, ["two.csv"], "out.csv", "3.2.0")

            append_history(history_path, first, limit=2)
            append_history(history_path, second, limit=2)
            record = append_history(history_path, third, limit=2)

            stored = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(len(stored["records"]), 2)
            self.assertIn("config", record["changed_fields"])
            self.assertIn("inputs", record["changed_fields"])
            self.assertEqual(len(list(Path(temp_dir).glob(".*.tmp"))), 0)

    def test_corrupt_history_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "history.json"
            history_path.write_text("not-json", encoding="utf-8")
            document = build_workflow({"dedupe_enabled": False})

            with self.assertRaises(WorkflowError):
                append_history(history_path, document)
            self.assertEqual(history_path.read_text(encoding="utf-8"), "not-json")

    def test_workflow_file_loads_through_atomic_writer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workflow_path = Path(temp_dir) / "workflow.json"
            document = build_workflow({"dedupe_enabled": False}, ["input.csv"], "out.csv", "3.2.0")

            write_workflow(workflow_path, document)
            loaded = load_workflow(workflow_path)

            self.assertEqual(loaded, document)
            self.assertEqual(list(Path(temp_dir).glob(".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
