import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "CSV_Consolidator.py"
SPEC = importlib.util.spec_from_file_location("csv_consolidator", MODULE_PATH)
csv_consolidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(csv_consolidator)

CSVEngine = csv_consolidator.CSVEngine
ProcessingConfig = csv_consolidator.ProcessingConfig


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


if __name__ == "__main__":
    unittest.main()
