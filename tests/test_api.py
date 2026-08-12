import json
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from csv_power_tool.api import API_FORMAT, API_VERSION, create_upload_server
from csv_power_tool.api import UploadRequestHandler


class _FakeStats:
    final_row_count = 1
    files_processed = 1
    errors = []


class _FakeEngine:
    seen_paths = []

    def process(self, paths, output_path):
        self.seen_paths.extend(Path(path) for path in paths)
        Path(output_path).write_text("id,name\n1,A\n", encoding="utf-8")
        return _FakeStats()


class LoopbackApiContractTests(unittest.TestCase):
    def setUp(self):
        _FakeEngine.seen_paths = []
        self.server = create_upload_server(
            config={"dedupe_enabled": False},
            port=0,
            auth_token="test-token",
            engine_factory=lambda _config: _FakeEngine(),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _request(self, path, *, method="GET", body=None, headers=None):
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers=headers or {},
        )
        return urlopen(request, timeout=10)

    def _error(self, path, *, method="GET", body=None, headers=None):
        with self.assertRaises(HTTPError) as raised:
            self._request(path, method=method, body=body, headers=headers)
        response = raised.exception
        return response, json.loads(response.read().decode("utf-8"))

    def test_contract_and_health_publish_versioned_limits(self):
        headers = {"X-CSV-Power-Token": self.server.auth_token}
        with self._request("/health", headers=headers) as response:
            health = json.loads(response.read().decode("utf-8"))
            self.assertEqual(health["format"], API_FORMAT)
            self.assertEqual(health["api_version"], API_VERSION)
            self.assertEqual(response.headers["X-CSV-Power-API-Version"], str(API_VERSION))
            self.assertTrue(response.headers["X-CSV-Power-Request-Id"])

        with self._request("/contract", headers=headers) as response:
            contract = json.loads(response.read().decode("utf-8"))
            self.assertEqual(contract["format"], API_FORMAT)
            self.assertEqual(contract["version"], API_VERSION)
            self.assertEqual(contract["limits"]["concurrent_requests"], 4)
            self.assertEqual(contract["limits"]["request_bytes"], UploadRequestHandler.MAX_UPLOAD_BYTES)
            self.assertIn("multipart/form-data", contract["endpoints"]["process"]["request"])
            self.assertIn("/process", contract["endpoints"]["process"]["path"])

    def test_raw_process_has_correlation_and_cleans_staged_input(self):
        headers = {
            "Content-Type": "text/csv",
            "X-CSV-Power-Token": self.server.auth_token,
        }
        with self._request("/process?filename=input.csv", method="POST", body=b"id,name\n1,A\n", headers=headers) as response:
            output = response.read().decode("utf-8")
            request_id = response.headers["X-CSV-Power-Request-Id"]
            run_id = response.headers["X-CSV-Power-Run-Id"]
            self.assertEqual(response.headers["X-CSV-Power-API-Version"], str(API_VERSION))
            self.assertEqual(response.headers["X-CSV-Power-Rows"], "1")
            self.assertTrue(request_id)
            self.assertTrue(run_id)
        self.assertEqual(output.splitlines(), ["id,name", "1,A"])
        self.assertEqual(len(_FakeEngine.seen_paths), 1)
        self.assertFalse(_FakeEngine.seen_paths[0].exists())

    def test_multipart_process_is_supported_by_the_same_contract(self):
        boundary = "csv-power-api-test"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="input.csv"\r\n'
            "Content-Type: text/csv\r\n\r\n"
            "id,name\r\n1,A\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-CSV-Power-Token": self.server.auth_token,
        }
        with self._request("/process", method="POST", body=body, headers=headers) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("1,A", response.read().decode("utf-8"))

    def test_errors_are_stable_and_correlated(self):
        response, payload = self._error(
            "/process?filename=input.csv",
            method="POST",
            body=b"id\n1\n",
            headers={"Content-Type": "text/csv"},
        )
        self.assertEqual(response.status, 401)
        self.assertEqual(payload["error"]["code"], "unauthorized")
        self.assertTrue(payload["request_id"])
        self.assertTrue(payload["run_id"])
        self.assertEqual(response.headers["X-CSV-Power-API-Version"], str(API_VERSION))
        self.assertEqual(response.headers["X-CSV-Power-Request-Id"], payload["request_id"])

    def test_invalid_type_oversize_and_busy_requests_are_rejected(self):
        auth = {"X-CSV-Power-Token": self.server.auth_token, "Content-Type": "text/csv"}
        response, payload = self._error(
            "/process?filename=input.exe", method="POST", body=b"x", headers=auth
        )
        self.assertEqual(response.status, 415)
        self.assertEqual(payload["error"]["code"], "unsupported_type")

        response, payload = self._error(
            "/process?filename=input.csv",
            method="POST",
            body=b"x",
            headers={
                **auth,
                "Content-Length": str(UploadRequestHandler.MAX_UPLOAD_BYTES + 1),
            },
        )
        self.assertEqual(response.status, 413)
        self.assertEqual(payload["error"]["code"], "request_too_large")

        self.server.request_slots = threading.BoundedSemaphore(0)
        response, payload = self._error(
            "/process?filename=input.csv", method="POST", body=b"", headers=auth
        )
        self.assertEqual(response.status, 429)
        self.assertEqual(payload["error"]["code"], "busy")
        self.assertTrue(payload["run_id"])


if __name__ == "__main__":
    unittest.main()
