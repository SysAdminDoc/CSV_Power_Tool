"""Format-aware, bounded DuckDB execution for local input files."""

from __future__ import annotations

import csv
import hashlib
import importlib
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


SQL_REPORT_FORMAT = "csv-power-tool-sql-report"
SQL_REPORT_VERSION = 1
SQL_INPUT_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".parquet", ".jsonl", ".ndjson"}


class SQLQueryError(RuntimeError):
    """Actionable SQL failure carrying the machine-readable execution report."""

    def __init__(self, message: str, report: dict, code: str = "sql_error"):
        super().__init__(message)
        self.code = code
        self.report = report


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_module(name: str):
    return importlib.import_module(name)


def _limit(config, name: str, default: int) -> int:
    try:
        value = int(getattr(config, name, default))
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def _float_limit(config, name: str, default: float) -> float:
    try:
        value = float(getattr(config, name, default))
    except (TypeError, ValueError):
        value = default
    return max(0.001, value)


def _report_for(query: str, config) -> dict:
    max_rows = _limit(config, "sql_max_rows", 100_000)
    max_cell_bytes = _limit(config, "sql_max_cell_bytes", 1 * 1024 * 1024)
    timeout_seconds = _float_limit(config, "sql_timeout_seconds", 30.0)
    memory_limit_mb = _limit(config, "sql_memory_limit_mb", 512)
    source_rows = _limit(config, "max_input_rows", 1_000_000)
    return {
        "format": SQL_REPORT_FORMAT,
        "version": SQL_REPORT_VERSION,
        "status": "running",
        "started_at": _utc_now(),
        "query": query.strip(),
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "limits": {
            "max_result_rows": max_rows,
            "max_result_cell_bytes": max_cell_bytes,
            "timeout_seconds": timeout_seconds,
            "memory_limit_mb": memory_limit_mb,
            "max_source_rows": source_rows,
            "threads": 1,
            "enforcement": [
                "result rows are fetched with LIMIT max_result_rows + 1",
                "source files are materialized with LIMIT max_source_rows + 1",
                "DuckDB memory_limit and a single worker thread are configured",
                "the worker connection is interrupted when timeout_seconds elapses",
                "external file access is disabled before the user query runs",
            ],
        },
        "views": [],
        "result": {
            "row_count": 0,
            "column_count": 0,
            "columns": [],
            "truncated": False,
        },
        "errors": [],
    }


def _mark_error(report: dict, code: str, message: str, details: dict | None = None) -> None:
    report["status"] = "error"
    report["finished_at"] = _utc_now()
    report["error"] = {
        "code": code,
        "message": message,
        **(details or {}),
    }
    report["errors"] = [message]


def _fail(report: dict, code: str, message: str, details: dict | None = None):
    _mark_error(report, code, message, details)
    raise SQLQueryError(message, report, code)


