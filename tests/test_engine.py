import csv
import importlib.util
import json
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "CSV_Consolidator.py"
SPEC = importlib.util.spec_from_file_location("csv_consolidator", MODULE_PATH)
csv_consolidator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(csv_consolidator)

CSVEngine = csv_consolidator.CSVEngine
ProcessingConfig = csv_consolidator.ProcessingConfig
ConfigHistory = csv_consolidator.ConfigHistory
PreviewPanel = csv_consolidator.PreviewPanel
create_upload_server = csv_consolidator.create_upload_server
UploadRequestHandler = csv_consolidator.UploadRequestHandler


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

    def test_malformed_csv_fails_without_replacing_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_text("id,name\n1,A\n2\n", encoding="utf-8")
            output_path.write_text("previous\nvalue\n", encoding="utf-8")

            stats = CSVEngine(ProcessingConfig(dedupe_enabled=False)).process(
                [input_path], output_path
            )

            self.assertTrue(stats.fatal_input_errors)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "previous\nvalue\n")

    def test_malformed_csv_warn_policy_keeps_repaired_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_text("id,name\n1,A\n2\n", encoding="utf-8")
            config = ProcessingConfig(dedupe_enabled=False, invalid_row_policy="warn")

            stats = CSVEngine(config).process([input_path], output_path)

            self.assertEqual(stats.errors, [])
            self.assertEqual(len(stats.warnings), 1)
            self.assertEqual(output_path.read_text(encoding="utf-8").splitlines(), [
                "id,name", "1,A", "2,",
            ])

    def test_malformed_jsonl_quarantine_writes_location_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.jsonl"
            output_path = Path(temp_dir) / "output.jsonl"
            quarantine_path = Path(temp_dir) / "quarantine.jsonl"
            input_path.write_text(
                '{"id": 1}\nnot-json\n{"id": 2}\n',
                encoding="utf-8",
            )
            config = ProcessingConfig(
                dedupe_enabled=False,
                invalid_row_policy="quarantine",
                quarantine_path=str(quarantine_path),
            )

            stats = CSVEngine(config).process([input_path], output_path)

            self.assertEqual(stats.errors, [])
            self.assertEqual(stats.quarantined_rows, 1)
            self.assertEqual(len(output_path.read_text(encoding="utf-8").splitlines()), 2)
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(quarantine["line"], 2)
            self.assertEqual(quarantine["file"], str(input_path))

    def test_input_limits_fail_before_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_text("id\n1\n2\n", encoding="utf-8")
            config = ProcessingConfig(dedupe_enabled=False, max_input_rows=1)

            stats = CSVEngine(config).process([input_path], output_path)

            self.assertTrue(any("row count" in error for error in stats.fatal_input_errors))
            self.assertFalse(output_path.exists())

    def test_invalid_xlsx_container_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.xlsx"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_bytes(b"not-an-xlsx-container")

            stats = CSVEngine(ProcessingConfig(dedupe_enabled=False)).process(
                [input_path], output_path
            )

            self.assertTrue(any("workbook container" in error for error in stats.fatal_input_errors))
            self.assertFalse(output_path.exists())

    def test_invalid_parquet_container_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.parquet"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_bytes(b"not-a-parquet-container")

            stats = CSVEngine(ProcessingConfig(dedupe_enabled=False)).process(
                [input_path], output_path
            )

            self.assertTrue(stats.fatal_input_errors)
            self.assertFalse(output_path.exists())

    def test_output_backup_and_manifest_are_auditable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_text("id,name\n1,A\n", encoding="utf-8")
            output_path.write_text("old\n", encoding="utf-8")
            config = ProcessingConfig(
                dedupe_enabled=False,
                output_collision_policy="backup",
                streaming_enabled=False,
            )

            stats = CSVEngine(config).process([input_path], output_path)

            self.assertFalse(stats.errors)
            self.assertEqual(output_path.read_text(encoding="utf-8").splitlines(), ["id,name", "1,A"])
            backups = list(Path(temp_dir).glob("output.csv.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "old\n")
            manifest = json.loads(
                Path(f"{output_path}.manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["stats"]["rows_written"], 1)
            self.assertEqual(manifest["output"]["sha256"], CSVEngine._sha256_file(output_path))
            self.assertEqual(manifest["inputs"][0]["sha256"], CSVEngine._sha256_file(input_path))

    def test_output_fail_collision_leaves_destination_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            output_path = Path(temp_dir) / "output.csv"
            input_path.write_text("id\n1\n", encoding="utf-8")
            output_path.write_text("previous\n", encoding="utf-8")
            config = ProcessingConfig(
                dedupe_enabled=False,
                output_collision_policy="fail",
            )

            stats = CSVEngine(config).process([input_path], output_path)

            self.assertTrue(any("already exists" in error for error in stats.errors))
            self.assertEqual(output_path.read_text(encoding="utf-8"), "previous\n")

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

    def test_duckdb_sql_query_can_filter_loaded_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.csv"
            input_path.write_text("id,name\n1,A\n2,B\n", encoding="utf-8")

            rows, columns = CSVEngine(ProcessingConfig()).sql_query(
                [input_path], "SELECT id, name FROM input_0 WHERE id > 1"
            )

            self.assertEqual(columns, ["id", "name"])
            self.assertEqual(rows, [{"id": "2", "name": "B"}])

    def test_loopback_upload_endpoint_runs_the_engine(self):
        server = create_upload_server(ProcessingConfig(dedupe_enabled=False), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            request = Request(
                f"http://127.0.0.1:{port}/process?filename=input.csv",
                data=b"id,name\n1,A\n",
                method="POST",
                headers={
                    "Content-Type": "text/csv",
                    "Content-Length": "12",
                    "X-CSV-Power-Token": server.auth_token,
                },
            )
            with urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["X-CSV-Power-Rows"], "1")
            self.assertEqual(body.splitlines(), ["id,name", "1,A"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_upload_rejects_missing_token(self):
        server = create_upload_server(ProcessingConfig(dedupe_enabled=False), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            request = Request(
                f"http://127.0.0.1:{port}/process?filename=input.csv",
                data=b"id\n1\n",
                method="POST",
                headers={"Content-Type": "text/csv", "Content-Length": "5"},
            )
            with self.assertRaises(Exception) as raised:
                urlopen(request, timeout=10)
            response = raised.exception
            self.assertEqual(response.code, 401)
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["error"]["code"], "unauthorized")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_upload_rejects_non_loopback_origin(self):
        server = create_upload_server(ProcessingConfig(dedupe_enabled=False), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            request = Request(
                f"http://127.0.0.1:{port}/process?filename=input.csv",
                data=b"id\n1\n",
                method="POST",
                headers={
                    "Content-Type": "text/csv",
                    "Content-Length": "5",
                    "X-CSV-Power-Token": server.auth_token,
                    "Origin": "https://example.invalid",
                },
            )
            with self.assertRaises(Exception) as raised:
                urlopen(request, timeout=10)
            response = raised.exception
            self.assertEqual(response.code, 403)
            payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["error"]["code"], "invalid_origin")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_upload_accepts_multipart_files(self):
        server = create_upload_server(ProcessingConfig(dedupe_enabled=False), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            boundary = "csv-power-test-boundary"
            body = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="file"; filename="input.csv"\r\n'
                "Content-Type: text/csv\r\n\r\n"
                "id,name\r\n1,A\r\n"
                f"--{boundary}--\r\n"
            ).encode("utf-8")
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/process",
                data=body,
                method="POST",
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "X-CSV-Power-Token": server.auth_token,
                },
            )
            with urlopen(request, timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("1,A", response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_upload_rejects_oversized_body_before_parsing(self):
        server = create_upload_server(ProcessingConfig(dedupe_enabled=False), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/process?filename=input.csv",
                data=b"x",
                method="POST",
                headers={
                    "Content-Type": "text/csv",
                    "Content-Length": str(UploadRequestHandler.MAX_UPLOAD_BYTES + 1),
                    "X-CSV-Power-Token": server.auth_token,
                },
            )
            with self.assertRaises(Exception) as raised:
                urlopen(request, timeout=10)
            self.assertEqual(raised.exception.code, 413)
            payload = json.loads(raised.exception.read().decode("utf-8"))
            self.assertEqual(payload["error"]["code"], "request_too_large")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_loopback_upload_enforces_active_request_limit(self):
        server = create_upload_server(ProcessingConfig(dedupe_enabled=False), port=0)
        server.request_slots = threading.BoundedSemaphore(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/process?filename=input.csv",
                data=b"",
                method="POST",
                headers={
                    "Content-Type": "text/csv",
                    "X-CSV-Power-Token": server.auth_token,
                },
            )
            with self.assertRaises(Exception) as raised:
                urlopen(request, timeout=10)
            self.assertEqual(raised.exception.code, 429)
            payload = json.loads(raised.exception.read().decode("utf-8"))
            self.assertEqual(payload["error"]["code"], "busy")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

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

    def test_schema_aware_join_reports_cardinality_conflicts_and_policies(self):
        left = [
            {"id": " A ", "value": "left-1"},
            {"id": "a", "value": "left-2"},
            {"id": "", "value": "missing"},
        ]
        right = [
            {"id": "a", "value": "right"},
            {"id": "b", "value": "right-b"},
        ]

        report = CSVEngine.analyze_join(left, right, ["id"], "outer")

        self.assertEqual(report["cardinality"], "one-to-many")
        self.assertEqual(report["unmatched_left_rows"], 1)
        self.assertEqual(report["unmatched_right_rows"], 1)
        self.assertEqual(report["conflict_count"], 2)
        self.assertEqual(report["sources"]["left"]["rows_missing_key"], 1)
        self.assertEqual(report["coercions"]["normalization_count"], 1)

        semi, semi_columns = CSVEngine.join_rows(left, right, ["id"], "semi")
        anti, anti_columns = CSVEngine.join_rows(left, right, ["id"], "anti")
        self.assertEqual(semi_columns, ["id", "value"])
        self.assertEqual([row["value"] for row in semi], ["left-1", "left-2"])
        self.assertEqual(anti_columns, ["id", "value"])
        self.assertEqual([row["value"] for row in anti], ["missing"])

        with self.assertRaisesRegex(ValueError, "missing key column"):
            CSVEngine.join_rows([{"id": "1"}], [{"other": "1"}], ["id"])

    def test_three_way_merge_requires_resolution_and_reports_conflict_values(self):
        base = [{"id": "1", "value": "base"}]
        ours = [{"id": "1", "value": "ours"}]
        theirs = [{"id": "1", "value": "theirs"}]

        report = CSVEngine.analyze_three_way(base, ours, theirs, ["id"])
        self.assertTrue(report["requires_explicit_resolution"])
        self.assertEqual(report["conflicts"][0]["base"], "base")
        self.assertEqual(report["conflicts"][0]["ours"], "ours")
        self.assertEqual(report["conflicts"][0]["theirs"], "theirs")

        with self.assertRaisesRegex(ValueError, "require explicit resolution"):
            CSVEngine.three_way_merge_rows(base, ours, theirs, ["id"], "fail")

        merged, conflicts, columns = CSVEngine.three_way_merge_rows(
            base, ours, theirs, ["id"], "theirs"
        )
        self.assertEqual(columns, ["id", "value"])
        self.assertEqual(merged[0]["value"], "theirs")
        self.assertEqual(conflicts, [{"key": ["1"], "columns": ["value"]}])

    def test_three_way_duplicate_keys_are_rejected_before_merge(self):
        with self.assertRaisesRegex(ValueError, "duplicate merge keys"):
            CSVEngine.three_way_merge_rows(
                [{"id": "1", "value": "base"}],
                [{"id": "1", "value": "ours"}, {"id": "1", "value": "again"}],
                [{"id": "1", "value": "theirs"}],
                ["id"],
                "ours",
            )


if __name__ == "__main__":
    unittest.main()
