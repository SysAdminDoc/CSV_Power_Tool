import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq


MODULE_PATH = Path(__file__).resolve().parents[1] / "CSV_Consolidator.py"
SPEC = importlib.util.spec_from_file_location("csv_consolidator_bounded", MODULE_PATH)
csv_consolidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(csv_consolidator)

CSVEngine = csv_consolidator.CSVEngine
PreviewBudget = csv_consolidator.PreviewBudget
ProcessingConfig = csv_consolidator.ProcessingConfig


class BoundedProcessingTests(unittest.TestCase):
    def test_preview_stops_at_row_and_sample_byte_budgets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "large.csv"
            with input_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["id", "value"])
                writer.writerows((index, f"value-{index}") for index in range(10_000))

            result = CSVEngine(ProcessingConfig(dedupe_enabled=False)).preview(
                [input_path],
                limit=5,
                budget=PreviewBudget(
                    row_limit=5,
                    scan_row_limit=20,
                    scan_byte_limit=512,
                    column_limit=8,
                    cell_byte_limit=64,
                ),
            )

            self.assertEqual(len(result["rows"]), 5)
            self.assertTrue(result["bounded"])
            self.assertEqual(result["mode"], "read-only")
            self.assertEqual(result["metadata"]["rows_scanned"], 20)
            self.assertTrue(result["metadata"]["scan_truncated"])
            self.assertEqual(result["metadata"]["remaining_rows"], None)
            self.assertLess(result["metadata"]["bytes_scanned"], 512)

    def test_preview_cancellation_is_observable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "large.jsonl"
            input_path.write_text(
                "".join(f'{{"id": {index}, "value": "v-{index}"}}\n' for index in range(2_000)),
                encoding="utf-8",
            )
            engine = None

            def cancel_after_progress(_value, status):
                if "Preview scanning" in status:
                    engine.cancel()

            engine = CSVEngine(
                ProcessingConfig(dedupe_enabled=False),
                progress_callback=cancel_after_progress,
            )
            result = engine.preview(
                [input_path],
                budget=PreviewBudget(row_limit=100, scan_row_limit=1_000),
            )

            self.assertTrue(result["metadata"]["cancelled"])
            self.assertLess(result["metadata"]["rows_scanned"], 1_000)

    def test_cli_writes_bounded_preview_artifact_without_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            preview_path = root / "preview.json"
            input_path.write_text(
                "id,value\n" + "".join(f"{index},value-{index}\n" for index in range(100)),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--inputs",
                    str(input_path),
                    "--preview",
                    str(preview_path),
                    "--preview-rows",
                    "3",
                    "--preview-scan-rows",
                    "12",
                    "--no-manifest",
                ],
                cwd=MODULE_PATH.parent,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["format"], "csv-power-tool-preview")
            self.assertEqual(len(payload["rows"]), 3)
            self.assertEqual(payload["metadata"]["rows_scanned"], 12)
            self.assertTrue(payload["metadata"]["scan_truncated"])

    @unittest.skipUnless(
        os.environ.get("CSV_POWER_TOOL_EXE"),
        "set CSV_POWER_TOOL_EXE to run the clean packaged preview smoke test",
    )
    def test_clean_packaged_preview_smoke(self):
        executable = Path(os.environ["CSV_POWER_TOOL_EXE"])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            preview_path = root / "preview.json"
            input_path.write_text(
                "id,value\n" + "".join(f"{index},value-{index}\n" for index in range(100)),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    str(executable),
                    "--inputs",
                    str(input_path),
                    "--preview",
                    str(preview_path),
                    "--preview-rows",
                    "3",
                    "--preview-scan-rows",
                    "12",
                    "--no-manifest",
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(preview_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["format"], "csv-power-tool-preview")
            self.assertTrue(payload["metadata"]["scan_truncated"])

    def test_parquet_input_streams_in_batches_and_matches_materialized_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.parquet"
            streamed_output = root / "streamed.csv"
            materialized_output = root / "materialized.csv"
            pq.write_table(
                pa.table({
                    "id": list(range(3_000)),
                    "name": [f"name-{index}" for index in range(3_000)],
                }),
                input_path,
            )

            with patch.object(pq, "read_table", side_effect=AssertionError("whole-table read")):
                streamed_stats = CSVEngine(
                    ProcessingConfig(dedupe_enabled=False)
                ).process([input_path], streamed_output)

            materialized_stats = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, streaming_enabled=False)
            ).process([input_path], materialized_output)

            self.assertEqual(streamed_stats.final_row_count, 3_000)
            self.assertEqual(materialized_stats.final_row_count, 3_000)
            self.assertEqual(
                streamed_output.read_text(encoding="utf-8"),
                materialized_output.read_text(encoding="utf-8"),
            )
            self.assertEqual(streamed_stats.execution_mode, "streaming")
            self.assertEqual(materialized_stats.execution_mode, "materialized")

    def test_materialized_parquet_path_rejects_rows_above_explicit_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.parquet"
            output_path = root / "output.csv"
            pq.write_table(pa.table({"id": list(range(3))}), input_path)

            stats = CSVEngine(
                ProcessingConfig(
                    dedupe_enabled=False,
                    streaming_enabled=False,
                    max_materialized_rows=2,
                )
            ).process([input_path], output_path)

            self.assertTrue(any("materialization limit" in error for error in stats.errors))
            self.assertFalse(output_path.exists())

    def test_streaming_writer_supports_batched_parquet_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.csv"
            output_path = root / "output.parquet"
            input_path.write_text(
                "id,name\n" + "".join(f"{index},name-{index}\n" for index in range(2_500)),
                encoding="utf-8",
            )

            stats = CSVEngine(
                ProcessingConfig(dedupe_enabled=False, stream_batch_rows=128)
            ).process([input_path], output_path)

            self.assertEqual(stats.final_row_count, 2_500)
            table = pq.read_table(output_path)
            self.assertEqual(table.num_rows, 2_500)
            self.assertEqual(table.column("id")[0].as_py(), "0")
            self.assertEqual(table.column("name")[-1].as_py(), "name-2499")


if __name__ == "__main__":
    unittest.main()