def _skip_sql_noise(text: str, start: int = 0) -> int:
    index = start
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text.startswith("--", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                return len(text)
            index = end + 2
            continue
        break
    return index


def _single_sql_statement(query: str, report: dict) -> str:
    """Accept one read-only statement and remove one optional trailing semicolon."""

    text = query.strip()
    if not text:
        _fail(report, "empty_query", "SQL query cannot be empty")

    semicolons = []
    index = 0
    quote = None
    while index < len(text):
        character = text[index]
        if quote:
            if character == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            elif character == "\\" and quote == '"':
                index += 2
                continue
            index += 1
            continue
        if text.startswith("--", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                _fail(report, "invalid_query", "SQL query contains an unterminated block comment")
            index = end + 2
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == ";":
            semicolons.append(index)
        index += 1

    if quote:
        _fail(report, "invalid_query", "SQL query contains an unterminated quoted string")
    if len(semicolons) > 1 or (semicolons and text[semicolons[0] + 1 :].strip()):
        _fail(
            report,
            "multiple_statements",
            "SQL mode accepts exactly one read-only statement; remove additional statements",
        )
    if semicolons:
        text = text[: semicolons[0]].rstrip()

    first = _skip_sql_noise(text)
    match = re.match(r"[A-Za-z_]+", text[first:])
    keyword = match.group(0).lower() if match else ""
    if keyword not in {"select", "with", "values"}:
        _fail(
            report,
            "read_only_required",
            "SQL mode accepts SELECT, WITH, or VALUES statements only; no database writes are permitted",
        )
    return text


def _cell_text(engine, value) -> str:
    return engine._cell_to_text(value)


def _cell_size(engine, value) -> int:
    return len(_cell_text(engine, value).encode("utf-8"))


def _unique_columns(engine, values) -> list[str]:
    seen: set[str] = set()
    columns = []
    for value in values:
        name = engine._normalize_header(
            _cell_text(engine, value),
            getattr(engine.config, "header_normalize", "none"),
        )
        if not name:
            continue
        candidate = name
        suffix = 2
        while candidate in seen:
            candidate = f"{name}_{suffix}"
            suffix += 1
        seen.add(candidate)
        columns.append(candidate)
    return columns


def _raise_if_invalid_source(engine, path: Path, report: dict, fallback: str) -> None:
    if not engine._validate_input_file(path):
        message = engine.stats.fatal_input_errors[-1] if engine.stats.fatal_input_errors else fallback
        _fail(report, "invalid_input", str(message), {"source": str(path)})


def _delimited_metadata(engine, path: Path, report: dict) -> dict:
    """Preflight delimited files so ragged rows fail before DuckDB can sniff them away."""

    try:
        encoding, detected_delimiter, detected_quote = engine._detect_file_params(path)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        _fail(report, "source_decode_error", f"Unable to inspect {path.name}: {exc}", {"source": str(path)})

    suffix = path.suffix.lower()
    delimiter = "\t" if suffix == ".tsv" else detected_delimiter or ","
    quotechar = detected_quote or '"'
    if len(delimiter) != 1 or len(quotechar) != 1:
        _fail(
            report,
            "source_format_error",
            f"Could not determine a one-character delimiter/quote for {path.name}",
            {"source": str(path)},
        )

    max_rows = _limit(engine.config, "max_input_rows", 1_000_000)
    max_cell_bytes = _limit(engine.config, "max_cell_bytes", 1 * 1024 * 1024)
    metadata = {
        "encoding": encoding or "utf-8",
        "delimiter": delimiter,
        "quotechar": quotechar,
        "row_count": 0,
        "columns": [],
        "empty": False,
    }
    try:
        with path.open("r", encoding=encoding, newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter, quotechar=quotechar, strict=True)
            raw_headers = next(reader, None)
            if raw_headers is None:
                metadata["empty"] = True
                return metadata
            metadata["columns"] = _unique_columns(engine, raw_headers)
            expected = len(raw_headers)
            if expected > _limit(engine.config, "max_input_columns", 16_384):
                _fail(
                    report,
                    "source_column_limit",
                    f"{path.name} has {expected:,} columns; the configured limit is exceeded",
                    {"source": str(path), "observed_columns": expected},
                )
            for header in raw_headers:
                if len(str(header).encode("utf-8")) > max_cell_bytes:
                    _fail(
                        report,
                        "source_cell_limit",
                        f"{path.name} contains a header cell larger than {max_cell_bytes:,} bytes",
                        {"source": str(path)},
                    )
            for line_number, values in enumerate(reader, 2):
                if not values or not any(str(value) for value in values):
                    continue
                if len(values) != expected:
                    _fail(
                        report,
                        "malformed_delimited_row",
                        f"{path.name} line {line_number} has {len(values)} fields; expected {expected}",
                        {
                            "source": str(path),
                            "line": line_number,
                            "observed_fields": len(values),
                            "expected_fields": expected,
                        },
                    )
                metadata["row_count"] += 1
                if metadata["row_count"] > max_rows:
                    _fail(
                        report,
                        "source_row_limit",
                        f"{path.name} exceeds the configured source-row limit of {max_rows:,}",
                        {"source": str(path), "limit": max_rows},
                    )
                for value in values:
                    if len(str(value).encode("utf-8")) > max_cell_bytes:
                        _fail(
                            report,
                            "source_cell_limit",
                            f"{path.name} line {line_number} contains a cell larger than {max_cell_bytes:,} bytes",
                            {"source": str(path), "line": line_number, "limit": max_cell_bytes},
                        )
    except SQLQueryError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        _fail(
            report,
            "source_decode_error",
            f"Unable to parse {path.name}: {exc}",
            {"source": str(path)},
        )
    return metadata


def _write_conversion_csv(engine, columns: list[str], rows: list[dict], prefix: str) -> Path:
    """Write a bounded explicit UTF-8 conversion consumed by DuckDB."""

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        prefix=prefix,
        suffix=".csv",
        delete=False,
    )
    path = Path(handle.name)
    try:
        with handle:
            writer = csv.writer(handle)
            output_columns = columns or ["__empty_input"]
            writer.writerow(output_columns)
            for row in rows:
                writer.writerow([_cell_text(engine, row.get(column, "")) for column in output_columns])
            handle.flush()
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def _conversion_relation(connection, path: Path, report: dict):
    try:
        return connection.read_csv(
            str(path),
            delimiter=",",
            quotechar='"',
            header=True,
            strict_mode=True,
            sample_size=-1,
            encoding="utf-8",
        )
    except Exception as exc:
        _fail(
            report,
            "conversion_error",
            f"DuckDB could not read the local conversion file: {exc}",
            {"adapter": "explicit-utf8-csv-conversion"},
        )


def _text_fallback_relation(connection, engine, path: Path, metadata: dict, report: dict):
    all_columns: set[str] = set()
    column_order: list[str] = []
    rows = engine._read_file(path, all_columns, column_order)
    if engine.stats.fatal_input_errors:
        _fail(
            report,
            "source_parse_error",
            engine.stats.fatal_input_errors[-1],
            {"source": str(path)},
        )
    columns = column_order or metadata.get("columns", [])
    temporary_path = _write_conversion_csv(engine, columns, rows, ".csv-power-tool-sql-text-")
    report_item = {
        "format": path.suffix.lower().lstrip("."),
        "adapter": "python-delimited-to-duckdb-csv",
        "limitations": [
            "non-UTF-8 text is decoded locally before registration",
            "decoded values are written to a bounded temporary UTF-8 CSV before SQL execution",
        ],
        "_temporary_path": temporary_path,
    }
    try:
        relation = _conversion_relation(connection, temporary_path, report)
    except SQLQueryError:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return relation, report_item


def _xlsx_relation(connection, engine, path: Path, report: dict):
    try:
        load_workbook = _optional_module("openpyxl").load_workbook
    except ImportError:
        _fail(report, "missing_dependency", "openpyxl is required for SQL queries over XLSX files", {"source": str(path)})

    max_rows = _limit(engine.config, "max_input_rows", 1_000_000)
    max_columns = _limit(engine.config, "max_input_columns", 16_384)
    workbook = None
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        row_iter = sheet.iter_rows(values_only=True)
        raw_headers = list(next(row_iter, ()))
        if len(raw_headers) > max_columns:
            _fail(
                report,
                "source_column_limit",
                f"{path.name} has {len(raw_headers):,} columns; the configured limit is exceeded",
                {"source": str(path), "observed_columns": len(raw_headers), "limit": max_columns},
            )
        columns = _unique_columns(engine, raw_headers)
        rows = []
        for row_number, values in enumerate(row_iter, 2):
            if not any(value is not None for value in values):
                continue
            if len(rows) >= max_rows:
                _fail(
                    report,
                    "source_row_limit",
                    f"{path.name} exceeds the configured source-row limit of {max_rows:,}",
                    {"source": str(path), "limit": max_rows},
                )
            row = {}
            column_index = 0
            for index, header in enumerate(raw_headers):
                if column_index >= len(columns):
                    break
                name = columns[column_index]
                if not engine._normalize_header(
                    _cell_text(engine, header), getattr(engine.config, "header_normalize", "none")
                ):
                    continue
                value = values[index] if index < len(values) else None
                if _cell_size(engine, value) > _limit(engine.config, "max_cell_bytes", 1 * 1024 * 1024):
                    _fail(
                        report,
                        "source_cell_limit",
                        f"{path.name} row {row_number} contains a cell larger than the configured limit",
                        {"source": str(path), "row": row_number},
                    )
                row[name] = value
                column_index += 1
            rows.append(row)

        temporary_path = _write_conversion_csv(engine, columns, rows, ".csv-power-tool-sql-xlsx-")
        try:
            relation = _conversion_relation(connection, temporary_path, report)
        except SQLQueryError:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise
        return relation, {
            "format": "xlsx",
            "adapter": "openpyxl-to-duckdb-csv",
            "limitations": [
                "the active worksheet is used",
                "formula cells use cached values when present; uncached formulas are empty",
                "workbook values are materialized in memory and converted to a temporary UTF-8 CSV",
            ],
            "_temporary_path": temporary_path,
        }
    except SQLQueryError:
        raise
    except Exception as exc:
        _fail(report, "source_parse_error", f"Unable to read workbook {path.name}: {exc}", {"source": str(path)})
    finally:
        if workbook is not None:
            workbook.close()


def _relation_for(connection, engine, path: Path, report: dict):
    suffix = path.suffix.lower()
    if suffix not in SQL_INPUT_SUFFIXES:
        _fail(
            report,
            "unsupported_source_format",
            f"SQL mode does not support {suffix or '(no extension)'}; supported formats are CSV, TSV, XLSX, Parquet, and JSONL",
            {"source": str(path), "supported_formats": sorted(SQL_INPUT_SUFFIXES)},
        )
    _raise_if_invalid_source(engine, path, report, f"Input file is not readable: {path}")

    if suffix == ".xlsx":
        return _xlsx_relation(connection, engine, path, report)

    if suffix in {".csv", ".tsv", ".txt"}:
        metadata = _delimited_metadata(engine, path, report)
        if not metadata["columns"]:
            temporary_path = _write_conversion_csv(
                engine, [], [], ".csv-power-tool-sql-empty-"
            )
            relation = _conversion_relation(connection, temporary_path, report)
            return relation, {
                "format": suffix.lstrip("."),
                "adapter": "empty-delimited-source",
                "limitations": ["the source has no header; a synthetic __empty_input column is exposed"],
                "_temporary_path": temporary_path,
            }
        encoding = str(metadata.get("encoding") or "utf-8").lower().replace("_", "-")
        if encoding not in {"utf-8", "utf8", "utf-8-sig", "ascii"}:
            return _text_fallback_relation(connection, engine, path, metadata, report)
        kwargs = {
            "delimiter": metadata["delimiter"],
            "quotechar": metadata["quotechar"],
            "header": True,
            "strict_mode": True,
            "sample_size": -1,
            "encoding": "utf-8",
        }
        try:
            relation = connection.read_csv(str(path), **kwargs)
        except Exception as exc:
            _fail(
                report,
                "source_parse_error",
                f"DuckDB could not parse {path.name}: {exc}",
                {"source": str(path), "adapter": "duckdb.read_csv"},
            )
        return relation, {
            "format": suffix.lstrip("."),
            "adapter": "duckdb.read_csv",
            "limitations": [
                "delimiter and quote settings are inferred locally and then made explicit",
                "UTF-8 input is read natively by DuckDB",
            ],
        }

    if suffix == ".parquet":
        try:
            return connection.read_parquet(str(path)), {
                "format": "parquet",
                "adapter": "duckdb.read_parquet",
                "limitations": ["Parquet values remain subject to the configured memory and source-row budgets"],
            }
        except Exception as exc:
            _fail(
                report,
                "source_parse_error",
                f"DuckDB could not parse {path.name}: {exc}",
                {"source": str(path), "adapter": "duckdb.read_parquet"},
            )

    try:
        return connection.read_json(
            str(path),
            format="newline_delimited",
            ignore_errors=False,
            maximum_object_size=_limit(engine.config, "max_cell_bytes", 1 * 1024 * 1024),
        ), {
            "format": "jsonl",
            "adapter": "duckdb.read_json",
            "limitations": [
                "each line must be a JSON object or a DuckDB-compatible JSON record",
                "schema is inferred from the sampled JSON records",
            ],
        }
    except Exception as exc:
        _fail(
            report,
            "source_parse_error",
            f"DuckDB could not parse {path.name}: {exc}",
            {"source": str(path), "adapter": "duckdb.read_json"},
        )


def _describe(connection, view_name: str) -> list[dict]:
    return [
        {
            "name": row[0],
            "type": row[1],
            "nullable": str(row[2]).upper() in {"YES", "TRUE", "1"},
        }
        for row in connection.execute(f"DESCRIBE {view_name}").fetchall()
    ]


def _materialize_sources(connection, engine, files: list[Path], report: dict) -> None:
    source_limit = _limit(engine.config, "max_input_rows", 1_000_000)
    for index, path in enumerate(files):
        relation, adapter = _relation_for(connection, engine, path, report)
        source_name = f"__csv_power_source_{index}"
        view_name = f"input_{index}"
        try:
            connection.register(source_name, relation)
            connection.execute(
                f"CREATE TEMP TABLE {view_name} AS "
                f"SELECT * FROM {source_name} LIMIT {source_limit + 1}"
            )
            row_count = int(connection.execute(f"SELECT COUNT(*) FROM {view_name}").fetchone()[0])
            if row_count > source_limit:
                _fail(
                    report,
                    "source_row_limit",
                    f"{path.name} exceeds the configured source-row limit of {source_limit:,}",
                    {"source": str(path), "limit": source_limit, "observed_at_least": row_count},
                )
            schema = _describe(connection, view_name)
            report["views"].append({
                "name": view_name,
                "source": str(path),
                "format": adapter["format"],
                "adapter": adapter["adapter"],
                "limitations": adapter["limitations"],
                "row_count": row_count,
                "empty": row_count == 0,
                "schema": schema,
            })
        finally:
            try:
                connection.unregister(source_name)
            except Exception:
                pass
            temporary_path = adapter.get("_temporary_path")
            if temporary_path is not None:
                try:
                    Path(temporary_path).unlink()
                except FileNotFoundError:
                    pass


def _run_in_worker(engine, files: list[Path], query: str, report: dict, state: dict):
    connection = None
    temporary_directory = None
    try:
        duckdb = _optional_module("duckdb")
        connection = duckdb.connect(database=":memory:")
        state["connection"] = connection
        memory_limit_mb = _limit(engine.config, "sql_memory_limit_mb", 512)
        connection.execute(f"SET memory_limit='{memory_limit_mb}MB'")
        connection.execute("SET threads=1")
        temporary_directory = Path(tempfile.mkdtemp(prefix="csv-power-tool-sql-"))
        escaped_temp = str(temporary_directory).replace("'", "''")
        connection.execute(f"SET temp_directory='{escaped_temp}'")

        _materialize_sources(connection, engine, files, report)
        connection.execute("SET enable_external_access=false")

        max_rows = _limit(engine.config, "sql_max_rows", 100_000)
        wrapped_query = (
            "SELECT * FROM ("
            f"{query}"
            f") AS __csv_power_result LIMIT {max_rows + 1}"
        )
        result = connection.execute(wrapped_query)
        columns = [description[0] for description in (result.description or [])]
        values = result.fetchall()
        if len(values) > max_rows:
            _fail(
                report,
                "result_row_limit",
                f"SQL result exceeds the configured limit of {max_rows:,} rows; add a narrower predicate or raise --sql-max-rows",
                {"limit": max_rows, "observed_at_least": len(values)},
            )

        max_cell_bytes = _limit(engine.config, "sql_max_cell_bytes", 1 * 1024 * 1024)
        rows = []
        for row_number, values_row in enumerate(values, 1):
            row = {}
            for column, value in zip(columns, values_row):
                text = _cell_text(engine, value)
                byte_count = len(text.encode("utf-8"))
                if byte_count > max_cell_bytes:
                    _fail(
                        report,
                        "result_cell_limit",
                        f"SQL result cell {column!r} at row {row_number:,} is {byte_count:,} bytes; the configured limit is {max_cell_bytes:,}",
                        {
                            "column": column,
                            "row": row_number,
                            "observed_bytes": byte_count,
                            "limit": max_cell_bytes,
                        },
                    )
                row[column] = text
            rows.append(row)

        report["status"] = "success"
        report["finished_at"] = _utc_now()
        report["result"] = {
            "row_count": len(rows),
            "column_count": len(columns),
            "columns": columns,
            "truncated": False,
        }
        state["result"] = (rows, columns)
    except SQLQueryError as exc:
        state["error"] = exc
    except BaseException as exc:
        state["error"] = exc
    finally:
        state["connection"] = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if temporary_directory is not None:
            shutil.rmtree(temporary_directory, ignore_errors=True)


def execute_sql_query(engine, files: list[Path], query: str):
    """Execute one local read-only query and return rows, columns, and its report."""

    report = _report_for(str(query or ""), engine.config)
    try:
        normalized_query = _single_sql_statement(str(query or ""), report)
    except SQLQueryError:
        engine.stats.sql_report = report
        raise

    state = {}
    worker = threading.Thread(
        target=_run_in_worker,
        args=(engine, [Path(path) for path in files], normalized_query, report, state),
        name="csv-power-tool-sql",
        daemon=True,
    )
    worker.start()
    timeout_seconds = _float_limit(engine.config, "sql_timeout_seconds", 30.0)
    deadline = time.monotonic() + timeout_seconds
    while worker.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        worker.join(min(0.1, remaining))
        if getattr(engine, "cancelled", False):
            connection = state.get("connection")
            if connection is not None:
                try:
                    connection.interrupt()
                except Exception:
                    pass
            worker.join(5.0)
            message = "SQL execution was cancelled; no output was written"
            _mark_error(report, "query_cancelled", message)
            engine.stats.cancelled = True
            engine.stats.sql_report = report
            raise SQLQueryError(message, report, "query_cancelled")
    if worker.is_alive():
        connection = state.get("connection")
        if connection is not None:
            try:
                connection.interrupt()
            except Exception:
                pass
        worker.join(5.0)
        message = (
            f"SQL execution exceeded the configured timeout of {timeout_seconds:g} seconds and was interrupted; "
            "narrow the query or raise --sql-timeout-seconds"
        )
        if worker.is_alive():
            message += " (the DuckDB worker is still stopping)"
        _mark_error(report, "query_timeout", message, {"timeout_seconds": timeout_seconds})
        engine.stats.sql_report = report
        raise SQLQueryError(message, report, "query_timeout")

    error = state.get("error")
    if error is not None:
        if isinstance(error, SQLQueryError):
            report = error.report
            engine.stats.sql_report = report
            raise error
        message = f"SQL execution failed: {error}"
        _mark_error(report, "sql_error", message, {"exception": type(error).__name__})
        engine.stats.sql_report = report
        raise SQLQueryError(message, report, "sql_error") from error

    rows, columns = state.get("result", ([], []))
    engine.stats.sql_report = report
    return rows, columns, report
