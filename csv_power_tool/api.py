"""Loopback upload API boundary.

The handler receives its engine factory and compatibility metadata from the
launcher. This keeps the HTTP transport testable with a fake engine and avoids
an API-to-GUI import cycle.
"""

from __future__ import annotations

import copy
import hmac
import json
import secrets
import socket
import sys
import tempfile
import threading
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


DEFAULT_APP_NAME = "CSV Power Tool"
DEFAULT_APP_VERSION = "unknown"
DEFAULT_INPUT_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".parquet", ".jsonl", ".ndjson"}
API_FORMAT = "csv-power-tool-loopback-api"
API_VERSION = 1


def _default_engine_factory(config):
    from CSV_Consolidator import CSVEngine

    return CSVEngine(config)


class UploadRequestHandler(BaseHTTPRequestHandler):
    """Loopback upload endpoint that runs an injected processing engine."""

    MAX_UPLOAD_BYTES = 50 * 1024 * 1024
    MAX_FILE_COUNT = 32
    MAX_FILE_BYTES = 50 * 1024 * 1024
    MAX_FORM_FIELD_BYTES = 64 * 1024
    MAX_FILENAME_BYTES = 512

    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.request_timeout)
        self.request_id = secrets.token_hex(16)
        self.run_id = None

    @staticmethod
    def _loopback_host(host: str | None) -> bool:
        if not host:
            return False
        try:
            parsed = urlparse(f"//{host}")
            return parsed.hostname in {"127.0.0.1", "localhost"}
        except ValueError:
            return False

    def _validate_origin(self):
        host = self.headers.get("Host", "")
        if not self._loopback_host(host):
            raise UploadRequestError(403, "invalid_host", "Host must identify the loopback server")

        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not self._loopback_host(parsed.netloc):
                raise UploadRequestError(403, "invalid_origin", "Origin must identify the loopback server")

    def _authorize(self):
        self._validate_origin()
        supplied = self.headers.get("X-CSV-Power-Token", "")
        if not supplied:
            authorization = self.headers.get("Authorization", "")
            if authorization.lower().startswith("bearer "):
                supplied = authorization[7:].strip()
        expected = self.server.auth_token
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise UploadRequestError(401, "unauthorized", "A valid CSV Power Tool upload token is required")

    def _request_error(self, error: "UploadRequestError"):
        payload = {
            "error": {"code": error.code, "message": str(error)},
            "request_id": self.request_id,
        }
        if self.run_id:
            payload["run_id"] = self.run_id
        self._send_json(error.status, payload)

    def _send_contract_headers(self):
        self.send_header("X-CSV-Power-API-Version", str(API_VERSION))
        self.send_header("X-CSV-Power-Request-Id", self.request_id)
        if self.run_id:
            self.send_header("X-CSV-Power-Run-Id", self.run_id)

    def _send_json(self, status: int, payload: dict):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self._send_contract_headers()
        self.end_headers()
        self.wfile.write(encoded)

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise UploadRequestError(411, "length_required", "Transfer-Encoding is not supported")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0:
            raise UploadRequestError(411, "length_required", "Content-Length is required")
        if length > self.MAX_UPLOAD_BYTES:
            raise UploadRequestError(413, "request_too_large", f"Request exceeds {self.MAX_UPLOAD_BYTES} bytes")
        body = self.rfile.read(length)
        if len(body) != length:
            raise UploadRequestError(400, "incomplete_request", "Request body ended before Content-Length")
        return body

    def _parse_files(self, body: bytes) -> list[tuple[str, bytes]]:
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            envelope = (
                f"Content-Type: {content_type}\r\n"
                "MIME-Version: 1.0\r\n\r\n"
            ).encode("utf-8") + body
            message = BytesParser(policy=email_default_policy).parsebytes(envelope)
            files = []
            part_count = 0
            for part in message.iter_parts():
                part_count += 1
                if part.get_content_disposition() != "form-data" or not part.get_filename():
                    payload = part.get_payload(decode=True) or b""
                    if len(payload) > self.MAX_FORM_FIELD_BYTES:
                        raise UploadRequestError(413, "field_too_large", "Multipart form field exceeds the size limit")
                    continue
                if part_count > self.MAX_FILE_COUNT:
                    raise UploadRequestError(413, "too_many_files", "Multipart file count exceeds the limit")
                filename = str(part.get_filename())
                if len(filename.encode("utf-8")) > self.MAX_FILENAME_BYTES:
                    raise UploadRequestError(413, "filename_too_long", "Multipart filename exceeds the size limit")
                payload = part.get_payload(decode=True) or b""
                if len(payload) > self.MAX_FILE_BYTES:
                    raise UploadRequestError(413, "file_too_large", "Multipart file exceeds the size limit")
                files.append((Path(filename).name, payload))
            return files

        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if "sql" in query or "query" in query:
            raise UploadRequestError(400, "unsupported_operation", "SQL is not available through the upload endpoint")
        filename = query.get("filename", ["upload.csv"])[0]
        if len(filename.encode("utf-8")) > self.MAX_FILENAME_BYTES:
            raise UploadRequestError(413, "filename_too_long", "Upload filename exceeds the size limit")
        return [(Path(filename).name, body)]

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            self._authorize()
            path = urlparse(self.path).path
            if path == "/health":
                self._send_json(
                    200,
                    {
                        "format": API_FORMAT,
                        "api_version": API_VERSION,
                        "status": "ok",
                        "service": self.server.app_name,
                        "version": self.server.app_version,
                    },
                )
                return
            if path == "/contract":
                self._send_json(200, build_api_contract(self.server))
                return
            raise UploadRequestError(404, "not_found", "Use GET /health or POST /process")
        except UploadRequestError as exc:
            self._request_error(exc)

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if urlparse(self.path).path != "/process":
            self._request_error(UploadRequestError(404, "not_found", "Use POST /process"))
            return
        self.run_id = secrets.token_hex(16)
        if not self.server.request_slots.acquire(blocking=False):
            self._request_error(UploadRequestError(429, "busy", "The upload server is at its concurrency limit"))
            return
        try:
            self._authorize()
            files = self._parse_files(self._read_body())
            if not files:
                raise UploadRequestError(400, "no_files", "No upload files were supplied")
            paths = []
            with tempfile.TemporaryDirectory(prefix="csv-power-upload-") as temp_dir:
                temp_root = Path(temp_dir)
                for index, (filename, payload) in enumerate(files):
                    suffix = Path(filename).suffix.lower()
                    if suffix not in self.server.supported_input_suffixes:
                        raise UploadRequestError(415, "unsupported_type", f"Unsupported upload type: {suffix or '(none)'}")
                    path = temp_root / f"upload_{index}{suffix}"
                    path.write_bytes(payload)
                    paths.append(path)

                output_path = temp_root / "output.csv"
                engine = self.server.engine_factory(copy.deepcopy(self.server.csv_config))
                stats = engine.process(paths, output_path)
                if stats.errors:
                    raise UploadRequestError(422, "processing_failed", "; ".join(stats.errors))
                output = output_path.read_bytes() if output_path.exists() else b""
                if len(output) > self.MAX_UPLOAD_BYTES:
                    raise UploadRequestError(413, "response_too_large", "Processed output exceeds the response size limit")

            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(output)))
            self._send_contract_headers()
            self.send_header("X-CSV-Power-Rows", str(stats.final_row_count))
            self.send_header("X-CSV-Power-Files", str(stats.files_processed))
            self.end_headers()
            self.wfile.write(output)
        except UploadRequestError as exc:
            self._request_error(exc)
        except socket.timeout:
            self._request_error(UploadRequestError(408, "request_timeout", "Upload request timed out"))
        except (OSError, RuntimeError, ValueError) as exc:
            self._request_error(UploadRequestError(400, "request_failed", str(exc)))
        finally:
            self.server.request_slots.release()

    def log_message(self, _format, *_args):
        return


class UploadHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class UploadRequestError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = int(status)
        self.code = code


def build_api_contract(server=None) -> dict:
    """Return the machine-readable contract shared by health and process clients."""

    app_name = getattr(server, "app_name", DEFAULT_APP_NAME)
    app_version = getattr(server, "app_version", DEFAULT_APP_VERSION)
    suffixes = getattr(server, "supported_input_suffixes", DEFAULT_INPUT_SUFFIXES)
    handler = UploadRequestHandler
    return {
        "format": API_FORMAT,
        "version": API_VERSION,
        "service": app_name,
        "tool_version": app_version,
        "authentication": {
            "required": True,
            "headers": ["X-CSV-Power-Token", "Authorization: Bearer <token>"],
            "host": "loopback only",
            "origin": "optional loopback origin only",
        },
        "endpoints": {
            "health": {
                "method": "GET",
                "path": "/health",
                "response": "application/json",
            },
            "contract": {
                "method": "GET",
                "path": "/contract",
                "response": "application/json",
            },
            "process": {
                "method": "POST",
                "path": "/process",
                "request": ["raw file body", "multipart/form-data"],
                "response": "text/csv",
                "sql": False,
            },
        },
        "limits": {
            "request_bytes": handler.MAX_UPLOAD_BYTES,
            "file_bytes": handler.MAX_FILE_BYTES,
            "file_count": handler.MAX_FILE_COUNT,
            "form_field_bytes": handler.MAX_FORM_FIELD_BYTES,
            "filename_bytes": handler.MAX_FILENAME_BYTES,
            "concurrent_requests": getattr(server, "max_concurrent_requests", 4),
            "request_timeout_seconds": getattr(server, "request_timeout", 30.0),
        },
        "input_suffixes": sorted(str(suffix) for suffix in suffixes),
        "response_headers": {
            "X-CSV-Power-API-Version": "contract version",
            "X-CSV-Power-Request-Id": "request correlation identifier",
            "X-CSV-Power-Run-Id": "processing run identifier for POST /process",
            "X-CSV-Power-Rows": "successful process row count",
            "X-CSV-Power-Files": "successful process file count",
        },
        "errors": {
            "content_type": "application/json; charset=utf-8",
            "shape": {
                "error": {"code": "stable machine-readable code", "message": "human-readable detail"},
                "request_id": "request correlation identifier",
                "run_id": "processing run identifier when allocated",
            },
        },
    }


def create_upload_server(
    config,
    host: str = "127.0.0.1",
    port: int = 0,
    auth_token: str | None = None,
    *,
    engine_factory=None,
    supported_input_suffixes=None,
    app_name: str = DEFAULT_APP_NAME,
    app_version: str = DEFAULT_APP_VERSION,
):
    """Create a loopback-only upload server without starting its serving thread."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The upload server only binds to localhost")
    server = UploadHTTPServer((host, int(port)), UploadRequestHandler)
    server.csv_config = copy.deepcopy(config)
    server.engine_factory = engine_factory or _default_engine_factory
    server.supported_input_suffixes = set(supported_input_suffixes or DEFAULT_INPUT_SUFFIXES)
    server.app_name = app_name
    server.app_version = app_version
    server.auth_token = auth_token or secrets.token_urlsafe(32)
    server.request_timeout = 30.0
    server.max_concurrent_requests = 4
    server.request_slots = threading.BoundedSemaphore(server.max_concurrent_requests)
    return server


def serve_upload_server(
    config,
    host: str = "127.0.0.1",
    port: int = 0,
    auth_token: str | None = None,
    *,
    engine_factory=None,
    supported_input_suffixes=None,
    app_name: str = DEFAULT_APP_NAME,
    app_version: str = DEFAULT_APP_VERSION,
):
    server = create_upload_server(
        config,
        host,
        port,
        auth_token,
        engine_factory=engine_factory,
        supported_input_suffixes=supported_input_suffixes,
        app_name=app_name,
        app_version=app_version,
    )
    print(f"{app_name} upload server: http://{host}:{server.server_address[1]}", file=sys.stderr)
    print(f"{app_name} upload token: {server.auth_token}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
