#!/usr/bin/env python3
"""
CSV Power Tool
Professional-grade CSV file combiner and processor.
Merge, filter, transform, deduplicate, and export CSV data with full control.
"""

import sys
import csv
import re
import json
import copy
import importlib
import hashlib
import os
import shutil
import threading
import time
import locale
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Callable
from tkinter import filedialog, StringVar, BooleanVar, IntVar, END
from csv_power_tool import api as upload_api
from csv_power_tool.core import EngineService
from csv_power_tool.workflow import (
    WorkflowError,
    append_history,
    build_workflow,
    canonical_json,
    extract_config,
    load_workflow,
    operation_types,
    workflow_inputs,
    workflow_output,
    write_workflow,
)
from csv_power_tool.schema import (
    SchemaError,
    infer_schema,
    load_schema,
    normalize_schema,
    validate_rows,
    validation_report,
    write_schema,
    write_validation_report,
)
from csv_power_tool.quality import (
    QualityError,
    QualityProfiler,
    apply_repairs,
    infer_value_type,
    load_repairs,
    normalize_repairs,
    write_quality_report,
)
from csv_power_tool.joins import (
    JoinError,
    analyze_join,
    analyze_three_way,
    execute_join,
    execute_three_way,
)

APP_NAME = "CSV Power Tool"
APP_VERSION = "3.2.0"
SUPPORTED_INPUT_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".parquet", ".jsonl", ".ndjson"}
SUPPORTED_TEXT_SUFFIXES = {".csv", ".tsv", ".txt"}
SUPPORTED_JSONL_SUFFIXES = {".jsonl", ".ndjson"}
SUPPORTED_STREAM_SUFFIXES = SUPPORTED_TEXT_SUFFIXES | SUPPORTED_JSONL_SUFFIXES
SUPPORTED_STREAM_INPUT_SUFFIXES = SUPPORTED_STREAM_SUFFIXES | {".parquet"}
SUPPORTED_STREAM_OUTPUT_SUFFIXES = SUPPORTED_STREAM_SUFFIXES | {".parquet"}
SUPPORTED_OUTPUT_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".parquet", ".jsonl", ".ndjson"}


def _optional_module(name: str):
    """Load a feature dependency lazily so headless/package startup stays small."""
    return importlib.import_module(name)


def _write_json_atomic(path: str | Path, payload: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        delete=False,
    )
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            json.dump(payload, temporary, indent=2, ensure_ascii=False, default=str)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return target


try:
    locale.setlocale(locale.LC_COLLATE, "")
except locale.Error:
    pass


GUI_IMPORT_ERROR = None
try:
    import customtkinter as ctk
except ImportError as exc:
    GUI_IMPORT_ERROR = exc

    class _MissingCTkFrame:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("customtkinter is required for GUI mode")

    class _MissingCTk:
        CTkFrame = _MissingCTkFrame

    ctk = _MissingCTk()

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_AVAILABLE = False
else:
    DND_AVAILABLE = GUI_IMPORT_ERROR is None


# ══════════════════════════════════════════════════════════════════════════════
# THEME CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

COLORS = {
    "bg_dark": "#020617",
    "bg_secondary": "#0f172a",
    "bg_tertiary": "#1e293b",
    "bg_input": "#0f172a",
    "bg_hover": "#334155",
    "border": "#334155",
    "border_light": "#475569",
    "text_primary": "#f8fafc",
    "text_secondary": "#94a3b8",
    "text_muted": "#64748b",
    "accent_green": "#22c55e",
    "accent_green_hover": "#16a34a",
    "accent_blue": "#60a5fa",
    "accent_blue_hover": "#3b82f6",
    "accent_purple": "#a78bfa",
    "accent_purple_hover": "#8b5cf6",
    "accent_orange": "#f97316",
    "accent_red": "#ef4444",
    "accent_cyan": "#22d3ee",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "error": "#ef4444",
    "tab_active": "#1e293b",
    "tab_inactive": "#0f172a",
}

DARK_COLORS = COLORS.copy()
LIGHT_COLORS = {
    **DARK_COLORS,
    "bg_dark": "#f8fafc",
    "bg_secondary": "#ffffff",
    "bg_tertiary": "#e2e8f0",
    "bg_input": "#f1f5f9",
    "bg_hover": "#cbd5e1",
    "border": "#cbd5e1",
    "border_light": "#94a3b8",
    "text_primary": "#0f172a",
    "text_secondary": "#334155",
    "text_muted": "#64748b",
    "tab_active": "#e2e8f0",
    "tab_inactive": "#f1f5f9",
}

FONTS = {
    "title": ("Segoe UI", 24, "bold"),
    "heading": ("Segoe UI", 14, "bold"),
    "subheading": ("Segoe UI", 12, "bold"),
    "body": ("Segoe UI", 12),
    "small": ("Segoe UI", 11),
    "mono": ("Consolas", 11),
}


# ══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PreviewBudget:
    """Hard limits for read-only preview scans."""

    row_limit: int = 100
    scan_row_limit: int = 5_000
    scan_byte_limit: int = 8 * 1024 * 1024
    column_limit: int = 256
    cell_byte_limit: int = 16 * 1024


@dataclass
class ProcessingConfig:
    """Configuration for CSV processing."""
    # Column selection
    columns_mode: str = "all"  # "all", "select", "exclude"
    selected_columns: list = field(default_factory=list)
    column_mapping: dict = field(default_factory=dict)  # {original: renamed}
    column_order: list = field(default_factory=list)

    # Sorting
    sort_enabled: bool = False
    sort_columns: list = field(default_factory=list)  # [(column, ascending), ...]
    sort_case_sensitive: bool = False
    sort_numeric_aware: bool = True

    # Deduplication
    dedupe_enabled: bool = True
    dedupe_columns: list = field(default_factory=list)  # Empty = all columns
    dedupe_keep: str = "first"  # "first", "last", "none"
    dedupe_fuzzy_enabled: bool = False
    dedupe_fuzzy_threshold: int = 90
    dedupe_aggregate_mode: str = "none"  # "none", "max", "min", "sum", "concat"
    dedupe_aggregate_separator: str = "; "

    # Filtering
    filters: list = field(default_factory=list)  # [(column, operator, value), ...]
    filter_logic: str = "and"  # "and", "or"

    # Transformations
    trim_whitespace: bool = True
    case_transform: str = "none"  # "none", "upper", "lower", "title"
    empty_value: str = ""  # Replace empty cells with this

    # Header normalization
    header_normalize: str = "none"  # "none", "trim", "lowercase", "snake_case"
    strip_bom: bool = True

    # Input safety and malformed-row policy
    max_input_bytes: int = 512 * 1024 * 1024
    max_decompressed_bytes: int = 1024 * 1024 * 1024
    max_input_rows: int = 1_000_000
    max_input_columns: int = 16_384
    max_cell_bytes: int = 1 * 1024 * 1024
    max_json_nesting: int = 32
    max_workbook_sheets: int = 100
    max_sheet_rows: int = 1_048_576
    max_sheet_columns: int = 16_384
    invalid_row_policy: str = "fail"  # "fail", "warn", or "quarantine"
    quarantine_path: str = ""

    # Advanced transforms (per-column)
    column_transforms: list = field(default_factory=list)  # [(column, transform_type, *args)]
    # transform_type: "trim", "upper", "lower", "title", "replace", "regex_replace",
    #                  "split", "merge", "compute"

    # Schema unification
    schema_mode: str = "union"  # "union", "intersection", "first_file"
    schema_contract: dict = field(default_factory=dict)
    schema_validation_mode: str = "strict"  # "strict", "advisory", or "quarantine"
    schema_validation_report_path: str = ""
    schema_validate_only: bool = False
    column_template: str = ""  # Optional input file whose header defines output order
    source_column: str = ""  # Optional provenance column, e.g. "(Source)"
    source_value: str = "name"  # "name" or "path"

    # Reshaping
    unpivot_columns: list = field(default_factory=list)
    unpivot_name_column: str = "variable"
    unpivot_value_column: str = "value"
    pivot_index_columns: list = field(default_factory=list)
    pivot_column: str = ""
    pivot_value_column: str = ""
    pivot_aggregate: str = "first"  # "first", "sum", "min", "max", "concat"
    pivot_separator: str = "; "

    # Data-quality and privacy helpers
    redact_sensitive: bool = False
    redaction_token: str = "[REDACTED]"
    quality_scan_rows: int = 100_000
    quality_facet_limit: int = 20
    quality_max_distinct_values: int = 100_000
    quality_sample_limit: int = 5
    repair_edits: list = field(default_factory=list)
    repair_report_path: str = ""

    # Join and merge audit policies
    key_normalization: str = "trim-casefold"
    join_type: str = "inner"
    join_key_columns: list = field(default_factory=list)
    join_conflict_policy: str = "keep-both"
    join_report_path: str = ""
    merge_key_columns: list = field(default_factory=list)
    merge_conflict_resolution: str = "fail"
    merge_report_path: str = ""

    # Dataframe backend
    engine_backend: str = "auto"  # "auto", "python", or "polars"
    polars_threshold_bytes: int = 5_000_000
    stream_batch_rows: int = 2_048

    # Output
    output_delimiter: str = ","
    output_encoding: str = "utf-8"
    output_quoting: str = "minimal"  # "minimal", "all", "nonnumeric", "none"
    include_header: bool = True
    line_ending: str = "auto"  # "auto", "unix", "windows"
    streaming_enabled: bool = True
    output_collision_policy: str = "replace"  # "replace", "fail", or "backup"
    run_manifest_enabled: bool = True
    run_manifest_path: str = ""


@dataclass 
class ProcessingStats:
    """Statistics from processing."""
    files_processed: int = 0
    files_skipped: int = 0
    total_rows_read: int = 0
    rows_filtered: int = 0
    duplicates_removed: int = 0
    final_row_count: int = 0
    unique_columns: int = 0
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    fatal_input_errors: list = field(default_factory=list)
    quarantined_rows: int = 0
    cancelled: bool = False
    column_summary: dict = field(default_factory=dict)
    input_diagnostics: dict = field(default_factory=dict)
    schema_validation: dict = field(default_factory=dict)
    quality_profile: dict = field(default_factory=dict)
    repair_report: dict = field(default_factory=dict)
    join_report: dict = field(default_factory=dict)
    merge_report: dict = field(default_factory=dict)


class ConfigHistory:
    """Bounded undo/redo history for JSON-serializable preset snapshots."""

    def __init__(self, initial=None, limit: int = 50):
        self.limit = max(2, int(limit))
        self._states = [copy.deepcopy(initial or {})]
        self._index = 0

    def record(self, state: dict):
        snapshot = copy.deepcopy(state)
        if snapshot == self._states[self._index]:
            return
        self._states = self._states[: self._index + 1]
        self._states.append(snapshot)
        if len(self._states) > self.limit:
            trim = len(self._states) - self.limit
            self._states = self._states[trim:]
        self._index = len(self._states) - 1

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._states) - 1

    def undo(self):
        if not self.can_undo:
            return None
        self._index -= 1
        return copy.deepcopy(self._states[self._index])

    def redo(self):
        if not self.can_redo:
            return None
        self._index += 1
        return copy.deepcopy(self._states[self._index])


class ProcessingCancelled(Exception):
    """Internal signal used to discard an unfinished output temporary file."""


# ══════════════════════════════════════════════════════════════════════════════
# CSV PROCESSING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class CSVEngine:
    """Core CSV processing engine."""

    FILTER_OPERATORS = {
        "equals": lambda v, t: str(v).lower() == str(t).lower(),
        "not_equals": lambda v, t: str(v).lower() != str(t).lower(),
        "contains": lambda v, t: str(t).lower() in str(v).lower(),
        "not_contains": lambda v, t: str(t).lower() not in str(v).lower(),
        "starts_with": lambda v, t: str(v).lower().startswith(str(t).lower()),
        "ends_with": lambda v, t: str(v).lower().endswith(str(t).lower()),
        "is_empty": lambda v, t: not str(v).strip(),
        "is_not_empty": lambda v, t: bool(str(v).strip()),
        "greater_than": lambda v, t: CSVEngine._numeric_compare(v, t, ">"),
        "less_than": lambda v, t: CSVEngine._numeric_compare(v, t, "<"),
        "greater_than_or_equal": lambda v, t: CSVEngine._numeric_compare(v, t, ">="),
        "less_than_or_equal": lambda v, t: CSVEngine._numeric_compare(v, t, "<="),
        "between": lambda v, t: CSVEngine._between(v, t),
        "fuzzy": lambda v, t: CSVEngine._fuzzy_match(v, t),
        "in_list": lambda v, t: str(v).strip().lower() in [x.strip().lower() for x in str(t).split(",")],
        "not_in_list": lambda v, t: str(v).strip().lower() not in [x.strip().lower() for x in str(t).split(",")],
        "regex": lambda v, t: bool(re.search(t, str(v), re.IGNORECASE)),
    }

    SENSITIVE_COLUMN_HINTS = {
        "ssn", "social_security", "socialsecurity", "credit_card", "creditcard",
        "card_number", "cardnumber", "cvv", "email", "e_mail", "phone",
        "password", "secret", "token", "api_key", "apikey",
    }
    SENSITIVE_PATTERNS = (
        re.compile(r"^\d{3}-\d{2}-\d{4}$"),
        re.compile(r"^\d{13,19}$"),
        re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$"),
    )

    @staticmethod
    def _infer_value_type(value: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            return "empty"
        if text.lower() in {"true", "false", "yes", "no"}:
            return "boolean"
        if CSVEngine._parse_number(text) is not None:
            return "number"
        if CSVEngine._parse_datetime(text) is not None:
            return "datetime"
        return "text"

    @classmethod
    def infer_column_types(cls, rows: list[dict], columns: list[str] | None = None) -> dict:
        """Return deterministic type counts and a best-effort type per column."""
        columns = list(columns or [])
        if not columns and rows:
            columns = list(rows[0].keys())
        result = {}
        precedence = {"empty": 0, "boolean": 1, "number": 2, "datetime": 3, "text": 4}
        for column in columns:
            counts = Counter(cls._infer_value_type(row.get(column, "")) for row in rows)
            non_empty = [kind for kind in counts if kind != "empty"]
            if not non_empty:
                inferred = "empty"
            elif len(non_empty) == 1:
                inferred = non_empty[0]
            elif all(kind in {"number", "boolean"} for kind in non_empty):
                inferred = "number"
            else:
                inferred = max(non_empty, key=lambda kind: precedence[kind])
            result[column] = {
                "type": inferred,
                "counts": dict(sorted(counts.items())),
            }
        return result

    @classmethod
    def _looks_sensitive(cls, column: str, value: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(column).lower()).strip("_")
        if normalized in cls.SENSITIVE_COLUMN_HINTS:
            return True
        text = "" if value is None else str(value).strip()
        return bool(text and any(pattern.fullmatch(text) for pattern in cls.SENSITIVE_PATTERNS))

    def _redact_row(self, row: dict) -> dict:
        if not getattr(self.config, "redact_sensitive", False):
            return row
        token = getattr(self.config, "redaction_token", "[REDACTED]")
        return {
            column: token if self._looks_sensitive(column, value) else value
            for column, value in row.items()
        }

    @staticmethod
    def _parse_number(value):
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_datetime(value):
        """Parse common date/time strings and normalize aware values to UTC."""
        text = str(value).strip()
        if not text:
            return None

        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = None

        if parsed is None:
            formats = [
                "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d",
                "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M",
                "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y",
            ]
            for fmt in formats:
                try:
                    parsed = datetime.strptime(text.replace(",", ""), fmt)
                    break
                except ValueError:
                    continue

        if parsed is None:
            try:
                from email.utils import parsedate_to_datetime
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                return None

        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @staticmethod
    def _numeric_compare(v, t, op):
        v_num = CSVEngine._parse_number(v)
        t_num = CSVEngine._parse_number(t)
        if v_num is None or t_num is None:
            return False
        if op == ">":
            return v_num > t_num
        elif op == "<":
            return v_num < t_num
        elif op == ">=":
            return v_num >= t_num
        elif op == "<=":
            return v_num <= t_num
        return False

    @staticmethod
    def _between(v, t):
        """Check if a number or date is between two bounds."""
        parts = None
        target = str(t)
        for separator in ("..", "|", ","):
            split = target.split(separator)
            if len(split) == 2:
                parts = [split[0].strip(), split[1].strip()]
                break
        if parts is None:
            return False

        val_num = CSVEngine._parse_number(v)
        lo_num = CSVEngine._parse_number(parts[0])
        hi_num = CSVEngine._parse_number(parts[1])
        if val_num is not None and lo_num is not None and hi_num is not None:
            return lo_num <= val_num <= hi_num

        val_date = CSVEngine._parse_datetime(v)
        lo_date = CSVEngine._parse_datetime(parts[0])
        hi_date = CSVEngine._parse_datetime(parts[1])
        if val_date and lo_date and hi_date:
            return lo_date <= val_date <= hi_date

        return False

    @staticmethod
    def _fuzzy_match(v, t):
        """Return True when v is similar to target. Target may be 'text|90'."""
        target = str(t).strip()
        threshold = 85
        for separator in ("|", ","):
            candidate, sep, maybe_threshold = target.rpartition(separator)
            if sep and maybe_threshold.strip().isdigit():
                target = candidate.strip()
                threshold = int(maybe_threshold.strip())
                break

        if not target:
            return False

        value = str(v).strip()
        value_lower = value.lower()
        target_lower = target.lower()
        if target_lower in value_lower or value_lower in target_lower:
            return True
        try:
            fuzz = _optional_module("rapidfuzz.fuzz")
            score = fuzz.partial_ratio(value_lower, target_lower)
        except ImportError:
            from difflib import SequenceMatcher
            score = SequenceMatcher(None, value_lower, target_lower).ratio() * 100
        return score >= threshold

    @staticmethod
    def _normalize_header(name: str, mode: str) -> str:
        """Normalize a header name according to the mode."""
        if mode == "none":
            return name.strip()
        name = name.strip()
        # Strip BOM if present
        if name.startswith("﻿"):
            name = name[1:]
        if mode == "trim":
            return name
        if mode == "lowercase":
            return name.lower()
        if mode == "snake_case":
            # Convert to snake_case: "First Name" -> "first_name", "firstName" -> "first_name"
            import re as _re
            s = _re.sub(r'[\s\-\.]+', '_', name)  # spaces/hyphens/dots to underscore
            s = _re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s)
            s = _re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s)
            s = _re.sub(r'_+', '_', s)
            return s.lower().strip('_')
        return name

    @staticmethod
    def _detect_file_details(file_path: Path) -> dict:
        """Detect text-file parameters and expose confidence for diagnostics."""
        encoding = "utf-8"
        encoding_confidence = 0.25
        try:
            import chardet
            with open(file_path, "rb") as handle:
                raw = handle.read(min(32768, file_path.stat().st_size))
            detection = chardet.detect(raw)
            detected_encoding = detection.get("encoding") if detection else None
            if detected_encoding:
                enc_lower = detected_encoding.lower().replace("-", "").replace("_", "")
                if enc_lower in ("ascii", "utf8"):
                    encoding = "utf-8"
                elif enc_lower in ("iso88591", "latin1"):
                    encoding = "latin-1"
                elif enc_lower in ("cp1252", "windows1252"):
                    encoding = "cp1252"
                elif enc_lower.startswith("utf16"):
                    encoding = "utf-16"
                else:
                    encoding = detected_encoding
                encoding_confidence = float(detection.get("confidence") or 0.0)
        except (ImportError, OSError, ValueError):
            pass

        delimiter = ","
        quotechar = '"'
        delimiter_confidence = 0.2
        sample = ""
        for candidate_encoding in [encoding, "utf-8", "latin-1", "cp1252"]:
            try:
                with open(file_path, "r", encoding=candidate_encoding, newline="") as handle:
                    sample = handle.read(8192)
                encoding = candidate_encoding
                break
            except (UnicodeDecodeError, OSError):
                continue

        if sample:
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
                delimiter = dialect.delimiter
                quotechar = dialect.quotechar or '"'
                delimiter_confidence = 0.95
            except csv.Error:
                counts = {delim: sample.count(delim) for delim in [",", "\t", ";", "|"]}
                delimiter = max(counts, key=counts.get)
                if counts[delimiter] == 0:
                    delimiter = ","
                delimiter_confidence = 0.45 if counts[delimiter] else 0.2

        return {
            "encoding": encoding,
            "delimiter": delimiter,
            "quotechar": quotechar,
            "encoding_confidence": round(max(0.0, min(1.0, encoding_confidence)), 3),
            "delimiter_confidence": round(max(0.0, min(1.0, delimiter_confidence)), 3),
        }

    @staticmethod
    def _detect_file_params(file_path: Path) -> tuple:
        """Return the backwards-compatible (encoding, delimiter, quotechar) tuple."""
        details = CSVEngine._detect_file_details(file_path)
        return details["encoding"], details["delimiter"], details["quotechar"]

    @staticmethod
    def _cell_to_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _discover_structured_columns(self, file_path: Path) -> list[str]:
        suffix = file_path.suffix.lower()
        norm_mode = getattr(self.config, 'header_normalize', 'none')

        if suffix == ".xlsx":
            try:
                load_workbook = _optional_module("openpyxl").load_workbook
            except ImportError as exc:
                raise RuntimeError("openpyxl is required for .xlsx input") from exc

            workbook = load_workbook(file_path, read_only=True, data_only=True)
            try:
                sheet = workbook.active
                rows = sheet.iter_rows(values_only=True)
                headers = next(rows, [])
                return [
                    self._normalize_header(self._cell_to_text(col), norm_mode)
                    for col in headers
                    if self._normalize_header(self._cell_to_text(col), norm_mode)
                ]
            finally:
                workbook.close()

        if suffix == ".parquet":
            try:
                pq = _optional_module("pyarrow.parquet")
                schema = pq.read_schema(file_path)
                return [
                    self._normalize_header(name, norm_mode)
                    for name in schema.names
                    if self._normalize_header(name, norm_mode)
                ]
            except ImportError:
                try:
                    pl = _optional_module("polars")
                    frame = pl.scan_parquet(file_path)
                    return [
                        self._normalize_header(name, norm_mode)
                        for name in frame.collect_schema().names()
                        if self._normalize_header(name, norm_mode)
                    ]
                except ImportError as exc:
                    raise RuntimeError("pyarrow or polars is required for .parquet input") from exc

        return []

    def _discover_jsonl_columns(self, file_path: Path) -> list[str]:
        """Discover the union of object keys in a JSON Lines input."""
        if not self._validate_input_file(file_path):
            return []
        norm_mode = getattr(self.config, "header_normalize", "none")
        columns = []
        seen = set()
        try:
            with open(file_path, "r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except (json.JSONDecodeError, RecursionError) as exc:
                        action = self._record_invalid_row(file_path, str(exc), line_number, line)
                        if action == "stop":
                            break
                        continue
                    if not isinstance(value, dict):
                        action = self._record_invalid_row(
                            file_path, "expected a JSON object", line_number, line
                        )
                        if action == "stop":
                            break
                        continue
                    if not self._check_json_value(file_path, value, line_number):
                        break
                    if not self._check_column_limit(file_path, len(value)):
                        break
                    for key in value:
                        normalized = self._normalize_header(str(key), norm_mode)
                        if normalized and normalized not in seen:
                            seen.add(normalized)
                            columns.append(normalized)
        except (OSError, UnicodeDecodeError) as exc:
            self._record_fatal_input(file_path, f"error reading JSONL: {exc}")
        return columns

    def _read_jsonl_file(self, file_path: Path, all_columns: set, column_order: list) -> list[dict]:
        rows = []
        norm_mode = getattr(self.config, "header_normalize", "none")
        if not self._validate_input_file(file_path):
            return rows
        try:
            with open(file_path, "r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except (json.JSONDecodeError, RecursionError) as exc:
                        action = self._record_invalid_row(file_path, str(exc), line_number, line)
                        if action == "stop":
                            break
                        continue
                    if not isinstance(value, dict):
                        action = self._record_invalid_row(
                            file_path, "expected a JSON object", line_number, line
                        )
                        if action == "stop":
                            break
                        continue
                    if not self._check_json_value(file_path, value, line_number):
                        break
                    if not self._check_column_limit(file_path, len(value)):
                        break
                    if not self._check_row_limit(file_path, len(rows) + 1, line_number):
                        break

                    cleaned = {}
                    for key, raw_value in value.items():
                        normalized = self._normalize_header(str(key), norm_mode)
                        if not normalized:
                            continue
                        if normalized not in all_columns:
                            all_columns.add(normalized)
                            column_order.append(normalized)
                        cleaned[normalized] = self._cell_to_text(raw_value)
                        if self.config.trim_whitespace:
                            cleaned[normalized] = cleaned[normalized].strip()
                    rows.append(cleaned)
        except (OSError, UnicodeDecodeError) as exc:
            self.stats.files_skipped += 1
            self._record_fatal_input(file_path, str(exc))
            return []

        self.stats.files_processed += 1
        self.stats.total_rows_read += len(rows)
        self.log(f"OK {file_path.name} ({len(rows):,} JSON rows)", "success")
        return rows

    def _read_structured_file(self, file_path: Path, all_columns: set, column_order: list) -> list[dict]:
        suffix = file_path.suffix.lower()
        norm_mode = getattr(self.config, 'header_normalize', 'none')
        rows = []

        if not self._validate_input_file(file_path):
            return rows

        if suffix == ".xlsx":
            try:
                load_workbook = _optional_module("openpyxl").load_workbook

                workbook = load_workbook(file_path, read_only=True, data_only=True)
                try:
                    if len(workbook.sheetnames) > self._input_limit("max_workbook_sheets", 100):
                        self._record_fatal_input(
                            file_path,
                            f"workbook has {len(workbook.sheetnames):,} sheets, exceeding the configured limit",
                        )
                        return []
                    sheet = workbook.active
                    max_columns = int(sheet.max_column or 0)
                    max_rows = int(sheet.max_row or 0)
                    if max_columns > self._input_limit("max_sheet_columns", 16_384):
                        self._record_fatal_input(
                            file_path,
                            f"active sheet has {max_columns:,} columns, exceeding the configured limit",
                        )
                        return []
                    if max_rows > self._input_limit("max_sheet_rows", 1_048_576):
                        self._record_fatal_input(
                            file_path,
                            f"active sheet has {max_rows:,} rows, exceeding the configured limit",
                        )
                        return []
                    row_iter = sheet.iter_rows(values_only=True)
                    raw_headers = next(row_iter, [])
                    if not self._check_column_limit(file_path, len(raw_headers)):
                        return []
                    headers = [
                        self._normalize_header(self._cell_to_text(header), norm_mode)
                        for header in raw_headers
                    ]

                    for col in headers:
                        if col and col not in all_columns:
                            all_columns.add(col)
                            column_order.append(col)

                    for row_number, values in enumerate(row_iter, 2):
                        if not self._check_row_limit(file_path, len(rows) + 1, row_number):
                            break
                        cleaned_row = {}
                        valid_row = True
                        for idx, header in enumerate(headers):
                            if not header:
                                continue
                            value = values[idx] if idx < len(values) else ""
                            if not self._check_cell_limit(file_path, value, header, row_number):
                                valid_row = False
                                break
                            cleaned_row[header] = self._cell_to_text(value).strip() if self.config.trim_whitespace else self._cell_to_text(value)
                        if valid_row:
                            rows.append(cleaned_row)
                finally:
                    workbook.close()
            except Exception as exc:
                self.log(f"XX Error reading {file_path.name}: {exc}", "error")
                self.stats.files_skipped += 1
                self._record_fatal_input(file_path, str(exc))
                return []

        elif suffix == ".parquet":
            try:
                try:
                    pq = _optional_module("pyarrow.parquet")
                    parquet_file = pq.ParquetFile(file_path)
                    metadata = parquet_file.metadata
                    if not self._check_column_limit(file_path, metadata.num_columns):
                        return []
                    if not self._check_row_limit(file_path, metadata.num_rows):
                        return []
                    table = pq.read_table(file_path)
                    records = table.to_pylist()
                except ImportError:
                    pl = _optional_module("polars")
                    frame = pl.read_parquet(file_path)
                    if not self._check_column_limit(file_path, len(frame.columns)):
                        return []
                    if not self._check_row_limit(file_path, frame.height):
                        return []
                    records = frame.to_dicts()

                for row_number, record in enumerate(records, 1):
                    cleaned_row = {}
                    for key, value in record.items():
                        header = self._normalize_header(str(key), norm_mode)
                        if not header:
                            continue
                        if not self._check_cell_limit(file_path, value, header, row_number):
                            return []
                        if header not in all_columns:
                            all_columns.add(header)
                            column_order.append(header)
                        cleaned_row[header] = self._cell_to_text(value).strip() if self.config.trim_whitespace else self._cell_to_text(value)
                    rows.append(cleaned_row)
            except Exception as exc:
                self.log(f"XX Error reading {file_path.name}: {exc}", "error")
                self.stats.files_skipped += 1
                self._record_fatal_input(file_path, str(exc))
                return []

        self.stats.files_processed += 1
        self.stats.total_rows_read += len(rows)
        self.log(f"OK {file_path.name} ({len(rows):,} rows)", "success")
        return rows
    
    def __init__(self, config: ProcessingConfig, 
                 progress_callback: Callable = None,
                 log_callback: Callable = None):
        self.config = config
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.cancelled = False
        self.stats = ProcessingStats()
        self._input_diagnostics = {}
        self._summary_distinct = defaultdict(set)
        self._summary_non_empty = Counter()
        self._summary_rows = Counter()
        self._summary_types = defaultdict(Counter)
        self._validated_inputs = {}
        self._reported_input_issues = {}
        self._quarantine_records = []
        self._manifest_input_files = []
        self._schema_reports = []
        self._repair_report = {}

    INPUT_POLICIES = {"fail", "warn", "quarantine"}
    MAX_QUARANTINE_RECORDS = 10_000

    def _input_limit(self, name: str, default: int) -> int:
        try:
            return max(1, int(getattr(self.config, name, default)))
        except (TypeError, ValueError):
            return default

    def _invalid_row_policy(self) -> str:
        policy = str(getattr(self.config, "invalid_row_policy", "fail")).lower().strip()
        return policy if policy in self.INPUT_POLICIES else "fail"

    @staticmethod
    def _issue_message(file_path: Path, reason: str, line_number: int | None = None) -> str:
        location = f"{file_path.name}:{line_number}" if line_number else file_path.name
        return f"{location}: {reason}"

    def _record_fatal_input(self, file_path: Path, reason: str, line_number: int | None = None) -> None:
        message = self._issue_message(file_path, reason, line_number)
        key = (str(file_path), line_number, reason, "fatal")
        if key in self._reported_input_issues:
            return
        self._reported_input_issues[key] = "stop"
        self.stats.errors.append(message)
        self.stats.fatal_input_errors.append(message)
        self.log(f"XX {message}", "error")

    def _record_invalid_row(
        self,
        file_path: Path,
        reason: str,
        line_number: int | None = None,
        raw: str | None = None,
    ) -> str:
        """Record a malformed row and return keep, skip, or stop."""
        policy = self._invalid_row_policy()
        key = (str(file_path), line_number, reason, policy)
        if key in self._reported_input_issues:
            return self._reported_input_issues[key]

        if policy == "fail":
            self._record_fatal_input(file_path, reason, line_number)
            action = "stop"
        else:
            message = self._issue_message(file_path, reason, line_number)
            self.stats.warnings.append(message)
            self.log(f"!! {message}", "warning")
            if policy == "quarantine":
                self.stats.quarantined_rows += 1
                if len(self._quarantine_records) < self.MAX_QUARANTINE_RECORDS:
                    self._quarantine_records.append({
                        "file": str(file_path),
                        "line": line_number,
                        "reason": reason,
                        "raw": (raw or "")[:4096],
                    })
                action = "skip"
            else:
                action = "keep"
        self._reported_input_issues[key] = action
        return action

    def _validate_input_file(self, file_path: Path) -> bool:
        key = str(file_path.resolve())
        if key in self._validated_inputs:
            return self._validated_inputs[key]

        try:
            size = file_path.stat().st_size
        except OSError as exc:
            self._record_fatal_input(file_path, f"cannot stat input: {exc}")
            self._validated_inputs[key] = False
            return False

        if size > self._input_limit("max_input_bytes", 512 * 1024 * 1024):
            self._record_fatal_input(
                file_path,
                f"input size {size:,} bytes exceeds the configured limit",
            )
            self._validated_inputs[key] = False
            return False

        if file_path.suffix.lower() == ".xlsx":
            try:
                with zipfile.ZipFile(file_path) as archive:
                    expanded = sum(max(0, info.file_size) for info in archive.infolist())
                    if expanded > self._input_limit(
                        "max_decompressed_bytes", 1024 * 1024 * 1024
                    ):
                        self._record_fatal_input(
                            file_path,
                            f"decompressed workbook size {expanded:,} bytes exceeds the configured limit",
                        )
                        self._validated_inputs[key] = False
                        return False
            except (OSError, zipfile.BadZipFile) as exc:
                self._record_fatal_input(file_path, f"invalid workbook container: {exc}")
                self._validated_inputs[key] = False
                return False

        self._validated_inputs[key] = True
        return True

    def _check_column_limit(self, file_path: Path, count: int) -> bool:
        limit = self._input_limit("max_input_columns", 16_384)
        if count > limit:
            self._record_fatal_input(file_path, f"column count {count:,} exceeds the limit of {limit:,}")
            return False
        return True

    def _check_row_limit(self, file_path: Path, count: int, line_number: int | None = None) -> bool:
        limit = self._input_limit("max_input_rows", 1_000_000)
        if count > limit:
            self._record_fatal_input(
                file_path,
                f"row count exceeds the limit of {limit:,}",
                line_number,
            )
            return False
        return True

    def _check_cell_limit(
        self,
        file_path: Path,
        value,
        column: str | None = None,
        line_number: int | None = None,
    ) -> bool:
        text = self._cell_to_text(value)
        size = len(text.encode("utf-8"))
        limit = self._input_limit("max_cell_bytes", 1 * 1024 * 1024)
        if size > limit:
            label = f"cell {column!r} is" if column else "cell is"
            self._record_fatal_input(
                file_path,
                f"{label} {size:,} bytes, exceeding the limit of {limit:,}",
                line_number,
            )
            return False
        return True

    def _json_depth(self, value) -> int:
        maximum = 0
        pending = [(value, 0)]
        while pending:
            current, depth = pending.pop()
            maximum = max(maximum, depth)
            if isinstance(current, dict):
                pending.extend((item, depth + 1) for item in current.values())
            elif isinstance(current, list):
                pending.extend((item, depth + 1) for item in current)
        return maximum

    def _check_json_value(self, file_path: Path, value, line_number: int) -> bool:
        depth_limit = self._input_limit("max_json_nesting", 32)
        if self._json_depth(value) > depth_limit:
            self._record_fatal_input(
                file_path,
                f"JSON nesting exceeds the limit of {depth_limit}",
                line_number,
            )
            return False
        if isinstance(value, dict):
            values = value.values()
        elif isinstance(value, list):
            values = value
        else:
            values = [value]
        return all(self._check_cell_limit(file_path, item, line_number=line_number) for item in values)

    def _write_quarantine(self) -> None:
        quarantine_path = str(getattr(self.config, "quarantine_path", "") or "").strip()
        if not quarantine_path or not self._quarantine_records:
            return
        try:
            path = Path(quarantine_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                for record in self._quarantine_records:
                    json.dump(record, handle, ensure_ascii=False)
                    handle.write("\n")
        except OSError as exc:
            message = f"Quarantine write error: {exc}"
            self.stats.errors.append(message)
            self.log(f"XX {message}", "error")

    def _schema_validation_mode(self) -> str:
        mode = str(getattr(self.config, "schema_validation_mode", "strict")).lower().strip()
        return mode if mode in {"strict", "advisory", "quarantine"} else "strict"

    def _schema_error_message(self, error: dict) -> str:
        return (
            f"{Path(error['file']).name}:{error['row']} {error['column']} "
            f"violates {error['rule']} (observed {error['observed_value']!r})"
        )

    def _apply_schema_contract(self, file_path: Path, rows: list[dict]) -> list[dict]:
        contract = getattr(self.config, "schema_contract", {})
        if not contract:
            return rows
        try:
            valid_rows, report = validate_rows(rows, contract, file_path)
        except SchemaError as exc:
            self._record_fatal_input(file_path, f"invalid schema contract: {exc}")
            return rows

        self._schema_reports.append(report)
        if not report["errors"]:
            return rows
        mode = self._schema_validation_mode()
        messages = [self._schema_error_message(error) for error in report["errors"]]
        if mode == "strict":
            for error, message in zip(report["errors"], messages):
                self._record_fatal_input(file_path, message, error.get("row"))
            return rows
        if mode == "advisory":
            self.stats.warnings.extend(messages)
            return rows

        invalid_indexes = set(report.get("_invalid_indexes", []))
        self.stats.quarantined_rows += len(invalid_indexes)
        for index in sorted(invalid_indexes):
            raw = rows[index] if index < len(rows) else {}
            self._quarantine_records.append({
                "file": str(file_path),
                "line": index + 2,
                "reason": "schema validation",
                "raw": json.dumps(raw, ensure_ascii=False, default=str)[:4096],
            })
        self.stats.warnings.extend(messages)
        return valid_rows

    def _write_schema_validation_report(self) -> None:
        if not self._schema_reports:
            return
        report_path = str(getattr(self.config, "schema_validation_report_path", "") or "").strip()
        if not report_path:
            self.stats.schema_validation = validation_report(
                self._schema_reports,
                self._schema_validation_mode(),
                self.config.schema_contract,
            )
            return
        try:
            report = validation_report(
                self._schema_reports,
                self._schema_validation_mode(),
                self.config.schema_contract,
            )
            write_validation_report(report_path, report)
            self.stats.schema_validation = report
        except (OSError, SchemaError, TypeError, ValueError) as exc:
            message = f"Schema validation report error: {exc}"
            self.stats.errors.append(message)
            self.log(f"XX {message}", "error")

    def _apply_reviewed_repairs(self, rows: list[dict]) -> list[dict]:
        edits = getattr(self.config, "repair_edits", []) or []
        if not edits:
            return rows
        try:
            repaired, report = apply_repairs(rows, edits)
        except QualityError as exc:
            message = f"Repair validation failed: {exc}"
            self.stats.errors.append(message)
            self.log(f"XX {message}", "error")
            return rows
        report["input_rows"] = len(rows)
        self._repair_report = report
        self.stats.repair_report = report
        return repaired

    def _write_repair_report(self) -> None:
        if not self._repair_report:
            return
        report_path = str(getattr(self.config, "repair_report_path", "") or "").strip()
        if not report_path:
            return
        try:
            write_quality_report(report_path, self._repair_report)
        except (OSError, TypeError, ValueError) as exc:
            message = f"Repair report error: {exc}"
            self.stats.errors.append(message)
            self.log(f"XX {message}", "error")

    def validate_schema(self, input_files: list[Path]) -> ProcessingStats:
        """Validate inputs without writing output, for CLI validation-only mode."""
        self.stats = ProcessingStats()
        self._validated_inputs = {}
        self._reported_input_issues = {}
        self._quarantine_records = []
        self._input_diagnostics = {}
        self._manifest_input_files = [Path(path) for path in input_files]
        self._schema_reports = []
        self._repair_report = {}
        for file_path in input_files:
            all_columns = set()
            column_order = []
            rows = self._read_file(file_path, all_columns, column_order)
            self._apply_schema_contract(Path(file_path), rows)
        self._write_schema_validation_report()
        self._write_quarantine()
        return self.stats

    def profile(
        self,
        input_files: list[Path],
        scan_limit: int | None = None,
        facet_limit: int | None = None,
        max_distinct_values: int | None = None,
        sample_limit: int | None = None,
        filter_column: str | None = None,
        filter_value: str | None = None,
    ) -> dict:
        """Build an incremental quality profile without materializing all rows."""
        self.stats = ProcessingStats()
        self.cancelled = False
        maximum = max(1, int(scan_limit or getattr(self.config, "quality_scan_rows", 100_000)))
        profiler = QualityProfiler(
            facet_limit=facet_limit or getattr(self.config, "quality_facet_limit", 20),
            max_distinct_values=max_distinct_values or getattr(
                self.config, "quality_max_distinct_values", 100_000
            ),
            sample_limit=sample_limit or getattr(self.config, "quality_sample_limit", 5),
        )
        if filter_column is not None:
            filter_column = str(filter_column).strip()
            if not filter_column:
                filter_column = None
        filter_text = "" if filter_value is None else str(filter_value)
        scanned = 0
        truncated = False
        files_scanned = 0
        for file_index, raw_path in enumerate(input_files):
            if self.cancelled:
                break
            path = Path(raw_path)
            try:
                iterator = self._preview_row_iterator(
                    path,
                    batch_size=min(maximum - scanned, int(getattr(self.config, "stream_batch_rows", 2_048))),
                )
                for row in iterator:
                    if self.cancelled:
                        break
                    if scanned >= maximum:
                        truncated = True
                        break
                    if filter_column is None or self._cell_to_text(row.get(filter_column, "")) == filter_text:
                        profiler.add_row(row)
                    scanned += 1
                    if scanned % 256 == 0:
                        self.update_progress(
                            ((file_index + 0.5) / max(1, len(input_files))) * 100,
                            f"Profiling {path.name} ({scanned:,} row(s))",
                        )
                else:
                    files_scanned += 1
            except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as exc:
                self._record_fatal_input(path, f"quality profile failed: {exc}")
            if scanned >= maximum:
                truncated = True
                break
            self.update_progress(
                ((file_index + 1) / max(1, len(input_files))) * 100,
                f"Profiled {path.name} ({scanned:,} row(s))",
            )

        report = profiler.report(
            bounded=True,
            scan_limit=maximum,
            scan_truncated=truncated,
        )
        report["source_rows_scanned"] = scanned
        if filter_column is not None:
            report["facet_filter"] = {
                "column": str(filter_column),
                "value": "" if filter_value is None else str(filter_value),
            }
        report["files_scanned"] = files_scanned
        report["files_remaining"] = max(0, len(input_files) - files_scanned)
        report["cancelled"] = self.cancelled
        self.stats.files_processed = files_scanned
        self.stats.total_rows_read = scanned
        self.stats.quality_profile = report
        self.update_progress(100, "Quality profile ready")
        return report

    def inspect_row(self, input_files: list[Path], row_number: int) -> dict | None:
        """Read one global row with raw text and advisory type interpretations."""
        try:
            requested = int(row_number)
        except (TypeError, ValueError) as exc:
            raise QualityError("Inspected row must be a positive integer") from exc
        if requested < 1:
            raise QualityError("Inspected row must be a positive integer")

        raw_config = copy.deepcopy(self.config)
        raw_config.trim_whitespace = False
        raw_config.repair_edits = []
        inspector = CSVEngine(raw_config)
        current = 0
        for raw_path in input_files:
            path = Path(raw_path)
            iterator = inspector._preview_row_iterator(path, batch_size=1)
            for row in iterator:
                current += 1
                if current != requested:
                    continue
                values = []
                for column, value in row.items():
                    text_value = inspector._cell_to_text(value)
                    values.append({
                        "column": str(column),
                        "raw": text_value,
                        "inferred_type": infer_value_type(text_value),
                    })
                return {
                    "row_number": requested,
                    "source_file": str(path),
                    "values": values,
                    "raw_text_preserved": True,
                }
        return None

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _output_policy(self) -> str:
        policy = str(getattr(self.config, "output_collision_policy", "replace")).lower().strip()
        return policy if policy in {"replace", "fail", "backup"} else "replace"

    @staticmethod
    def _temporary_output_path(output_file: Path) -> Path:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        suffix = output_file.suffix or ".tmp"
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{output_file.name}.",
            suffix=suffix,
            dir=output_file.parent,
            delete=False,
        )
        path = Path(handle.name)
        handle.close()
        return path

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())

    def _backup_path(self, output_file: Path) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        candidate = output_file.with_name(f"{output_file.name}.{stamp}.bak")
        suffix = 1
        while candidate.exists():
            candidate = output_file.with_name(f"{output_file.name}.{stamp}.{suffix}.bak")
            suffix += 1
        return candidate

    def _commit_output(self, temporary: Path, output_file: Path) -> Path | None:
        policy = self._output_policy()
        if output_file.exists() and policy == "fail":
            raise RuntimeError(f"Output already exists: {output_file}")

        backup = None
        if output_file.exists() and policy == "backup":
            backup = self._backup_path(output_file)
            shutil.copy2(output_file, backup)

        os.replace(temporary, output_file)
        return backup

    def _manifest_path(self, output_file: Path) -> Path | None:
        if not getattr(self.config, "run_manifest_enabled", True):
            return None
        configured = str(getattr(self.config, "run_manifest_path", "") or "").strip()
        path = Path(configured) if configured else Path(f"{output_file}.manifest.json")
        if path.resolve() == output_file.resolve():
            raise RuntimeError("Run manifest path must differ from output path")
        return path

    def _write_run_manifest(
        self,
        output_file: Path,
        output_columns: list[str],
        backup_path: Path | None = None,
    ) -> None:
        manifest_path = self._manifest_path(output_file)
        if manifest_path is None:
            return

        config_data = asdict(self.config)
        config_json = json.dumps(config_data, sort_keys=True, ensure_ascii=False, default=str)
        inputs = []
        for input_path in self._manifest_input_files:
            try:
                stat = input_path.stat()
                inputs.append({
                    "path": str(input_path),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": self._sha256_file(input_path),
                })
            except OSError as exc:
                inputs.append({"path": str(input_path), "error": str(exc)})

        manifest = {
            "version": 1,
            "tool": APP_NAME,
            "tool_version": APP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "config_sha256": hashlib.sha256(config_json.encode("utf-8")).hexdigest(),
            "inputs": inputs,
            "output": {
                "path": str(output_file),
                "size": output_file.stat().st_size,
                "sha256": self._sha256_file(output_file),
                "backup": str(backup_path) if backup_path else None,
            },
            "schema": {"columns": output_columns, "column_count": len(output_columns)},
            "stats": {
                "files_processed": self.stats.files_processed,
                "files_skipped": self.stats.files_skipped,
                "rows_read": self.stats.total_rows_read,
                "rows_filtered": self.stats.rows_filtered,
                "duplicates_removed": self.stats.duplicates_removed,
                "rows_written": self.stats.final_row_count,
                "warnings": self.stats.warnings,
                "errors": self.stats.errors,
                "quarantined_rows": self.stats.quarantined_rows,
                "schema_validation": self.stats.schema_validation,
                "quality_profile": self.stats.quality_profile,
                "repair_report": self.stats.repair_report,
                "join_report": self.stats.join_report,
                "merge_report": self.stats.merge_report,
            },
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._temporary_output_path(manifest_path)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(manifest, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, manifest_path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    
    def log(self, message: str, level: str = "info"):
        if self.log_callback:
            self.log_callback(message, level)
    
    def update_progress(self, value: float, status: str):
        if self.progress_callback:
            self.progress_callback(value, status)
    
    def cancel(self):
        self.cancelled = True

    def _source_for_file(self, file_path: Path) -> str:
        if getattr(self.config, "source_value", "name") == "path":
            return str(file_path)
        return file_path.name

    def _add_source_column(self, rows: list[dict], file_path: Path, all_columns=None, column_order=None):
        source_column = getattr(self.config, "source_column", "").strip()
        if not source_column:
            return rows
        if all_columns is not None:
            all_columns.add(source_column)
        if column_order is not None and source_column not in column_order:
            column_order.append(source_column)
        source_value = self._source_for_file(file_path)
        for row in rows:
            row[source_column] = source_value
        return rows

    def _record_input_diagnostic(self, file_path: Path):
        if not self._validate_input_file(file_path):
            return
        if file_path.suffix.lower() in SUPPORTED_TEXT_SUFFIXES:
            details = self._detect_file_details(file_path)
        else:
            details = {
                "encoding": None,
                "delimiter": None,
                "quotechar": None,
                "encoding_confidence": 1.0,
                "delimiter_confidence": 1.0,
            }
        self._input_diagnostics[str(file_path)] = details
        self.stats.input_diagnostics = self._input_diagnostics

    def _record_summary(self, row: dict, columns: list[str]):
        for column in columns:
            value = row.get(column, "")
            text = "" if value is None else str(value)
            self._summary_rows[column] += 1
            if text.strip():
                self._summary_non_empty[column] += 1
                self._summary_distinct[column].add(text)
            self._summary_types[column][self._infer_value_type(text)] += 1

    def _finalize_summary(self, columns: list[str]):
        self.stats.column_summary = {}
        for column in columns:
            values = self._summary_distinct.get(column, set())
            type_counts = self._summary_types.get(column, {})
            non_empty = [kind for kind in type_counts if kind != "empty"]
            inferred = "empty" if not non_empty else (
                non_empty[0] if len(non_empty) == 1 else (
                    "number" if all(kind in {"number", "boolean"} for kind in non_empty)
                    else "text"
                )
            )
            self.stats.column_summary[column] = {
                "row_count": self._summary_rows.get(column, 0),
                "non_empty_count": self._summary_non_empty.get(column, 0),
                "distinct_count": len(values),
                "inferred_type": inferred,
            }

    def _compute_column_summary(self, rows: list[dict], columns: list[str]):
        self._summary_distinct = defaultdict(set)
        self._summary_non_empty = Counter()
        self._summary_rows = Counter()
        self._summary_types = defaultdict(Counter)
        for row in rows:
            self._record_summary(row, columns)
        type_info = self.infer_column_types(rows, columns)
        self.stats.column_summary = {}
        for column in columns:
            summary = type_info.get(column, {})
            self.stats.column_summary[column] = {
                "row_count": self._summary_rows.get(column, 0),
                "non_empty_count": self._summary_non_empty.get(column, 0),
                "distinct_count": len(self._summary_distinct.get(column, set())),
                "inferred_type": summary.get("type", "empty"),
            }
    
    def discover_columns(self, files: list[Path]) -> list[str]:
        """Discover all unique columns across files."""
        all_columns = []
        seen = set()
        per_file_columns = []  # Track columns per file for schema modes

        norm_mode = getattr(self.config, 'header_normalize', 'none')

        for file_path in files:
            try:
                if not self._validate_input_file(file_path):
                    continue
                self._record_input_diagnostic(file_path)
                if file_path.suffix.lower() in SUPPORTED_TEXT_SUFFIXES:
                    encoding, delimiter, quotechar = self._detect_file_params(file_path)
                    with open(file_path, 'r', encoding=encoding, newline='') as f:
                        reader = csv.reader(f, delimiter=delimiter, quotechar=quotechar)
                        headers = next(reader, [])
                        if not self._check_column_limit(file_path, len(headers)):
                            continue
                        file_cols = []
                        for col in headers:
                            col = self._normalize_header(col, norm_mode)
                            if col:
                                file_cols.append(col)
                                if col not in seen:
                                    all_columns.append(col)
                                    seen.add(col)
                        per_file_columns.append(set(file_cols))
                elif file_path.suffix.lower() in SUPPORTED_JSONL_SUFFIXES:
                    file_cols = self._discover_jsonl_columns(file_path)
                    for col in file_cols:
                        if col not in seen:
                            all_columns.append(col)
                            seen.add(col)
                    per_file_columns.append(set(file_cols))
                else:
                    file_cols = self._discover_structured_columns(file_path)
                    for col in file_cols:
                        if col not in seen:
                            all_columns.append(col)
                            seen.add(col)
                    per_file_columns.append(set(file_cols))
            except Exception:
                try:
                    with open(file_path, 'r', encoding='latin-1', newline='') as f:
                        reader = csv.reader(f)
                        headers = next(reader, [])
                        file_cols = []
                        for col in headers:
                            col = self._normalize_header(col, norm_mode)
                            if col and col not in seen:
                                all_columns.append(col)
                                seen.add(col)
                                file_cols.append(col)
                        per_file_columns.append(set(file_cols))
                except Exception:
                    self._record_fatal_input(file_path, "unable to discover input columns")
                    pass

        # Apply schema unification mode
        schema_mode = getattr(self.config, 'schema_mode', 'union')
        if schema_mode == "intersection" and per_file_columns:
            common = per_file_columns[0]
            for s in per_file_columns[1:]:
                common = common & s
            all_columns = [c for c in all_columns if c in common]
        elif schema_mode == "first_file" and per_file_columns:
            first_set = per_file_columns[0]
            all_columns = [c for c in all_columns if c in first_set]

        source_column = getattr(self.config, "source_column", "").strip()
        if source_column and source_column not in all_columns:
            all_columns.append(source_column)

        template = getattr(self.config, "column_template", "").strip()
        if template:
            template_path = Path(template)
            if template_path.exists():
                template_columns = CSVEngine(self.config)._discover_columns_without_source(template_path)
                all_columns = [
                    *[column for column in template_columns if column in all_columns],
                    *[column for column in all_columns if column not in template_columns],
                ]

        return all_columns

    def _discover_columns_without_source(self, file_path: Path) -> list[str]:
        """Discover one file's columns without applying provenance ordering."""
        source_column = self.config.source_column
        column_template = self.config.column_template
        self.config.source_column = ""
        self.config.column_template = ""
        try:
            return self.discover_columns([file_path])
        finally:
            self.config.source_column = source_column
            self.config.column_template = column_template

    def build_schema_report(self, files: list[Path], sample_limit: int = 3) -> dict:
        """Build a machine-readable schema-drift and type-inference report."""
        file_reports = []
        union = []
        union_seen = set()
        for file_path in files:
            probe = CSVEngine(copy.deepcopy(self.config))
            all_columns = set()
            column_order = []
            rows = probe._read_file(file_path, all_columns, column_order)
            columns = list(column_order)
            for column in columns:
                if column not in union_seen:
                    union_seen.add(column)
                    union.append(column)
            file_reports.append({
                "file": str(file_path),
                "columns": columns,
                "row_count": len(rows),
                "samples": rows[:sample_limit],
                "types": self.infer_column_types(rows, columns),
                "diagnostics": probe._input_diagnostics.get(str(file_path), {}),
            })

        for report in file_reports:
            present = set(report["columns"])
            report["missing_columns"] = [column for column in union if column not in present]
            report["extra_columns"] = [
                column for column in report["columns"] if column not in union
            ]

        return {
            "version": 1,
            "files": file_reports,
            "union_columns": union,
            "common_columns": [
                column for column in union
                if all(column in set(report["columns"]) for report in file_reports)
            ] if file_reports else [],
        }

    def write_schema_report(self, files: list[Path], report_path: Path) -> dict:
        report = self.build_schema_report(files)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
        return report

    def sql_query(self, files: list[Path], query: str) -> tuple[list[dict], list[str]]:
        """Run SQL against input files exposed as input_0, input_1, and so on."""
        try:
            duckdb = _optional_module("duckdb")
        except ImportError as exc:
            raise RuntimeError("duckdb is required for SQL queries") from exc

        connection = duckdb.connect(database=":memory:")
        try:
            for index, file_path in enumerate(files):
                escaped_path = str(file_path.resolve()).replace("'", "''")
                suffix = file_path.suffix.lower()
                if suffix == ".parquet":
                    source = f"read_parquet('{escaped_path}')"
                elif suffix in SUPPORTED_JSONL_SUFFIXES:
                    source = f"read_json_auto('{escaped_path}', format='newline_delimited')"
                else:
                    source = f"read_csv_auto('{escaped_path}')"
                connection.execute(
                    f"CREATE OR REPLACE VIEW input_{index} AS SELECT * FROM {source}"
                )

            result = connection.execute(query)
            columns = [description[0] for description in (result.description or [])]
            rows = [
                {column: self._cell_to_text(value) for column, value in zip(columns, values)}
                for values in result.fetchall()
            ]
            return rows, columns
        finally:
            connection.close()

    @staticmethod
    def analyze_join(
        left_rows: list[dict],
        right_rows: list[dict],
        key_columns: list[str],
        join_type: str = "inner",
        key_normalization: str = "trim-casefold",
        conflict_policy: str = "keep-both",
        max_details: int = 1_000,
    ) -> dict:
        return analyze_join(
            left_rows,
            right_rows,
            key_columns,
            join_type,
            key_normalization,
            conflict_policy,
            max_details,
        )

    @staticmethod
    def analyze_three_way(
        base_rows: list[dict],
        ours_rows: list[dict],
        theirs_rows: list[dict],
        key_columns: list[str],
        key_normalization: str = "trim-casefold",
        resolution: str = "fail",
        max_details: int = 1_000,
    ) -> dict:
        return analyze_three_way(
            base_rows,
            ours_rows,
            theirs_rows,
            key_columns,
            key_normalization,
            resolution,
            max_details,
        )

    @staticmethod
    def join_rows(
        left_rows: list[dict],
        right_rows: list[dict],
        key_columns: list[str],
        join_type: str = "inner",
        right_suffix: str = "_right",
        key_normalization: str = "trim-casefold",
        conflict_policy: str = "keep-both",
        return_diagnostics: bool = False,
        max_details: int = 1_000,
    ) -> tuple[list[dict], list[str]]:
        """Join rows while preserving input order and optionally returning diagnostics."""
        return execute_join(
            left_rows,
            right_rows,
            list(key_columns),
            join_type,
            right_suffix,
            key_normalization,
            conflict_policy,
            return_diagnostics,
            max_details,
        )

    @staticmethod
    def three_way_merge_rows(
        base_rows: list[dict],
        ours_rows: list[dict],
        theirs_rows: list[dict],
        key_columns: list[str],
        conflict_resolution: str = "ours",
        key_normalization: str = "trim-casefold",
        return_diagnostics: bool = False,
        max_details: int = 1_000,
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Merge keyed row sets with deterministic, auditable conflict handling."""
        return execute_three_way(
            base_rows,
            ours_rows,
            theirs_rows,
            list(key_columns),
            conflict_resolution,
            key_normalization,
            return_diagnostics,
            max_details,
        )
    
    def process(self, input_files: list[Path], output_file: Path) -> ProcessingStats:
        """Process CSV files according to configuration."""
        self.cancelled = False
        self.stats = ProcessingStats()
        self._validated_inputs = {}
        self._reported_input_issues = {}
        self._quarantine_records = []
        self._input_diagnostics = {}
        self._manifest_input_files = [Path(path) for path in input_files]
        self._schema_reports = []
        self._repair_report = {}

        if self._can_stream(input_files, output_file):
            return self._process_streaming(input_files, output_file)

        all_rows = []
        all_columns = set()
        column_order = []
        per_file_columns = []  # Track columns per file for schema modes

        total_files = len(input_files)

        # Phase 1: Read all files
        self.log("Phase 1: Reading files...", "info")

        for idx, csv_path in enumerate(input_files):
            if self.cancelled:
                self.log("Processing cancelled", "warning")
                self.stats.cancelled = True
                return self.stats

            progress = (idx / total_files) * 40
            self.update_progress(progress, f"Reading {csv_path.name}...")

            rows_from_file = self._read_file(csv_path, all_columns, column_order)
            rows_from_file = self._apply_schema_contract(Path(csv_path), rows_from_file)
            # Track which columns this file contributed
            # We need all columns that appeared in this file's headers
            file_cols = set()
            if rows_from_file:
                file_cols = set(rows_from_file[0].keys())
            per_file_columns.append(file_cols)
            all_rows.extend(rows_from_file)

        self._write_schema_validation_report()
        self._write_quarantine()
        if self.stats.fatal_input_errors:
            self.log("Input validation failed; no output was written", "error")
            return self.stats

        all_rows = self._apply_reviewed_repairs(all_rows)
        self._write_repair_report()
        if self.stats.errors:
            self.log("Quality repair failed; no output was written", "error")
            return self.stats

        if not all_rows:
            self.log("No data to process", "warning")
            return self.stats

        self.stats.unique_columns = len(all_columns)

        # Apply schema unification before determining final columns
        schema_mode = getattr(self.config, 'schema_mode', 'union')
        if schema_mode == "intersection" and per_file_columns:
            common = per_file_columns[0]
            for s in per_file_columns[1:]:
                common = common & s
            column_order = [c for c in column_order if c in common]
        elif schema_mode == "first_file" and per_file_columns:
            first_set = per_file_columns[0]
            column_order = [c for c in column_order if c in first_set]

        # Determine final columns
        final_columns = self._get_final_columns(column_order)
        final_columns = self._with_transform_columns(final_columns)
        
        # Phase 2: Apply filters
        if self.config.filters and not self.cancelled:
            self.update_progress(45, "Applying filters...")
            self.log(f"Phase 2: Applying {len(self.config.filters)} filter(s)...", "info")
            all_rows = self._apply_filters(all_rows)
        
        # Phase 3: Apply transformations
        if not self.cancelled:
            self.update_progress(55, "Applying transformations...")
            self.log("Phase 3: Applying transformations...", "info")
            all_rows = self._apply_transformations(all_rows, final_columns)

        # Phase 3b: Optional pivot/unpivot reshaping
        if not self.cancelled and (
            getattr(self.config, "unpivot_columns", None)
            or getattr(self.config, "pivot_column", "")
        ):
            self.update_progress(60, "Reshaping data...")
            self.log("Phase 3b: Reshaping data...", "info")
            all_rows, final_columns = self._apply_reshape(all_rows, final_columns)
            self.stats.unique_columns = len(final_columns)
        
        # Phase 4: Deduplicate
        if self.config.dedupe_enabled and not self.cancelled:
            self.update_progress(65, "Removing duplicates...")
            self.log("Phase 4: Removing duplicates...", "info")
            all_rows = self._deduplicate(all_rows, final_columns)
        
        # Phase 5: Sort
        if self.config.sort_enabled and self.config.sort_columns and not self.cancelled:
            self.update_progress(80, "Sorting data...")
            self.log("Phase 5: Sorting data...", "info")
            all_rows = self._sort_rows(all_rows, final_columns)

        all_rows = [self._redact_row(row) for row in all_rows]
        output_columns = [self.config.column_mapping.get(c, c) for c in final_columns]
        self._compute_column_summary(all_rows, output_columns)
        
        # Phase 6: Write output
        self.stats.final_row_count = len(all_rows)
        if not self.cancelled:
            self.update_progress(90, "Writing output file...")
            self.log("Phase 6: Writing output...", "info")
            self._write_output(all_rows, final_columns, output_file)
        else:
            self.stats.cancelled = True

        self.update_progress(100, "Complete!")
        
        return self.stats

    def _can_stream(self, input_files: list[Path], output_file: Path) -> bool:
        if not getattr(self.config, "streaming_enabled", True):
            return False
        if getattr(self.config, "engine_backend", "auto") == "polars":
            return False
        if self.config.dedupe_enabled or self.config.sort_enabled:
            return False
        if getattr(self.config, "unpivot_columns", None) or getattr(self.config, "pivot_column", ""):
            return False
        if getattr(self.config, "schema_contract", None):
            return False
        if getattr(self.config, "repair_edits", None):
            return False
        if output_file.suffix.lower() not in SUPPORTED_STREAM_OUTPUT_SUFFIXES:
            return False
        try:
            output_resolved = output_file.resolve()
            if any(path.resolve() == output_resolved for path in input_files):
                return False
        except OSError:
            return False
        return all(path.suffix.lower() in SUPPORTED_STREAM_INPUT_SUFFIXES for path in input_files)

    def _iter_parquet_rows(self, file_path: Path, batch_size: int | None = None):
        """Yield Parquet rows in bounded Arrow batches.

        The normal structured reader is intentionally retained for transforms
        that require a full in-memory table.  The streaming lane uses Arrow's
        batch iterator so large Parquet inputs never become one Python list.
        """
        if not self._validate_input_file(file_path):
            self.stats.files_skipped += 1
            return

        norm_mode = getattr(self.config, "header_normalize", "none")
        row_count = 0
        try:
            pq = _optional_module("pyarrow.parquet")
            parquet_file = pq.ParquetFile(file_path)
            metadata = parquet_file.metadata
            if not self._check_column_limit(file_path, metadata.num_columns):
                return
            if not self._check_row_limit(file_path, metadata.num_rows):
                return
            batch_size = max(
                1,
                int(batch_size or getattr(self.config, "stream_batch_rows", 2_048)),
            )
            for batch in parquet_file.iter_batches(batch_size=batch_size):
                for record in batch.to_pylist():
                    row_number = row_count + 1
                    cleaned = {}
                    for key, value in record.items():
                        normalized = self._normalize_header(str(key), norm_mode)
                        if not normalized:
                            continue
                        if not self._check_cell_limit(file_path, value, normalized, row_number):
                            return
                        text_value = self._cell_to_text(value)
                        cleaned[normalized] = (
                            text_value.strip() if self.config.trim_whitespace else text_value
                        )
                    if self.config.source_column:
                        cleaned[self.config.source_column] = self._source_for_file(file_path)
                    row_count += 1
                    yield cleaned
        except ImportError as exc:
            self.stats.files_skipped += 1
            self._record_fatal_input(
                file_path,
                "pyarrow is required for bounded Parquet streaming",
            )
            self.log(f"XX Error reading {file_path.name}: {exc}", "error")
            return
        except Exception as exc:
            self.stats.files_skipped += 1
            self._record_fatal_input(file_path, f"error reading Parquet: {exc}")
            self.log(f"XX Error reading {file_path.name}: {exc}", "error")
            return

        self.stats.files_processed += 1
        self.stats.total_rows_read += row_count
        self.log(f"OK {file_path.name} ({row_count:,} Parquet rows, batched)", "success")

    def _iter_xlsx_rows(self, file_path: Path):
        """Yield read-only workbook rows for bounded preview scans."""
        if not self._validate_input_file(file_path):
            self.stats.files_skipped += 1
            return

        norm_mode = getattr(self.config, "header_normalize", "none")
        row_count = 0
        workbook = None
        try:
            load_workbook = _optional_module("openpyxl").load_workbook
            workbook = load_workbook(file_path, read_only=True, data_only=True)
            sheet = workbook.active
            row_iter = sheet.iter_rows(values_only=True)
            raw_headers = next(row_iter, [])
            if not self._check_column_limit(file_path, len(raw_headers)):
                return
            headers = [
                self._normalize_header(self._cell_to_text(header), norm_mode)
                for header in raw_headers
            ]
            for row_number, values in enumerate(row_iter, 2):
                if not self._check_row_limit(file_path, row_count + 1, row_number):
                    return
                cleaned = {}
                for index, header in enumerate(headers):
                    if not header:
                        continue
                    value = values[index] if index < len(values) else ""
                    if not self._check_cell_limit(file_path, value, header, row_number):
                        return
                    text_value = self._cell_to_text(value)
                    cleaned[header] = (
                        text_value.strip() if self.config.trim_whitespace else text_value
                    )
                if self.config.source_column:
                    cleaned[self.config.source_column] = self._source_for_file(file_path)
                row_count += 1
                yield cleaned
        except Exception as exc:
            self.stats.files_skipped += 1
            self._record_fatal_input(file_path, f"error reading workbook: {exc}")
            self.log(f"XX Error reading {file_path.name}: {exc}", "error")
            return
        finally:
            if workbook is not None:
                workbook.close()

        self.stats.files_processed += 1
        self.stats.total_rows_read += row_count
        self.log(f"OK {file_path.name} ({row_count:,} workbook rows, read-only)", "success")

    def _iter_jsonl_rows(self, file_path: Path):
        if not self._validate_input_file(file_path):
            self.stats.files_skipped += 1
            return

        norm_mode = getattr(self.config, "header_normalize", "none")
        row_count = 0
        try:
            with open(file_path, "r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except (json.JSONDecodeError, RecursionError) as exc:
                        action = self._record_invalid_row(file_path, str(exc), line_number, line)
                        if action == "stop":
                            break
                        continue
                    if not isinstance(value, dict):
                        action = self._record_invalid_row(
                            file_path, "expected a JSON object", line_number, line
                        )
                        if action == "stop":
                            break
                        continue
                    if not self._check_json_value(file_path, value, line_number):
                        break
                    if not self._check_column_limit(file_path, len(value)):
                        break
                    if not self._check_row_limit(file_path, row_count + 1, line_number):
                        break
                    row = {}
                    for key, raw_value in value.items():
                        normalized_key = self._normalize_header(str(key), norm_mode)
                        if normalized_key:
                            text_value = self._cell_to_text(raw_value)
                            row[normalized_key] = text_value.strip() if self.config.trim_whitespace else text_value
                    if self.config.source_column:
                        row[self.config.source_column] = self._source_for_file(file_path)
                    row_count += 1
                    yield row
        except (OSError, UnicodeDecodeError) as exc:
            self.stats.files_skipped += 1
            self._record_fatal_input(file_path, str(exc))
            return

        self.stats.files_processed += 1
        self.stats.total_rows_read += row_count
        self.log(f"OK {file_path.name} ({row_count:,} JSON rows)", "success")

    def _iter_text_rows(self, file_path: Path):
        if not self._validate_input_file(file_path):
            self.stats.files_skipped += 1
            return

        norm_mode = getattr(self.config, 'header_normalize', 'none')
        detected_enc, detected_delim, detected_quote = self._detect_file_params(file_path)
        encodings_to_try = [detected_enc]
        for fallback in ['utf-8', 'latin-1', 'cp1252', 'utf-16']:
            if fallback not in encodings_to_try:
                encodings_to_try.append(fallback)

        for encoding in encodings_to_try:
            try:
                with open(file_path, 'r', encoding=encoding, newline='') as f:
                    reader = csv.DictReader(f, delimiter=detected_delim, quotechar=detected_quote)
                    if reader.fieldnames and not self._check_column_limit(file_path, len(reader.fieldnames)):
                        return
                    row_count = 0
                    for row in reader:
                        line_number = reader.line_num
                        if not self._check_row_limit(file_path, row_count + 1, line_number):
                            break
                        if None in row or any(value is None for key, value in row.items() if key is not None):
                            action = self._record_invalid_row(
                                file_path,
                                "ragged row has missing or extra fields",
                                line_number,
                                repr(row),
                            )
                            if action == "stop":
                                break
                            if action == "skip":
                                continue
                        cleaned_row = {}
                        valid_row = True
                        for key, value in row.items():
                            if key is None:
                                continue
                            normalized_key = self._normalize_header(key, norm_mode) if key else ""
                            if normalized_key:
                                text_value = value or ""
                                if not self._check_cell_limit(file_path, text_value, normalized_key, line_number):
                                    valid_row = False
                                    break
                                cleaned_row[normalized_key] = text_value.strip() if self.config.trim_whitespace else text_value
                        if not valid_row:
                            break
                        if self.config.source_column:
                            cleaned_row[self.config.source_column] = self._source_for_file(file_path)
                        row_count += 1
                        yield cleaned_row
                    self.stats.files_processed += 1
                    self.stats.total_rows_read += row_count
                    self.log(f"OK {file_path.name} ({row_count:,} rows, {encoding}, delim={repr(detected_delim)})", "success")
                    return
            except UnicodeDecodeError:
                continue
            except Exception as exc:
                if encoding == encodings_to_try[-1]:
                    self.log(f"XX Error reading {file_path.name}: {exc}", "error")
                    self.stats.files_skipped += 1
                    self._record_fatal_input(file_path, str(exc))
                    return
                continue

        self.stats.files_skipped += 1
        self._record_fatal_input(file_path, "unable to decode input with supported encodings")

    def _process_streaming(self, input_files: list[Path], output_file: Path) -> ProcessingStats:
        self.log("Phase 1: Streaming rows...", "info")
        discovered_order = self.discover_columns(input_files)
        if self.stats.fatal_input_errors:
            self._write_quarantine()
            self.log("Input validation failed; no output was written", "error")
            return self.stats
        final_columns = self._with_transform_columns(self._get_final_columns(discovered_order))
        output_columns = [self.config.column_mapping.get(c, c) for c in final_columns]
        self.stats.unique_columns = len(discovered_order)

        is_jsonl = output_file.suffix.lower() in SUPPORTED_JSONL_SUFFIXES
        is_parquet = output_file.suffix.lower() == ".parquet"
        quoting_map = {
            "minimal": csv.QUOTE_MINIMAL,
            "all": csv.QUOTE_ALL,
            "nonnumeric": csv.QUOTE_NONNUMERIC,
            "none": csv.QUOTE_NONE,
        }
        quoting = quoting_map.get(self.config.output_quoting, csv.QUOTE_MINIMAL)
        newline = "\n" if self.config.line_ending == "unix" else "\r\n" if self.config.line_ending == "windows" else ""

        temporary = None
        handle = None
        parquet_writer = None
        parquet_rows = []
        parquet_batch_size = max(1, int(getattr(self.config, "stream_batch_rows", 2_048)))

        def flush_parquet_rows():
            nonlocal parquet_rows
            if not parquet_rows:
                return
            pa = _optional_module("pyarrow")
            table = pa.table({
                column: pa.array(
                    [row.get(column, "") for row in parquet_rows],
                    type=pa.string(),
                )
                for column in output_columns
            })
            parquet_writer.write_table(table)
            parquet_rows = []

        try:
            temporary = self._temporary_output_path(output_file)
            writer = None
            if is_parquet:
                pa = _optional_module("pyarrow")
                pq = _optional_module("pyarrow.parquet")
                schema = pa.schema([pa.field(column, pa.string()) for column in output_columns])
                parquet_writer = pq.ParquetWriter(str(temporary), schema)
            else:
                handle = open(
                    temporary,
                    "w",
                    encoding=self.config.output_encoding,
                    newline=newline if newline else "",
                )
                if not is_jsonl:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=output_columns,
                        delimiter=self.config.output_delimiter,
                        quoting=quoting,
                        extrasaction='ignore',
                    )
                    if self.config.include_header:
                        writer.writeheader()

            total_files = len(input_files)
            for idx, csv_path in enumerate(input_files):
                if self.cancelled:
                    raise ProcessingCancelled()

                progress = (idx / total_files) * 90
                self.update_progress(progress, f"Streaming {csv_path.name}...")

                suffix = csv_path.suffix.lower()
                if suffix in SUPPORTED_JSONL_SUFFIXES:
                    iterator = self._iter_jsonl_rows(csv_path)
                elif suffix == ".parquet":
                    iterator = self._iter_parquet_rows(csv_path)
                else:
                    iterator = self._iter_text_rows(csv_path)
                for row in iterator:
                    if self.cancelled:
                        raise ProcessingCancelled()
                    if self.config.filters and not self._row_matches_filters(row):
                        self.stats.rows_filtered += 1
                        continue

                    transformed = self._apply_transformations([row], final_columns)[0]
                    transformed = self._redact_row(transformed)
                    if is_parquet:
                        parquet_rows.append({
                            column: transformed.get(column, "")
                            for column in output_columns
                        })
                        if len(parquet_rows) >= parquet_batch_size:
                            flush_parquet_rows()
                    elif is_jsonl:
                        json.dump(
                            {column: transformed.get(column, "") for column in output_columns},
                            handle,
                            ensure_ascii=False,
                        )
                        handle.write("\n")
                    else:
                        writer.writerow(transformed)
                    self._record_summary(transformed, output_columns)
                    self.stats.final_row_count += 1

            self._write_quarantine()
            if self.stats.fatal_input_errors or self.stats.errors:
                raise RuntimeError("input validation failed; output was not replaced")
            if is_parquet:
                flush_parquet_rows()
                parquet_writer.close()
                parquet_writer = None
            else:
                handle.flush()
                os.fsync(handle.fileno())
                handle.close()
                handle = None

            self._fsync_file(temporary)
            backup_path = self._commit_output(temporary, output_file)
            temporary = None
            self._finalize_summary(output_columns)
            self._write_run_manifest(output_file, output_columns, backup_path)
            self.update_progress(100, "Complete!")
            self.log(f"OK Saved: {output_file.name} ({self.stats.final_row_count:,} rows)", "success")
        except ProcessingCancelled:
            self.stats.cancelled = True
            self.log("Processing cancelled; output was not replaced", "warning")
        except Exception as exc:
            message = f"Write error: {exc}"
            self.stats.errors.append(message)
            self.log(f"XX {message}", "error")
        finally:
            if handle is not None:
                handle.close()
            if parquet_writer is not None:
                parquet_writer.close()
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
        return self.stats

    def _preview_columns_for_file(self, file_path: Path, row_limit: int) -> list[str]:
        suffix = file_path.suffix.lower()
        norm_mode = getattr(self.config, "header_normalize", "none")
        if suffix in SUPPORTED_TEXT_SUFFIXES:
            encoding, delimiter, quotechar = self._detect_file_params(file_path)
            with open(file_path, "r", encoding=encoding, newline="") as handle:
                headers = next(csv.reader(handle, delimiter=delimiter, quotechar=quotechar), [])
            return [
                normalized
                for header in headers
                if (normalized := self._normalize_header(header, norm_mode))
            ]
        if suffix in SUPPORTED_JSONL_SUFFIXES:
            columns = []
            seen = set()
            for index, row in enumerate(self._iter_jsonl_rows(file_path)):
                if index >= row_limit:
                    break
                for key in row:
                    normalized = self._normalize_header(str(key), norm_mode)
                    if normalized and normalized not in seen:
                        seen.add(normalized)
                        columns.append(normalized)
            return columns
        return self._discover_structured_columns(file_path)

    def discover_columns_bounded(self, files: list[Path], row_limit: int = 256) -> list[str]:
        """Discover columns without scanning an entire JSONL or table input."""
        all_columns = []
        seen = set()
        per_file_columns = []
        for raw_path in files:
            file_path = Path(raw_path)
            try:
                if not self._validate_input_file(file_path):
                    continue
                file_columns = self._preview_columns_for_file(file_path, max(1, int(row_limit)))
            except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as exc:
                self._record_fatal_input(file_path, f"unable to discover bounded columns: {exc}")
                continue
            per_file_columns.append(set(file_columns))
            for column in file_columns:
                if column not in seen:
                    seen.add(column)
                    all_columns.append(column)

        schema_mode = getattr(self.config, "schema_mode", "union")
        if schema_mode == "intersection" and per_file_columns:
            common = per_file_columns[0]
            for columns in per_file_columns[1:]:
                common &= columns
            all_columns = [column for column in all_columns if column in common]
        elif schema_mode == "first_file" and per_file_columns:
            first = per_file_columns[0]
            all_columns = [column for column in all_columns if column in first]

        source_column = getattr(self.config, "source_column", "").strip()
        if source_column and source_column not in all_columns:
            all_columns.append(source_column)
        return all_columns

    def _preview_row_iterator(self, file_path: Path, batch_size: int | None = None):
        suffix = file_path.suffix.lower()
        if suffix in SUPPORTED_JSONL_SUFFIXES:
            return self._iter_jsonl_rows(file_path)
        if suffix == ".parquet":
            return self._iter_parquet_rows(file_path, batch_size=batch_size)
        if suffix == ".xlsx":
            return self._iter_xlsx_rows(file_path)
        if suffix in SUPPORTED_TEXT_SUFFIXES:
            return self._iter_text_rows(file_path)
        raise ValueError(f"Unsupported preview input type: {suffix or '(none)'}")

    def _clip_preview_row(self, row: dict, budget: PreviewBudget) -> tuple[dict, int, bool, bool]:
        clipped = {}
        byte_count = 0
        cell_truncated = False
        column_truncated = len(row) > budget.column_limit
        for index, (column, value) in enumerate(row.items()):
            if index >= budget.column_limit:
                break
            text = self._cell_to_text(value)
            encoded = text.encode("utf-8")
            if len(encoded) > budget.cell_byte_limit:
                text = encoded[:budget.cell_byte_limit].decode("utf-8", errors="ignore")
                text += "…"
                cell_truncated = True
            clipped[column] = text
            byte_count += len(text.encode("utf-8"))
        return clipped, byte_count, cell_truncated, column_truncated

    def preview(
        self,
        input_files: list[Path],
        limit: int = 100,
        budget: PreviewBudget | None = None,
    ) -> dict:
        """Return a cancellable, read-only projected sample under hard budgets."""
        requested_limit = max(1, int(limit))
        base_budget = budget or PreviewBudget(row_limit=requested_limit)
        preview_budget = PreviewBudget(
            row_limit=min(requested_limit, max(1, int(base_budget.row_limit))),
            scan_row_limit=max(1, int(base_budget.scan_row_limit)),
            scan_byte_limit=max(1, int(base_budget.scan_byte_limit)),
            column_limit=max(1, int(base_budget.column_limit)),
            cell_byte_limit=max(1, int(base_budget.cell_byte_limit)),
        )
        self.cancelled = False
        self.stats = ProcessingStats()
        self._schema_reports = []
        all_rows = []
        all_columns = set()
        column_order = []
        per_file_columns = []
        file_reports = []
        rows_scanned = 0
        bytes_scanned = 0
        files_scanned = 0
        scan_truncated = False
        cells_truncated = False
        columns_truncated = False
        started = time.perf_counter()

        for file_index, raw_path in enumerate(input_files):
            file_path = Path(raw_path)
            if self.cancelled:
                break
            try:
                file_columns = self._preview_columns_for_file(
                    file_path,
                    min(preview_budget.scan_row_limit, 256),
                )
            except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as exc:
                self._record_fatal_input(file_path, f"unable to preview columns: {exc}")
                file_columns = []
            per_file_columns.append(set(file_columns))
            for column in file_columns:
                if column not in all_columns:
                    all_columns.add(column)
                    column_order.append(column)

            file_rows = 0
            file_bytes = 0
            if rows_scanned < preview_budget.scan_row_limit and not self.cancelled:
                try:
                    iterator = self._preview_row_iterator(
                        file_path,
                        batch_size=min(
                            preview_budget.scan_row_limit - rows_scanned,
                            int(getattr(self.config, "stream_batch_rows", 2_048)),
                        ),
                    )
                    for row in iterator:
                        if self.cancelled:
                            break
                        if rows_scanned >= preview_budget.scan_row_limit:
                            scan_truncated = True
                            break
                        clipped, row_bytes, cell_cut, column_cut = self._clip_preview_row(
                            row, preview_budget
                        )
                        if bytes_scanned + row_bytes > preview_budget.scan_byte_limit:
                            scan_truncated = True
                            break
                        all_rows.append(clipped)
                        rows_scanned += 1
                        file_rows += 1
                        bytes_scanned += row_bytes
                        file_bytes += row_bytes
                        cells_truncated = cells_truncated or cell_cut
                        columns_truncated = columns_truncated or column_cut
                        if rows_scanned % 256 == 0:
                            self.update_progress(
                                ((file_index + 0.5) / max(1, len(input_files))) * 100,
                                f"Preview scanning {file_path.name} ({rows_scanned:,} row(s))",
                            )
                    else:
                        files_scanned += 1
                except (OSError, RuntimeError, ValueError, UnicodeDecodeError) as exc:
                    self._record_fatal_input(file_path, f"preview scan failed: {exc}")
            elif rows_scanned >= preview_budget.scan_row_limit:
                scan_truncated = True
            file_reports.append({
                "file": str(file_path),
                "rows_scanned": file_rows,
                "bytes_scanned": file_bytes,
                "columns": file_columns[:preview_budget.column_limit],
            })
            self.update_progress(
                ((file_index + 1) / max(1, len(input_files))) * 100,
                f"Preview scanned {file_path.name} ({rows_scanned:,} row(s))",
            )

        if len(column_order) > preview_budget.column_limit:
            column_order = column_order[:preview_budget.column_limit]
            columns_truncated = True

        source_column = getattr(self.config, "source_column", "").strip()
        if source_column and source_column not in column_order and len(column_order) < preview_budget.column_limit:
            column_order.append(source_column)

        schema_mode = getattr(self.config, "schema_mode", "union")
        if schema_mode == "intersection" and per_file_columns:
            common = per_file_columns[0]
            for columns in per_file_columns[1:]:
                common &= columns
            column_order = [column for column in column_order if column in common]
        elif schema_mode == "first_file" and per_file_columns:
            first = per_file_columns[0]
            column_order = [column for column in column_order if column in first]

        final_columns = self._with_transform_columns(self._get_final_columns(column_order))
        if self.config.filters and not self.cancelled:
            all_rows = self._apply_filters(all_rows)
        if not self.cancelled:
            all_rows = self._apply_transformations(all_rows, final_columns)
        if not self.cancelled and (
            getattr(self.config, "unpivot_columns", None)
            or getattr(self.config, "pivot_column", "")
        ):
            all_rows, final_columns = self._apply_reshape(all_rows, final_columns)
        if self.config.dedupe_enabled and not self.cancelled:
            all_rows = self._deduplicate(all_rows, final_columns)
        if self.config.sort_enabled and self.config.sort_columns and not self.cancelled:
            all_rows = self._sort_rows(all_rows, final_columns)
        all_rows = [self._redact_row(row) for row in all_rows]
        output_columns = [self.config.column_mapping.get(c, c) for c in final_columns]
        if len(all_rows) > preview_budget.row_limit:
            scan_truncated = True
        all_rows = all_rows[:preview_budget.row_limit]

        self.stats.files_processed = files_scanned
        self.stats.total_rows_read = rows_scanned
        self.stats.unique_columns = len(output_columns)
        self.stats.final_row_count = len(all_rows)
        self._compute_column_summary(all_rows, output_columns)
        self.update_progress(100, "Preview ready (read-only sample)")
        return {
            "columns": output_columns,
            "rows": all_rows,
            "stats": self.stats,
            "mode": "read-only",
            "bounded": True,
            "metadata": {
                "mode": "read-only",
                "scan_row_limit": preview_budget.scan_row_limit,
                "row_limit": preview_budget.row_limit,
                "scan_byte_limit": preview_budget.scan_byte_limit,
                "column_limit": preview_budget.column_limit,
                "cell_byte_limit": preview_budget.cell_byte_limit,
                "rows_scanned": rows_scanned,
                "bytes_scanned": bytes_scanned,
                "scan_truncated": scan_truncated,
                "cells_truncated": cells_truncated,
                "columns_truncated": columns_truncated,
                "cancelled": self.cancelled,
                "remaining_rows": None if scan_truncated else 0,
                "files_scanned": files_scanned,
                "files_remaining": max(0, len(input_files) - files_scanned),
                "files": file_reports,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        }
    
    def _should_use_polars(self, file_path: Path) -> bool:
        backend = getattr(self.config, "engine_backend", "auto")
        if backend == "python" or file_path.suffix.lower() not in SUPPORTED_TEXT_SUFFIXES:
            return False
        if backend == "polars":
            return True
        try:
            return file_path.stat().st_size >= int(getattr(self.config, "polars_threshold_bytes", 5_000_000))
        except OSError:
            return False

    def _read_text_file_polars(self, file_path: Path, all_columns: set, column_order: list):
        """Read a UTF text input through Polars while preserving string semantics."""
        try:
            pl = _optional_module("polars")
        except ImportError:
            if getattr(self.config, "engine_backend", "auto") == "polars":
                raise RuntimeError("polars is required when engine_backend='polars'")
            return None

        details = self._detect_file_details(file_path)
        encoding = details["encoding"].lower().replace("-", "")
        if encoding not in {"utf8", "utf8sig", "ascii"}:
            return None
        norm_mode = getattr(self.config, "header_normalize", "none")
        try:
            frame = pl.read_csv(
                file_path,
                separator=details["delimiter"],
                quote_char=details["quotechar"],
                has_header=True,
                infer_schema=False,
                try_parse_dates=False,
                null_values=[],
                encoding="utf8",
            )
        except Exception:
            if getattr(self.config, "engine_backend", "auto") == "polars":
                raise
            return None

        rows = []
        for column in frame.columns:
            normalized = self._normalize_header(column, norm_mode)
            if normalized and normalized not in all_columns:
                all_columns.add(normalized)
                column_order.append(normalized)
        if not self._check_column_limit(file_path, len(frame.columns)):
            return []
        if not self._check_row_limit(file_path, frame.height):
            return []
        for row_number, record in enumerate(frame.iter_rows(named=True), 1):
            cleaned = {}
            for raw_column, value in record.items():
                normalized = self._normalize_header(raw_column, norm_mode)
                if normalized:
                    if not self._check_cell_limit(file_path, value, normalized, row_number):
                        return []
                    text_value = self._cell_to_text(value)
                    cleaned[normalized] = text_value.strip() if self.config.trim_whitespace else text_value
            rows.append(cleaned)

        self.stats.files_processed += 1
        self.stats.total_rows_read += len(rows)
        self.log(
            f"OK {file_path.name} ({len(rows):,} rows, polars, delim={repr(details['delimiter'])})",
            "success",
        )
        return self._add_source_column(rows, file_path, all_columns, column_order)

    def _read_file(self, file_path: Path, all_columns: set, column_order: list) -> list[dict]:
        """Read a single CSV file."""
        rows = []

        if not self._validate_input_file(file_path):
            self.stats.files_skipped += 1
            return rows

        self._record_input_diagnostic(file_path)

        if file_path.suffix.lower() in SUPPORTED_JSONL_SUFFIXES:
            rows = self._read_jsonl_file(file_path, all_columns, column_order)
            return self._add_source_column(rows, file_path, all_columns, column_order)

        if file_path.suffix.lower() not in SUPPORTED_TEXT_SUFFIXES:
            rows = self._read_structured_file(file_path, all_columns, column_order)
            return self._add_source_column(rows, file_path, all_columns, column_order)

        if self._should_use_polars(file_path):
            polars_rows = self._read_text_file_polars(file_path, all_columns, column_order)
            if polars_rows is not None:
                return polars_rows

        norm_mode = getattr(self.config, 'header_normalize', 'none')

        # Auto-detect encoding, delimiter, quotechar
        detected_enc, detected_delim, detected_quote = self._detect_file_params(file_path)
        encodings_to_try = [detected_enc]
        for fallback in ['utf-8', 'latin-1', 'cp1252', 'utf-16']:
            if fallback not in encodings_to_try:
                encodings_to_try.append(fallback)

        for encoding in encodings_to_try:
            try:
                with open(file_path, 'r', encoding=encoding, newline='') as f:
                    reader = csv.DictReader(f, delimiter=detected_delim,
                                            quotechar=detected_quote)

                    if reader.fieldnames:
                        if not self._check_column_limit(file_path, len(reader.fieldnames)):
                            return rows
                        for col in reader.fieldnames:
                            if not self._check_cell_limit(file_path, col, line_number=1):
                                return rows
                            col = self._normalize_header(col, norm_mode)
                            if col and col not in all_columns:
                                all_columns.add(col)
                                column_order.append(col)

                    row_count = 0
                    for row in reader:
                        line_number = reader.line_num
                        if not self._check_row_limit(file_path, row_count + 1, line_number):
                            break
                        if None in row or any(value is None for key, value in row.items() if key is not None):
                            action = self._record_invalid_row(
                                file_path,
                                "ragged row has missing or extra fields",
                                line_number,
                                repr(row),
                            )
                            if action == "stop":
                                break
                            if action == "skip":
                                continue
                        cleaned_row = {}
                        valid_row = True
                        for k, v in row.items():
                            if k is None:
                                continue
                            key = self._normalize_header(k, norm_mode) if k else ""
                            if key:
                                if not self._check_cell_limit(file_path, v or "", key, line_number):
                                    valid_row = False
                                    break
                                cleaned_row[key] = v.strip() if v and self.config.trim_whitespace else (v or "")
                        if not valid_row:
                            break
                        rows.append(cleaned_row)
                        row_count += 1

                    self.stats.files_processed += 1
                    self.stats.total_rows_read += row_count
                    self.log(f"✓ {file_path.name} ({row_count:,} rows, {encoding}, delim={repr(detected_delim)})", "success")
                    return self._add_source_column(rows, file_path, all_columns, column_order)

            except UnicodeDecodeError:
                continue
            except Exception as e:
                if encoding == encodings_to_try[-1]:
                    self.log(f"✗ Error reading {file_path.name}: {e}", "error")
                    self.stats.files_skipped += 1
                    self._record_fatal_input(file_path, str(e))
                    return rows
                continue

        self.stats.files_skipped += 1
        self._record_fatal_input(file_path, "unable to decode input with supported encodings")
        return rows
    
    def _get_final_columns(self, discovered_order: list) -> list[str]:
        """Determine final column list based on configuration."""
        if self.config.columns_mode == "all":
            columns = discovered_order
        elif self.config.columns_mode == "select":
            columns = [c for c in discovered_order if c in self.config.selected_columns]
        elif self.config.columns_mode == "exclude":
            columns = [c for c in discovered_order if c not in self.config.selected_columns]
        else:
            columns = discovered_order

        if self.config.column_order:
            preferred = [column for column in self.config.column_order if column in columns]
            columns = preferred + [column for column in columns if column not in preferred]
        
        # Apply column mapping for display names
        return columns

    def _with_transform_columns(self, columns: list[str]) -> list[str]:
        """Add output columns created by split, merge, and compute transforms."""
        result = list(columns)
        for ct in getattr(self.config, 'column_transforms', []):
            if len(ct) >= 3 and ct[1] == "split":
                num_parts = int(ct[3]) if len(ct) > 3 else 2
                for i in range(1, num_parts + 1):
                    new_col = f"{ct[0]}_{i}"
                    if new_col not in result:
                        result.append(new_col)
            elif len(ct) >= 4 and ct[1] == "merge":
                new_col = ct[0]
                if new_col not in result:
                    result.append(new_col)
            elif len(ct) >= 3 and ct[1] == "compute":
                new_col = ct[0]
                if new_col not in result:
                    result.append(new_col)
        return result

    def _mapped_value(self, row: dict, column: str):
        mapped = self.config.column_mapping.get(column, column)
        return row.get(mapped, row.get(column, ""))

    def _apply_reshape(self, rows: list[dict], columns: list[str]) -> tuple[list[dict], list[str]]:
        """Apply optional long-form unpivoting and wide-form pivoting."""
        current_rows = rows
        current_columns = list(columns)

        unpivot_columns = [
            column for column in getattr(self.config, "unpivot_columns", [])
            if column in current_columns
        ]
        if unpivot_columns:
            index_columns = [column for column in current_columns if column not in unpivot_columns]
            name_column = getattr(self.config, "unpivot_name_column", "variable") or "variable"
            value_column = getattr(self.config, "unpivot_value_column", "value") or "value"
            reshaped = []
            for row in current_rows:
                for source_column in unpivot_columns:
                    new_row = {
                        self.config.column_mapping.get(column, column): self._mapped_value(row, column)
                        for column in index_columns
                    }
                    new_row[name_column] = source_column
                    new_row[value_column] = self._mapped_value(row, source_column)
                    reshaped.append(new_row)
            current_rows = reshaped
            current_columns = index_columns + [name_column, value_column]

        pivot_column = getattr(self.config, "pivot_column", "")
        pivot_value_column = getattr(self.config, "pivot_value_column", "")
        if pivot_column and pivot_value_column and pivot_column in current_columns and pivot_value_column in current_columns:
            index_columns = [
                column for column in getattr(self.config, "pivot_index_columns", [])
                if column in current_columns and column not in {pivot_column, pivot_value_column}
            ]
            if not index_columns:
                index_columns = [
                    column for column in current_columns
                    if column not in {pivot_column, pivot_value_column}
                ]

            groups = {}
            pivot_values = []
            for row in current_rows:
                key = tuple(self._mapped_value(row, column) for column in index_columns)
                if key not in groups:
                    groups[key] = {
                        self.config.column_mapping.get(column, column): value
                        for column, value in zip(index_columns, key)
                    }
                pivot_value = str(self._mapped_value(row, pivot_column))
                if pivot_value not in pivot_values:
                    pivot_values.append(pivot_value)
                target_column = pivot_value
                incoming = self._mapped_value(row, pivot_value_column)
                target_row = groups[key]
                if target_column not in target_row:
                    target_row[target_column] = incoming
                else:
                    mode = getattr(self.config, "pivot_aggregate", "first")
                    if mode != "first":
                        target_row[target_column] = self._aggregate_value(
                            target_row[target_column],
                            incoming,
                            mode,
                            getattr(self.config, "pivot_separator", "; "),
                        )

            current_rows = []
            output_columns = [*index_columns, *pivot_values]
            for group in groups.values():
                current_rows.append({
                    self.config.column_mapping.get(column, column): group.get(
                        self.config.column_mapping.get(column, column), ""
                    )
                    for column in output_columns
                })
            current_columns = output_columns

        return current_rows, current_columns

    def _apply_filters(self, rows: list[dict]) -> list[dict]:
        """Apply configured filters to rows."""
        if not self.config.filters:
            return rows
        
        filtered = []
        original_count = len(rows)
        
        for row in rows:
            if self._row_matches_filters(row):
                filtered.append(row)
        
        self.stats.rows_filtered = original_count - len(filtered)
        self.log(f"  Filtered out {self.stats.rows_filtered:,} rows", "info")
        
        return filtered

    def _row_matches_filters(self, row: dict) -> bool:
        results = []
        for col, operator, value in self.config.filters:
            cell_value = row.get(col, "")
            op_func = self.FILTER_OPERATORS.get(operator)
            if op_func:
                results.append(op_func(cell_value, value))
            else:
                results.append(True)

        if self.config.filter_logic == "and":
            return all(results) if results else True
        return any(results) if results else True
    
    def _apply_transformations(self, rows: list[dict], columns: list[str]) -> list[dict]:
        """Apply configured transformations to data."""
        # Pre-index per-column transforms for fast lookup
        col_transforms = {}  # {column: [(transform_type, *args), ...]}
        for ct in getattr(self.config, 'column_transforms', []):
            col_name = ct[0]
            transform = ct[1:]
            col_transforms.setdefault(col_name, []).append(transform)

        # Collect split/merge/compute transforms that alter the column set
        extra_columns = []
        for ct in getattr(self.config, 'column_transforms', []):
            if len(ct) >= 3 and ct[1] == "split":
                # split: (column, "split", delimiter, num_parts)
                num_parts = int(ct[3]) if len(ct) > 3 else 2
                for i in range(1, num_parts + 1):
                    new_col = f"{ct[0]}_{i}"
                    if new_col not in columns and new_col not in extra_columns:
                        extra_columns.append(new_col)
            elif len(ct) >= 4 and ct[1] == "merge":
                # merge: (target_col, "merge", separator, col1, col2, ...)
                new_col = ct[0]
                if new_col not in columns and new_col not in extra_columns:
                    extra_columns.append(new_col)
            elif len(ct) >= 3 and ct[1] == "compute":
                # compute: (new_column, "compute", expression)
                new_col = ct[0]
                if new_col not in columns and new_col not in extra_columns:
                    extra_columns.append(new_col)

        transformed = []

        for row in rows:
            new_row = {}
            for col in columns:
                value = row.get(col, self.config.empty_value)

                if value is None:
                    value = self.config.empty_value

                # Global case transformation
                if self.config.case_transform == "upper":
                    value = str(value).upper()
                elif self.config.case_transform == "lower":
                    value = str(value).lower()
                elif self.config.case_transform == "title":
                    value = str(value).title()

                # Per-column transforms
                if col in col_transforms:
                    for transform in col_transforms[col]:
                        value = self._apply_column_transform(value, transform, row)

                # Apply column mapping
                output_col = self.config.column_mapping.get(col, col)
                new_row[output_col] = value

            # Handle split column transforms
            for ct in getattr(self.config, 'column_transforms', []):
                if len(ct) >= 3 and ct[1] == "split":
                    src_col = ct[0]
                    delimiter = ct[2]
                    num_parts = int(ct[3]) if len(ct) > 3 else 2
                    src_value = str(row.get(src_col, ""))
                    parts = src_value.split(delimiter, num_parts - 1)
                    for i in range(num_parts):
                        new_col = f"{src_col}_{i+1}"
                        new_row[new_col] = parts[i].strip() if i < len(parts) else ""

            # Handle merge column transforms
            for ct in getattr(self.config, 'column_transforms', []):
                if len(ct) >= 4 and ct[1] == "merge":
                    target_col = ct[0]
                    separator = ct[2]
                    merge_cols = ct[3:]
                    merged_parts = [str(row.get(c, new_row.get(c, ""))) for c in merge_cols]
                    new_row[target_col] = separator.join(merged_parts)

            # Handle compute column transforms
            for ct in getattr(self.config, 'column_transforms', []):
                if len(ct) >= 3 and ct[1] == "compute":
                    target_col = ct[0]
                    expression = ct[2]
                    new_row[target_col] = self._evaluate_expression(expression, row, new_row)

            transformed.append(new_row)

        return transformed

    @staticmethod
    def _apply_column_transform(value: str, transform: tuple, row: dict) -> str:
        """Apply a single per-column transform."""
        transform_type = transform[0]
        value = str(value)

        if transform_type == "trim":
            return value.strip()
        elif transform_type == "upper":
            return value.upper()
        elif transform_type == "lower":
            return value.lower()
        elif transform_type == "title":
            return value.title()
        elif transform_type == "replace" and len(transform) >= 3:
            # (replace, search, replacement)
            return value.replace(transform[1], transform[2])
        elif transform_type == "regex_replace" and len(transform) >= 3:
            # (regex_replace, pattern, replacement)
            try:
                return re.sub(transform[1], transform[2], value)
            except re.error:
                return value
        return value

    @staticmethod
    def _evaluate_expression(expression: str, row: dict, new_row: dict) -> str:
        """Evaluate a simple compute expression using row values.
        Supports: column references in curly braces, basic arithmetic.
        E.g. '{qty} * {price}' or '{first_name} + " " + {last_name}'
        """
        try:
            # Replace {column_name} with values
            resolved = expression
            # Find all column references
            col_refs = re.findall(r'\{([^}]+)\}', expression)
            for ref in col_refs:
                val = row.get(ref, new_row.get(ref, ""))
                # Try to use numeric value if possible
                try:
                    numeric_val = float(str(val).replace(",", ""))
                    resolved = resolved.replace(f'{{{ref}}}', str(numeric_val))
                except ValueError:
                    resolved = resolved.replace(f'{{{ref}}}', repr(str(val)))

            # Only allow safe operations
            allowed_chars = set('0123456789.+-*/() "\'_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ,')
            if all(c in allowed_chars for c in resolved):
                result = eval(resolved)  # nosec - input is sanitized
                if isinstance(result, float) and result == int(result):
                    return str(int(result))
                return str(result)
            return ""
        except Exception:
            return ""
    
    def _deduplicate(self, rows: list[dict], columns: list[str]) -> list[dict]:
        """Remove duplicate rows."""
        if not rows:
            return rows
        
        dedupe_cols = self.config.dedupe_columns if self.config.dedupe_columns else columns
        fuzzy_enabled = getattr(self.config, "dedupe_fuzzy_enabled", False)
        fuzzy_threshold = int(getattr(self.config, "dedupe_fuzzy_threshold", 90))
        aggregate_mode = getattr(self.config, "dedupe_aggregate_mode", "none")

        seen = {}
        seen_keys = []
        result = []
        
        for row in rows:
            key = self._dedupe_key(row, dedupe_cols)

            matched_key = None
            if fuzzy_enabled:
                key_text = "\x1f".join(key)
                for existing_key in seen_keys:
                    existing_text = "\x1f".join(existing_key)
                    if self._fuzzy_score(key_text, existing_text) >= fuzzy_threshold:
                        matched_key = existing_key
                        break
            elif key in seen:
                matched_key = key

            if matched_key is None:
                seen[key] = len(result)
                seen_keys.append(key)
                result.append(row)
            elif aggregate_mode != "none":
                result[seen[matched_key]] = self._aggregate_duplicate_row(
                    result[seen[matched_key]],
                    row,
                    dedupe_cols,
                    columns,
                    aggregate_mode,
                )
            elif self.config.dedupe_keep == "last":
                result[seen[matched_key]] = row
        
        self.stats.duplicates_removed = len(rows) - len(result)
        self.log(f"  Removed {self.stats.duplicates_removed:,} duplicates", "info")
        
        return result

    def preview_duplicates(self, rows: list[dict], columns: list[str] | None = None) -> dict:
        """Describe duplicate groups without changing the supplied rows."""
        if not rows:
            return {"duplicate_count": 0, "group_count": 0, "groups": []}

        columns = list(columns or rows[0].keys())
        dedupe_cols = self.config.dedupe_columns if self.config.dedupe_columns else columns
        fuzzy_enabled = getattr(self.config, "dedupe_fuzzy_enabled", False)
        threshold = int(getattr(self.config, "dedupe_fuzzy_threshold", 90))
        groups = []
        exact_index = {}

        for index, row in enumerate(rows):
            key = self._dedupe_key(row, dedupe_cols)
            matched = None
            if fuzzy_enabled:
                key_text = "\x1f".join(key)
                for group in groups:
                    existing_text = "\x1f".join(group["key"])
                    if self._fuzzy_score(key_text, existing_text) >= threshold:
                        matched = group
                        break
            else:
                matched = exact_index.get(key)

            if matched is None:
                matched = {"key": key, "indexes": [], "rows": []}
                groups.append(matched)
                exact_index[key] = matched
            matched["indexes"].append(index)
            matched["rows"].append(copy.deepcopy(row))

        duplicate_groups = []
        duplicate_count = 0
        for group in groups:
            if len(group["indexes"]) < 2:
                continue
            if self.config.dedupe_keep == "last" and getattr(self.config, "dedupe_aggregate_mode", "none") == "none":
                kept_position = len(group["indexes"]) - 1
            else:
                kept_position = 0
            dropped_positions = [
                position for position in range(len(group["indexes"])) if position != kept_position
            ]
            duplicate_count += len(dropped_positions)
            duplicate_groups.append({
                "key": list(group["key"]),
                "kept_index": group["indexes"][kept_position],
                "dropped_indexes": [group["indexes"][position] for position in dropped_positions],
                "kept_row": group["rows"][kept_position],
                "dropped_rows": [group["rows"][position] for position in dropped_positions],
            })

        return {
            "duplicate_count": duplicate_count,
            "group_count": len(duplicate_groups),
            "groups": duplicate_groups,
        }

    def _dedupe_key(self, row: dict, dedupe_cols: list[str]) -> tuple[str, ...]:
        key_parts = []
        for col in dedupe_cols:
            mapped_col = self.config.column_mapping.get(col, col)
            val = row.get(mapped_col, row.get(col, ""))
            key_parts.append(str(val).lower() if not self.config.sort_case_sensitive else str(val))
        return tuple(key_parts)

    @staticmethod
    def _fuzzy_score(left: str, right: str) -> float:
        left_lower = left.lower()
        right_lower = right.lower()
        if left_lower in right_lower or right_lower in left_lower:
            return 100
        try:
            fuzz = _optional_module("rapidfuzz.fuzz")
            return fuzz.partial_ratio(left_lower, right_lower)
        except ImportError:
            from difflib import SequenceMatcher
            return SequenceMatcher(None, left_lower, right_lower).ratio() * 100

    def _aggregate_duplicate_row(
        self,
        existing: dict,
        incoming: dict,
        dedupe_cols: list[str],
        output_columns: list[str],
        mode: str,
    ) -> dict:
        result = dict(existing)
        key_columns = {
            self.config.column_mapping.get(col, col)
            for col in dedupe_cols
        } | set(dedupe_cols)
        separator = getattr(self.config, "dedupe_aggregate_separator", "; ")

        for col in output_columns:
            mapped_col = self.config.column_mapping.get(col, col)
            if col in key_columns or mapped_col in key_columns:
                continue

            left = result.get(mapped_col, result.get(col, ""))
            right = incoming.get(mapped_col, incoming.get(col, ""))
            result[mapped_col] = self._aggregate_value(left, right, mode, separator)

        return result

    @staticmethod
    def _aggregate_value(left, right, mode: str, separator: str):
        left_text = "" if left is None else str(left)
        right_text = "" if right is None else str(right)
        if mode == "concat":
            parts = []
            for value in (left_text, right_text):
                if value and value not in parts:
                    parts.append(value)
            return separator.join(parts)

        left_num = CSVEngine._parse_number(left_text)
        right_num = CSVEngine._parse_number(right_text)
        if left_num is None or right_num is None:
            if mode == "max":
                return max(left_text, right_text)
            if mode == "min":
                return min(left_text, right_text)
            return left_text or right_text

        if mode == "sum":
            value = left_num + right_num
        elif mode == "max":
            value = max(left_num, right_num)
        elif mode == "min":
            value = min(left_num, right_num)
        else:
            return left_text

        return str(int(value)) if value == int(value) else str(value)
    
    def _sort_rows(self, rows: list[dict], columns: list[str]) -> list[dict]:
        """Sort rows by configured columns."""
        if not rows or not self.config.sort_columns:
            return rows
        
        def sort_key(row):
            keys = []
            for col, ascending in self.config.sort_columns:
                mapped_col = self.config.column_mapping.get(col, col)
                val = row.get(mapped_col, row.get(col, ""))
                
                if self.config.sort_numeric_aware:
                    try:
                        val = float(str(val).replace(",", ""))
                    except ValueError:
                        if not self.config.sort_case_sensitive:
                            val = str(val).lower()
                else:
                    if not self.config.sort_case_sensitive:
                        val = str(val).lower()
                
                keys.append(val)
            return keys
        
        # Multi-column sort requires custom approach
        # Sort by last column first, then work backwards
        sorted_rows = rows.copy()
        for i in range(len(self.config.sort_columns) - 1, -1, -1):
            col, ascending = self.config.sort_columns[i]
            mapped_col = self.config.column_mapping.get(col, col)
            
            def make_key(row, mc=mapped_col, c=col, na=self.config.sort_numeric_aware, cs=self.config.sort_case_sensitive):
                val = row.get(mc, row.get(c, ""))
                if na:
                    parsed_number = CSVEngine._parse_number(val)
                    if parsed_number is not None:
                        return (0, parsed_number)
                    parsed_date = CSVEngine._parse_datetime(val)
                    if parsed_date is not None:
                        return (1, parsed_date.timestamp())
                text = str(val) if cs else str(val).lower()
                return (2, locale.strxfrm(text))
            
            sorted_rows.sort(key=make_key, reverse=not ascending)
        
        self.log(f"  Sorted by {len(self.config.sort_columns)} column(s)", "info")
        
        return sorted_rows
    
    def _write_output(self, rows: list[dict], columns: list[str], output_file: Path):
        """Write processed data atomically and emit its audit manifest."""
        if not rows:
            self.log("No data to write", "warning")
            return

        output_columns = [self.config.column_mapping.get(c, c) for c in columns]
        temporary = None
        error_count = len(self.stats.errors)
        try:
            temporary = self._temporary_output_path(output_file)
            self._write_output_payload(rows, output_columns, temporary)
            if self.cancelled:
                raise ProcessingCancelled()
            if len(self.stats.errors) > error_count:
                raise RuntimeError("output writer reported an error")
            self._fsync_file(temporary)
            backup_path = self._commit_output(temporary, output_file)
            temporary = None
            self._write_run_manifest(output_file, output_columns, backup_path)
            self.log(f"OK Saved: {output_file.name} ({len(rows):,} rows)", "success")
        except ProcessingCancelled:
            self.stats.cancelled = True
            self.log("Processing cancelled; output was not replaced", "warning")
        except Exception as exc:
            message = f"Write error: {exc}"
            if len(self.stats.errors) == error_count:
                self.stats.errors.append(message)
            self.log(f"XX {message}", "error")
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass

    def _write_output_payload(self, rows: list[dict], output_columns: list[str], output_file: Path):
        suffix = output_file.suffix.lower()
        if suffix in SUPPORTED_JSONL_SUFFIXES:
            self._write_jsonl_output(rows, output_columns, output_file)
            return
        if suffix == ".xlsx":
            self._write_xlsx_output(rows, output_columns, output_file)
            return
        if suffix == ".parquet":
            self._write_parquet_output(rows, output_columns, output_file)
            return

        quoting_map = {
            "minimal": csv.QUOTE_MINIMAL,
            "all": csv.QUOTE_ALL,
            "nonnumeric": csv.QUOTE_NONNUMERIC,
            "none": csv.QUOTE_NONE,
        }
        quoting = quoting_map.get(self.config.output_quoting, csv.QUOTE_MINIMAL)
        
        # Line ending
        if self.config.line_ending == "unix":
            newline = "\n"
        elif self.config.line_ending == "windows":
            newline = "\r\n"
        else:
            newline = ""
        
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding=self.config.output_encoding,
                  newline=newline if newline else '') as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=output_columns,
                delimiter=self.config.output_delimiter,
                quoting=quoting,
                extrasaction='ignore',
            )

            if self.config.include_header:
                writer.writeheader()

            for row in rows:
                if self.cancelled:
                    raise ProcessingCancelled()
                writer.writerow(row)

    def _write_xlsx_output(self, rows: list[dict], output_columns: list[str], output_file: Path):
        try:
            Workbook = _optional_module("openpyxl").Workbook
        except ImportError as exc:
            self.log("XX openpyxl is required for .xlsx output", "error")
            self.stats.errors.append(f"Write error: {exc}")
            return

        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            workbook = Workbook(write_only=True)
            sheet = workbook.create_sheet("Data")
            if self.config.include_header:
                sheet.append(output_columns)
            for row in rows:
                if self.cancelled:
                    raise ProcessingCancelled()
                sheet.append([row.get(column, "") for column in output_columns])
            workbook.save(output_file)
            self.log(f"OK Saved: {output_file.name} ({len(rows):,} rows)", "success")
        except Exception as exc:
            self.log(f"XX Error writing file: {exc}", "error")
            self.stats.errors.append(f"Write error: {exc}")

    def _write_jsonl_output(self, rows: list[dict], output_columns: list[str], output_file: Path):
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding=self.config.output_encoding, newline="") as handle:
                for row in rows:
                    if self.cancelled:
                        raise ProcessingCancelled()
                    json.dump(
                        {column: row.get(column, "") for column in output_columns},
                        handle,
                        ensure_ascii=False,
                    )
                    handle.write("\n")
            self.log(f"OK Saved: {output_file.name} ({len(rows):,} rows)", "success")
        except Exception as exc:
            self.log(f"XX Error writing file: {exc}", "error")
            self.stats.errors.append(f"Write error: {exc}")

    def _write_parquet_output(self, rows: list[dict], output_columns: list[str], output_file: Path):
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            records = [
                {column: row.get(column, "") for column in output_columns}
                for row in rows
            ]
            if self.cancelled:
                raise ProcessingCancelled()
            try:
                pa = _optional_module("pyarrow")
                pq = _optional_module("pyarrow.parquet")
                table = pa.Table.from_pylist(records)
                pq.write_table(table, output_file)
            except ImportError:
                pl = _optional_module("polars")
                pl.DataFrame(records).write_parquet(output_file)
            self.log(f"OK Saved: {output_file.name} ({len(rows):,} rows)", "success")
        except Exception as exc:
            self.log(f"XX Error writing file: {exc}", "error")
            self.stats.errors.append(f"Write error: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# GUI COMPONENTS
# ══════════════════════════════════════════════════════════════════════════════

class FileListPanel(ctk.CTkFrame):
    """File list with drag-drop support."""
    
    def __init__(self, master, on_change: Callable = None, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.files: list[Path] = []
        self.on_change = on_change
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="📁 Input Files",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        self.count_label = ctk.CTkLabel(
            header, text="0 files",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        self.count_label.pack(side="right")
        
        # List container
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.scroll_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"],
            scrollbar_button_hover_color=COLORS["accent_blue"]
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=4, pady=4)
        
        self.placeholder = ctk.CTkLabel(
            self.scroll_frame,
            text="Drag & drop CSV files here\nor use buttons below",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"]
        )
        self.placeholder.pack(expand=True, pady=30)
        
        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkButton(
            btn_frame, text="Add Files", font=ctk.CTkFont(size=12),
            height=32, fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"],
            corner_radius=6, command=self._browse_files
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        
        ctk.CTkButton(
            btn_frame, text="Add Folder", font=ctk.CTkFont(size=12),
            height=32, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=6, command=self._browse_folder
        ).pack(side="left", fill="x", expand=True, padx=(4, 4))
        
        ctk.CTkButton(
            btn_frame, text="Clear", font=ctk.CTkFont(size=12),
            height=32, width=60, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_secondary"],
            corner_radius=6, command=self.clear
        ).pack(side="left", padx=(4, 0))
    
    def _browse_files(self):
        files = filedialog.askopenfilenames(
            title="Select Input Files",
            filetypes=[
                ("Supported Files", "*.csv *.tsv *.txt *.xlsx *.parquet *.jsonl *.ndjson"),
                ("CSV Files", "*.csv"),
                ("TSV Files", "*.tsv"),
                ("Excel Files", "*.xlsx"),
                ("Parquet Files", "*.parquet"),
                ("JSON Lines", "*.jsonl *.ndjson"),
                ("Text Files", "*.txt"),
                ("All Files", "*.*"),
            ]
        )
        if files:
            self.add_files([Path(f) for f in files])
    
    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select Folder")
        if folder:
            folder_path = Path(folder)
            input_files = [
                path for path in folder_path.rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
            ]
            if input_files:
                self.add_files(input_files)
    
    def add_files(self, paths: list[Path]) -> int:
        added = 0
        for p in paths:
            path = Path(p) if not isinstance(p, Path) else p
            if path.is_dir():
                added += self.add_files([
                    child for child in path.rglob("*")
                    if child.is_file() and child.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
                ])
            elif path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES and path not in self.files:
                self.files.append(path)
                added += 1
        
        if added:
            self._refresh()
            if self.on_change:
                self.on_change()
        return added
    
    def remove_file(self, path: Path):
        if path in self.files:
            self.files.remove(path)
            self._refresh()
            if self.on_change:
                self.on_change()
    
    def clear(self):
        self.files.clear()
        self._refresh()
        if self.on_change:
            self.on_change()
    
    def _refresh(self):
        for w in self.scroll_frame.winfo_children():
            w.destroy()
        
        if not self.files:
            self.placeholder = ctk.CTkLabel(
                self.scroll_frame,
                text="Drag & drop CSV files here\nor use buttons below",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"]
            )
            self.placeholder.pack(expand=True, pady=30)
            self.count_label.configure(text="0 files")
        else:
            for idx, fp in enumerate(self.files):
                self._create_item(fp, idx)
            self.count_label.configure(text=f"{len(self.files)} file{'s' if len(self.files) != 1 else ''}")
    
    def _create_item(self, path: Path, idx: int):
        bg = COLORS["bg_secondary"] if idx % 2 == 0 else COLORS["bg_tertiary"]
        
        frame = ctk.CTkFrame(self.scroll_frame, fg_color=bg, corner_radius=4, height=32)
        frame.pack(fill="x", pady=1)
        frame.pack_propagate(False)
        
        ctk.CTkLabel(frame, text="📄", font=ctk.CTkFont(size=12), width=24).pack(side="left", padx=(6, 2))
        
        ctk.CTkLabel(
            frame, text=path.name, font=ctk.CTkFont(size=11),
            text_color=COLORS["text_primary"], anchor="w"
        ).pack(side="left", fill="x", expand=True)
        
        try:
            size = path.stat().st_size
            size_text = f"{size/1024:.1f}KB" if size >= 1024 else f"{size}B"
        except OSError:
            size_text = ""
        
        ctk.CTkLabel(
            frame, text=size_text, font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"], width=50
        ).pack(side="right", padx=4)
        
        ctk.CTkButton(
            frame, text="✕", font=ctk.CTkFont(size=10),
            width=24, height=24, fg_color="transparent",
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_muted"], corner_radius=4,
            command=lambda p=path: self.remove_file(p)
        ).pack(side="right", padx=2)


class ColumnPanel(ctk.CTkFrame):
    """Column selection and configuration."""
    
    def __init__(self, master, **kwargs):
        self.on_order_change = kwargs.pop("on_order_change", None)
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.columns: list[str] = []
        self.selected: set[str] = set()
        self.column_mapping: dict[str, str] = {}
        self._drag_column = None
        self._row_frames = {}
        self.mode = StringVar(value="all")
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="📊 Column Selection",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        self.count_label = ctk.CTkLabel(
            header, text="No columns",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        self.count_label.pack(side="right")
        
        # Mode selection
        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        for mode, text in [("all", "All Columns"), ("select", "Include Selected"), ("exclude", "Exclude Selected")]:
            ctk.CTkRadioButton(
                mode_frame, text=text, variable=self.mode, value=mode,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["accent_blue"],
                hover_color=COLORS["accent_blue_hover"],
                text_color=COLORS["text_secondary"]
            ).pack(side="left", padx=(0, 12))
        
        # Column list
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.scroll_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"],
            scrollbar_button_hover_color=COLORS["accent_blue"]
        )
        self.scroll_frame.pack(fill="both", expand=True, padx=4, pady=4)
        
        self.placeholder = ctk.CTkLabel(
            self.scroll_frame,
            text="Add files to discover columns",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"]
        )
        self.placeholder.pack(expand=True, pady=20)
        
        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkButton(
            btn_frame, text="Select All", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._select_all
        ).pack(side="left", padx=(0, 4))
        
        ctk.CTkButton(
            btn_frame, text="Select None", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._select_none
        ).pack(side="left")
    
    def set_columns(self, columns: list[str]):
        existing_order = [column for column in self.columns if column in columns]
        self.columns = existing_order + [column for column in columns if column not in existing_order]
        self.selected = {column for column in self.selected if column in self.columns} or set(self.columns)
        self._refresh()

    def get_column_order(self) -> list[str]:
        return list(self.columns)

    def _start_drag(self, column: str, _event=None):
        self._drag_column = column

    def _drag_motion(self, event):
        if not self._drag_column:
            return
        target = None
        for column, frame in self._row_frames.items():
            midpoint = frame.winfo_rooty() + max(frame.winfo_height(), 1) / 2
            if event.y_root < midpoint:
                target = column
                break
        if target and target != self._drag_column:
            source_index = self.columns.index(self._drag_column)
            target_index = self.columns.index(target)
            self.columns.insert(target_index, self.columns.pop(source_index))
            self._refresh()
            if self.on_order_change:
                self.on_order_change()

    def _finish_drag(self, _event=None):
        self._drag_column = None
    
    def _select_all(self):
        self.selected = set(self.columns)
        self._refresh()
    
    def _select_none(self):
        self.selected.clear()
        self._refresh()
    
    def _toggle_column(self, col: str, var: BooleanVar):
        if var.get():
            self.selected.add(col)
        else:
            self.selected.discard(col)
    
    def _refresh(self):
        for w in self.scroll_frame.winfo_children():
            w.destroy()
        self._row_frames = {}
        
        if not self.columns:
            self.placeholder = ctk.CTkLabel(
                self.scroll_frame,
                text="Add files to discover columns",
                font=ctk.CTkFont(size=12),
                text_color=COLORS["text_muted"]
            )
            self.placeholder.pack(expand=True, pady=20)
            self.count_label.configure(text="No columns")
        else:
            for col in self.columns:
                var = BooleanVar(value=col in self.selected)
                
                frame = ctk.CTkFrame(self.scroll_frame, fg_color="transparent", height=28)
                frame.pack(fill="x", pady=1)
                self._row_frames[col] = frame

                grip = ctk.CTkLabel(
                    frame, text="⋮⋮", width=24,
                    font=ctk.CTkFont(size=11),
                    text_color=COLORS["text_muted"],
                    cursor="hand2",
                )
                grip.pack(side="left")
                grip.bind("<ButtonPress-1>", lambda event, c=col: self._start_drag(c, event))
                grip.bind("<B1-Motion>", self._drag_motion)
                grip.bind("<ButtonRelease-1>", self._finish_drag)
                
                ctk.CTkCheckBox(
                    frame, text=col, variable=var,
                    font=ctk.CTkFont(size=11),
                    fg_color=COLORS["accent_blue"],
                    hover_color=COLORS["accent_blue_hover"],
                    text_color=COLORS["text_primary"],
                    command=lambda c=col, v=var: self._toggle_column(c, v)
                ).pack(side="left")
            
            self.count_label.configure(text=f"{len(self.columns)} columns")
    
    def get_config(self) -> tuple[str, list[str]]:
        return self.mode.get(), list(self.selected)


class SortPanel(ctk.CTkFrame):
    """Sorting configuration."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.columns: list[str] = []
        self.sort_rules: list[tuple[str, bool]] = []  # (column, ascending)
        
        self.enabled = BooleanVar(value=False)
        self.case_sensitive = BooleanVar(value=False)
        self.numeric_aware = BooleanVar(value=True)
        
        # Header with enable toggle
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkSwitch(
            header, text="🔤 Sorting",
            variable=self.enabled,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["bg_tertiary"],
            progress_color=COLORS["accent_green"],
            button_color=COLORS["text_secondary"],
            button_hover_color=COLORS["text_primary"]
        ).pack(side="left")
        
        # Options
        opts_frame = ctk.CTkFrame(self, fg_color="transparent")
        opts_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        ctk.CTkCheckBox(
            opts_frame, text="Case sensitive", variable=self.case_sensitive,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 12))
        
        ctk.CTkCheckBox(
            opts_frame, text="Numeric-aware", variable=self.numeric_aware,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(side="left")
        
        # Sort rules list
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.rules_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"]
        )
        self.rules_frame.pack(fill="both", expand=True, padx=4, pady=4)
        
        # Add rule button
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkButton(
            btn_frame, text="+ Add Sort Rule", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["accent_purple"],
            hover_color=COLORS["accent_purple_hover"],
            corner_radius=4, command=self._add_rule
        ).pack(side="left")
        
        ctk.CTkButton(
            btn_frame, text="Clear All", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._clear_rules
        ).pack(side="right")
    
    def set_columns(self, columns: list[str]):
        self.columns = columns
    
    def _add_rule(self):
        if not self.columns:
            return
        
        self.sort_rules.append((self.columns[0], True))
        self._refresh_rules()
    
    def _remove_rule(self, idx: int):
        if 0 <= idx < len(self.sort_rules):
            self.sort_rules.pop(idx)
            self._refresh_rules()
    
    def _clear_rules(self):
        self.sort_rules.clear()
        self._refresh_rules()
    
    def _update_rule(self, idx: int, column: str = None, ascending: bool = None):
        if 0 <= idx < len(self.sort_rules):
            col, asc = self.sort_rules[idx]
            self.sort_rules[idx] = (column if column is not None else col,
                                     ascending if ascending is not None else asc)
    
    def _refresh_rules(self):
        for w in self.rules_frame.winfo_children():
            w.destroy()
        
        if not self.sort_rules:
            ctk.CTkLabel(
                self.rules_frame,
                text="No sort rules defined",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"]
            ).pack(pady=10)
        else:
            for idx, (col, asc) in enumerate(self.sort_rules):
                self._create_rule_row(idx, col, asc)
    
    def _create_rule_row(self, idx: int, column: str, ascending: bool):
        frame = ctk.CTkFrame(self.rules_frame, fg_color=COLORS["bg_secondary"], corner_radius=4, height=36)
        frame.pack(fill="x", pady=2)
        frame.pack_propagate(False)
        
        ctk.CTkLabel(
            frame, text=f"{idx + 1}.",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"], width=24
        ).pack(side="left", padx=(8, 4))
        
        col_var = StringVar(value=column)
        col_menu = ctk.CTkOptionMenu(
            frame, variable=col_var, values=self.columns,
            font=ctk.CTkFont(size=11), height=28, width=150,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            button_hover_color=COLORS["bg_hover"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_rule(i, column=v)
        )
        col_menu.pack(side="left", padx=4)
        
        dir_var = StringVar(value="A→Z" if ascending else "Z→A")
        dir_menu = ctk.CTkOptionMenu(
            frame, variable=dir_var, values=["A→Z", "Z→A"],
            font=ctk.CTkFont(size=11), height=28, width=70,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            button_hover_color=COLORS["bg_hover"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_rule(i, ascending=(v == "A→Z"))
        )
        dir_menu.pack(side="left", padx=4)
        
        ctk.CTkButton(
            frame, text="✕", font=ctk.CTkFont(size=10),
            width=24, height=24, fg_color="transparent",
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_muted"], corner_radius=4,
            command=lambda i=idx: self._remove_rule(i)
        ).pack(side="right", padx=4)
    
    def get_config(self) -> tuple[bool, list, bool, bool]:
        return self.enabled.get(), self.sort_rules.copy(), self.case_sensitive.get(), self.numeric_aware.get()


class DedupePanel(ctk.CTkFrame):
    """Deduplication configuration."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.columns: list[str] = []
        self.selected_columns: set[str] = set()
        
        self.enabled = BooleanVar(value=True)
        self.keep_mode = StringVar(value="first")
        self.use_all_columns = BooleanVar(value=True)
        self.fuzzy_enabled = BooleanVar(value=False)
        self.fuzzy_threshold = IntVar(value=90)
        self.aggregate_mode = StringVar(value="none")
        self.aggregate_separator = StringVar(value="; ")
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkSwitch(
            header, text="🔄 Deduplication",
            variable=self.enabled,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["bg_tertiary"],
            progress_color=COLORS["accent_green"]
        ).pack(side="left")
        
        # Keep mode
        keep_frame = ctk.CTkFrame(self, fg_color="transparent")
        keep_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        ctk.CTkLabel(
            keep_frame, text="Keep:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 8))
        
        for val, text in [("first", "First"), ("last", "Last")]:
            ctk.CTkRadioButton(
                keep_frame, text=text, variable=self.keep_mode, value=val,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["accent_blue"],
                text_color=COLORS["text_secondary"]
            ).pack(side="left", padx=(0, 12))
        
        # Column selection mode
        col_mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        col_mode_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        ctk.CTkCheckBox(
            col_mode_frame, text="Use all columns for comparison",
            variable=self.use_all_columns,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"],
            command=self._toggle_column_selection
        ).pack(side="left")

        fuzzy_frame = ctk.CTkFrame(self, fg_color="transparent")
        fuzzy_frame.pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkCheckBox(
            fuzzy_frame, text="Fuzzy duplicate matching",
            variable=self.fuzzy_enabled,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(
            fuzzy_frame, text="Threshold",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"],
        ).pack(side="left", padx=(0, 6))

        ctk.CTkSlider(
            fuzzy_frame, from_=50, to=100, number_of_steps=50,
            variable=self.fuzzy_threshold,
            width=100,
            progress_color=COLORS["accent_blue"],
            button_color=COLORS["accent_blue"],
            button_hover_color=COLORS["accent_blue_hover"],
        ).pack(side="left")

        aggregate_frame = ctk.CTkFrame(self, fg_color="transparent")
        aggregate_frame.pack(fill="x", padx=12, pady=(0, 8))

        ctk.CTkLabel(
            aggregate_frame, text="Aggregate:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(0, 8))

        ctk.CTkOptionMenu(
            aggregate_frame, variable=self.aggregate_mode,
            values=["none", "max", "min", "sum", "concat"],
            font=ctk.CTkFont(size=11), height=28, width=90,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"],
        ).pack(side="left", padx=(0, 8))

        ctk.CTkEntry(
            aggregate_frame, textvariable=self.aggregate_separator,
            placeholder_text="separator",
            font=ctk.CTkFont(size=11), height=28, width=80,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"],
        ).pack(side="left")
        
        # Column list (hidden when use_all_columns is True)
        self.col_list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        
        self.col_scroll = ctk.CTkScrollableFrame(
            self.col_list_frame, fg_color="transparent", height=100,
            scrollbar_button_color=COLORS["bg_tertiary"]
        )
        self.col_scroll.pack(fill="both", expand=True, padx=4, pady=4)
    
    def _toggle_column_selection(self):
        if self.use_all_columns.get():
            self.col_list_frame.pack_forget()
        else:
            self.col_list_frame.pack(fill="x", padx=12, pady=(0, 12))
            self._refresh_columns()
    
    def set_columns(self, columns: list[str]):
        self.columns = columns
        self.selected_columns = set(columns)
        self._refresh_columns()
    
    def _toggle_column(self, col: str, var: BooleanVar):
        if var.get():
            self.selected_columns.add(col)
        else:
            self.selected_columns.discard(col)
    
    def _refresh_columns(self):
        for w in self.col_scroll.winfo_children():
            w.destroy()
        
        for col in self.columns:
            var = BooleanVar(value=col in self.selected_columns)
            ctk.CTkCheckBox(
                self.col_scroll, text=col, variable=var,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["accent_blue"],
                text_color=COLORS["text_primary"],
                command=lambda c=col, v=var: self._toggle_column(c, v)
            ).pack(anchor="w", pady=1)
    
    def get_config(self) -> tuple[bool, list, str, bool, int, str, str]:
        columns = [] if self.use_all_columns.get() else list(self.selected_columns)
        return (
            self.enabled.get(),
            columns,
            self.keep_mode.get(),
            self.fuzzy_enabled.get(),
            int(self.fuzzy_threshold.get()),
            self.aggregate_mode.get(),
            self.aggregate_separator.get(),
        )


class FilterPanel(ctk.CTkFrame):
    """Filter configuration."""

    OPERATORS = [
        ("equals", "Equals"),
        ("not_equals", "Not Equals"),
        ("contains", "Contains"),
        ("not_contains", "Not Contains"),
        ("starts_with", "Starts With"),
        ("ends_with", "Ends With"),
        ("is_empty", "Is Empty"),
        ("is_not_empty", "Is Not Empty"),
        ("greater_than", "Greater Than"),
        ("less_than", "Less Than"),
        ("greater_than_or_equal", "Greater/Equal"),
        ("less_than_or_equal", "Less/Equal"),
        ("between", "Between"),
        ("fuzzy", "Fuzzy Match"),
        ("in_list", "In List"),
        ("not_in_list", "Not In List"),
        ("regex", "Regex Match"),
    ]
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.columns: list[str] = []
        self.filters: list[tuple[str, str, str]] = []  # (column, operator, value)
        self.logic = StringVar(value="and")
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="🔍 Filters",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        # Logic selection
        logic_frame = ctk.CTkFrame(header, fg_color="transparent")
        logic_frame.pack(side="right")
        
        ctk.CTkRadioButton(
            logic_frame, text="AND", variable=self.logic, value="and",
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=4)
        
        ctk.CTkRadioButton(
            logic_frame, text="OR", variable=self.logic, value="or",
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(side="left")
        
        # Filter list
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        
        self.filters_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"]
        )
        self.filters_frame.pack(fill="both", expand=True, padx=4, pady=4)
        
        self.placeholder = ctk.CTkLabel(
            self.filters_frame,
            text="No filters defined",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        self.placeholder.pack(pady=10)
        
        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkButton(
            btn_frame, text="+ Add Filter", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["accent_orange"],
            hover_color="#ea580c",
            corner_radius=4, command=self._add_filter
        ).pack(side="left")
        
        ctk.CTkButton(
            btn_frame, text="Clear All", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._clear_filters
        ).pack(side="right")
    
    def set_columns(self, columns: list[str]):
        self.columns = columns
    
    def _add_filter(self):
        if not self.columns:
            return
        self.filters.append((self.columns[0], "contains", ""))
        self._refresh()
    
    def _remove_filter(self, idx: int):
        if 0 <= idx < len(self.filters):
            self.filters.pop(idx)
            self._refresh()
    
    def _clear_filters(self):
        self.filters.clear()
        self._refresh()
    
    def _update_filter(self, idx: int, column: str = None, operator: str = None, value: str = None):
        if 0 <= idx < len(self.filters):
            col, op, val = self.filters[idx]
            self.filters[idx] = (
                column if column is not None else col,
                operator if operator is not None else op,
                value if value is not None else val
            )
    
    def _refresh(self):
        for w in self.filters_frame.winfo_children():
            w.destroy()
        
        if not self.filters:
            self.placeholder = ctk.CTkLabel(
                self.filters_frame,
                text="No filters defined",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"]
            )
            self.placeholder.pack(pady=10)
        else:
            for idx, (col, op, val) in enumerate(self.filters):
                self._create_filter_row(idx, col, op, val)
    
    def _create_filter_row(self, idx: int, column: str, operator: str, value: str):
        frame = ctk.CTkFrame(self.filters_frame, fg_color=COLORS["bg_secondary"], corner_radius=4)
        frame.pack(fill="x", pady=2)
        
        row1 = ctk.CTkFrame(frame, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(6, 2))
        
        col_var = StringVar(value=column)
        ctk.CTkOptionMenu(
            row1, variable=col_var, values=self.columns,
            font=ctk.CTkFont(size=10), height=26, width=120,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_filter(i, column=v)
        ).pack(side="left", padx=(0, 4))
        
        op_display = {op: name for op, name in self.OPERATORS}
        op_var = StringVar(value=op_display.get(operator, operator))
        ctk.CTkOptionMenu(
            row1, variable=op_var, values=[name for _, name in self.OPERATORS],
            font=ctk.CTkFont(size=10), height=26, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_filter(i, operator=next((op for op, name in self.OPERATORS if name == v), v))
        ).pack(side="left", padx=(0, 4))
        
        ctk.CTkButton(
            row1, text="✕", font=ctk.CTkFont(size=10),
            width=24, height=24, fg_color="transparent",
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_muted"], corner_radius=4,
            command=lambda i=idx: self._remove_filter(i)
        ).pack(side="right")
        
        # Value entry (hidden for is_empty/is_not_empty)
        if operator not in ["is_empty", "is_not_empty"]:
            row2 = ctk.CTkFrame(frame, fg_color="transparent")
            row2.pack(fill="x", padx=8, pady=(2, 6))
            
            val_entry = ctk.CTkEntry(
                row2, placeholder_text="Value...",
                font=ctk.CTkFont(size=10), height=26,
                fg_color=COLORS["bg_dark"],
                border_color=COLORS["border"],
                text_color=COLORS["text_primary"]
            )
            val_entry.pack(fill="x")
            val_entry.insert(0, value)
            val_entry.bind("<KeyRelease>", lambda e, i=idx: self._update_filter(i, value=e.widget.get()))
    
    def get_config(self) -> tuple[list, str]:
        return self.filters.copy(), self.logic.get()


class TransformPanel(ctk.CTkFrame):
    """Data transformation configuration."""

    COLUMN_TRANSFORM_TYPES = [
        ("trim", "Trim"),
        ("upper", "UPPER"),
        ("lower", "lower"),
        ("title", "Title"),
        ("replace", "Replace"),
        ("regex_replace", "Regex Replace"),
    ]

    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)

        self.columns: list[str] = []
        self.column_transforms: list = []  # [(column, type, *args)]

        self.trim_whitespace = BooleanVar(value=True)
        self.case_transform = StringVar(value="none")
        self.empty_value = StringVar(value="")
        self.header_normalize = StringVar(value="none")

        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))

        ctk.CTkLabel(
            header, text="⚙️ Transformations",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")

        # Options
        opts_frame = ctk.CTkFrame(self, fg_color="transparent")
        opts_frame.pack(fill="x", padx=12, pady=(0, 4))

        # Trim whitespace
        ctk.CTkCheckBox(
            opts_frame, text="Trim whitespace",
            variable=self.trim_whitespace,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w", pady=4)

        # Case transformation
        case_frame = ctk.CTkFrame(opts_frame, fg_color="transparent")
        case_frame.pack(fill="x", pady=4)

        ctk.CTkLabel(
            case_frame, text="Case:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 8))

        for val, text in [("none", "None"), ("upper", "UPPER"), ("lower", "lower"), ("title", "Title")]:
            ctk.CTkRadioButton(
                case_frame, text=text, variable=self.case_transform, value=val,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["accent_blue"],
                text_color=COLORS["text_secondary"]
            ).pack(side="left", padx=(0, 8))

        # Header normalization
        header_frame = ctk.CTkFrame(opts_frame, fg_color="transparent")
        header_frame.pack(fill="x", pady=4)

        ctk.CTkLabel(
            header_frame, text="Headers:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 8))

        ctk.CTkOptionMenu(
            header_frame, variable=self.header_normalize,
            values=["none", "trim", "lowercase", "snake_case"],
            font=ctk.CTkFont(size=11), height=28, width=120,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left")

        # Empty value replacement
        empty_frame = ctk.CTkFrame(opts_frame, fg_color="transparent")
        empty_frame.pack(fill="x", pady=4)

        ctk.CTkLabel(
            empty_frame, text="Replace empty cells with:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(side="left", padx=(0, 8))

        ctk.CTkEntry(
            empty_frame, textvariable=self.empty_value,
            placeholder_text="(leave blank)",
            font=ctk.CTkFont(size=11), height=28, width=120,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"]
        ).pack(side="left")

        # Per-column transforms section
        sep = ctk.CTkFrame(self, fg_color=COLORS["border"], height=1)
        sep.pack(fill="x", padx=12, pady=(8, 4))

        col_header = ctk.CTkFrame(self, fg_color="transparent")
        col_header.pack(fill="x", padx=12, pady=(4, 4))

        ctk.CTkLabel(
            col_header, text="Per-Column Transforms",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")

        # Per-column transform list
        list_frame = ctk.CTkFrame(self, fg_color=COLORS["bg_dark"], corner_radius=6)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        self.transforms_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["bg_tertiary"]
        )
        self.transforms_frame.pack(fill="both", expand=True, padx=4, pady=4)

        self._refresh_transforms()

        # Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=12, pady=(0, 12))

        ctk.CTkButton(
            btn_frame, text="+ Add Transform", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["accent_purple"],
            hover_color=COLORS["accent_purple_hover"],
            corner_radius=4, command=self._add_transform
        ).pack(side="left")

        ctk.CTkButton(
            btn_frame, text="Clear All", font=ctk.CTkFont(size=11),
            height=28, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self._clear_transforms
        ).pack(side="right")

    def set_columns(self, columns: list[str]):
        self.columns = columns

    def _add_transform(self):
        if not self.columns:
            return
        self.column_transforms.append((self.columns[0], "trim", "", ""))
        self._refresh_transforms()

    def _remove_transform(self, idx: int):
        if 0 <= idx < len(self.column_transforms):
            self.column_transforms.pop(idx)
            self._refresh_transforms()

    def _clear_transforms(self):
        self.column_transforms.clear()
        self._refresh_transforms()

    def _update_transform(self, idx: int, **kwargs):
        if 0 <= idx < len(self.column_transforms):
            current = list(self.column_transforms[idx])
            if "column" in kwargs:
                current[0] = kwargs["column"]
            if "transform_type" in kwargs:
                current[1] = kwargs["transform_type"]
            if "arg1" in kwargs:
                if len(current) > 2:
                    current[2] = kwargs["arg1"]
                else:
                    current.append(kwargs["arg1"])
            if "arg2" in kwargs:
                if len(current) > 3:
                    current[3] = kwargs["arg2"]
                else:
                    current.append(kwargs["arg2"])
            self.column_transforms[idx] = tuple(current)

    def _refresh_transforms(self):
        for w in self.transforms_frame.winfo_children():
            w.destroy()

        if not self.column_transforms:
            ctk.CTkLabel(
                self.transforms_frame,
                text="No per-column transforms defined",
                font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"]
            ).pack(pady=10)
        else:
            for idx, ct in enumerate(self.column_transforms):
                self._create_transform_row(idx, ct)

    def _create_transform_row(self, idx: int, ct: tuple):
        frame = ctk.CTkFrame(self.transforms_frame, fg_color=COLORS["bg_secondary"],
                             corner_radius=4)
        frame.pack(fill="x", pady=2)

        row1 = ctk.CTkFrame(frame, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(6, 2))

        col_var = StringVar(value=ct[0])
        ctk.CTkOptionMenu(
            row1, variable=col_var,
            values=self.columns if self.columns else ["(no columns)"],
            font=ctk.CTkFont(size=10), height=26, width=120,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_transform(i, column=v)
        ).pack(side="left", padx=(0, 4))

        type_display = {t: n for t, n in self.COLUMN_TRANSFORM_TYPES}
        type_var = StringVar(value=type_display.get(ct[1], ct[1]))
        ctk.CTkOptionMenu(
            row1, variable=type_var,
            values=[n for _, n in self.COLUMN_TRANSFORM_TYPES],
            font=ctk.CTkFont(size=10), height=26, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"],
            command=lambda v, i=idx: self._update_transform(
                i, transform_type=next(
                    (t for t, n in self.COLUMN_TRANSFORM_TYPES if n == v), v
                ))
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            row1, text="✕", font=ctk.CTkFont(size=10),
            width=24, height=24, fg_color="transparent",
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_muted"], corner_radius=4,
            command=lambda i=idx: self._remove_transform(i)
        ).pack(side="right")

        # Show arg fields for replace/regex_replace
        if ct[1] in ("replace", "regex_replace"):
            row2 = ctk.CTkFrame(frame, fg_color="transparent")
            row2.pack(fill="x", padx=8, pady=(2, 6))

            search_entry = ctk.CTkEntry(
                row2, placeholder_text="Search...",
                font=ctk.CTkFont(size=10), height=26, width=120,
                fg_color=COLORS["bg_dark"],
                border_color=COLORS["border"],
                text_color=COLORS["text_primary"]
            )
            search_entry.pack(side="left", padx=(0, 4))
            if len(ct) > 2:
                search_entry.insert(0, ct[2])
            search_entry.bind("<KeyRelease>",
                              lambda e, i=idx: self._update_transform(i, arg1=e.widget.get()))

            replace_entry = ctk.CTkEntry(
                row2, placeholder_text="Replace with...",
                font=ctk.CTkFont(size=10), height=26, width=120,
                fg_color=COLORS["bg_dark"],
                border_color=COLORS["border"],
                text_color=COLORS["text_primary"]
            )
            replace_entry.pack(side="left")
            if len(ct) > 3:
                replace_entry.insert(0, ct[3])
            replace_entry.bind("<KeyRelease>",
                               lambda e, i=idx: self._update_transform(i, arg2=e.widget.get()))

    def get_config(self) -> tuple[bool, str, str]:
        return self.trim_whitespace.get(), self.case_transform.get(), self.empty_value.get()

    def get_header_normalize(self) -> str:
        return self.header_normalize.get()

    def get_column_transforms(self) -> list:
        return [ct for ct in self.column_transforms]


class OutputPanel(ctk.CTkFrame):
    """Output configuration."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.delimiter = StringVar(value=",")
        self.encoding = StringVar(value="utf-8")
        self.quoting = StringVar(value="minimal")
        self.include_header = BooleanVar(value=True)
        self.line_ending = StringVar(value="auto")
        self.output_path = StringVar(value="")
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="💾 Output Settings",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        # Options grid
        opts_frame = ctk.CTkFrame(self, fg_color="transparent")
        opts_frame.pack(fill="x", padx=12, pady=(0, 8))
        
        # Row 1: Delimiter and Encoding
        row1 = ctk.CTkFrame(opts_frame, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        
        ctk.CTkLabel(row1, text="Delimiter:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], width=70, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(
            row1, variable=self.delimiter,
            values=[",", ";", "\\t (Tab)", "|"],
            font=ctk.CTkFont(size=11), height=28, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left", padx=(0, 20))
        
        ctk.CTkLabel(row1, text="Encoding:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], width=70, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(
            row1, variable=self.encoding,
            values=["utf-8", "utf-16", "latin-1", "cp1252"],
            font=ctk.CTkFont(size=11), height=28, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left")
        
        # Row 2: Quoting and Line ending
        row2 = ctk.CTkFrame(opts_frame, fg_color="transparent")
        row2.pack(fill="x", pady=4)
        
        ctk.CTkLabel(row2, text="Quoting:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], width=70, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(
            row2, variable=self.quoting,
            values=["minimal", "all", "nonnumeric", "none"],
            font=ctk.CTkFont(size=11), height=28, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left", padx=(0, 20))
        
        ctk.CTkLabel(row2, text="Line End:", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_secondary"], width=70, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(
            row2, variable=self.line_ending,
            values=["auto", "unix (LF)", "windows (CRLF)"],
            font=ctk.CTkFont(size=11), height=28, width=100,
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_tertiary"],
            dropdown_fg_color=COLORS["bg_secondary"]
        ).pack(side="left")
        
        # Include header checkbox
        ctk.CTkCheckBox(
            opts_frame, text="Include header row",
            variable=self.include_header,
            font=ctk.CTkFont(size=11),
            fg_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w", pady=(8, 4))
        
        # Output path
        path_frame = ctk.CTkFrame(self, fg_color="transparent")
        path_frame.pack(fill="x", padx=12, pady=(0, 12))
        
        ctk.CTkLabel(
            path_frame, text="Output File:",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_secondary"]
        ).pack(anchor="w", pady=(0, 4))
        
        path_row = ctk.CTkFrame(path_frame, fg_color="transparent")
        path_row.pack(fill="x")
        
        self.path_entry = ctk.CTkEntry(
            path_row, textvariable=self.output_path,
            placeholder_text="Select output file...",
            font=ctk.CTkFont(size=11), height=32,
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"],
            text_color=COLORS["text_primary"]
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        
        ctk.CTkButton(
            path_row, text="Browse", font=ctk.CTkFont(size=11),
            height=32, width=70, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=6, command=self._browse
        ).pack(side="left")
    
    def _browse(self):
        file = filedialog.asksaveasfilename(
            title="Save As",
            defaultextension=".csv",
            filetypes=[
                ("CSV Files", "*.csv"),
                ("TSV Files", "*.tsv"),
                ("Excel Files", "*.xlsx"),
                ("Parquet Files", "*.parquet"),
                ("JSON Lines", "*.jsonl *.ndjson"),
                ("Text Files", "*.txt"),
                ("All Files", "*.*"),
            ]
        )
        if file:
            self.output_path.set(file)
    
    def get_config(self) -> dict:
        delim = self.delimiter.get()
        if delim == "\\t (Tab)":
            delim = "\t"
        
        line_end = self.line_ending.get()
        if "unix" in line_end:
            line_end = "unix"
        elif "windows" in line_end:
            line_end = "windows"
        else:
            line_end = "auto"
        
        return {
            "delimiter": delim,
            "encoding": self.encoding.get(),
            "quoting": self.quoting.get(),
            "include_header": self.include_header.get(),
            "line_ending": line_end,
            "output_path": self.output_path.get()
        }


class LogPanel(ctk.CTkFrame):
    """Processing log display."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        # Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 8))
        
        ctk.CTkLabel(
            header, text="📋 Processing Log",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(side="left")
        
        ctk.CTkButton(
            header, text="Clear", font=ctk.CTkFont(size=10),
            height=24, width=50, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=4, command=self.clear
        ).pack(side="right")
        
        # Log text
        self.log_text = ctk.CTkTextbox(
            self, fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=6
        )
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log_text.configure(state="disabled")
    
    def log(self, message: str, level: str = "info"):
        self.log_text.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "│", "success": "✓", "warning": "⚠", "error": "✗"}.get(level, "│")
        self.log_text.insert(END, f"[{timestamp}] {prefix} {message}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")
    
    def clear(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", END)
        self.log_text.configure(state="disabled")


class PreviewPanel(ctk.CTkFrame):
    """Read-only projected sample with explicit bounded-scan status."""

    @staticmethod
    def format_preview(columns: list[str], rows: list[dict], max_width: int = 24) -> str:
        if not columns:
            return "No columns discovered."
        values = []
        for row in rows:
            values.append([
                str(row.get(column, "")).replace("\r", " ").replace("\n", " ")
                for column in columns
            ])
        widths = [min(max_width, max(len(column), *(len(row[index]) for row in values)) if values else len(column))
                  for index, column in enumerate(columns)]

        def render(row):
            return " | ".join(value[:width].ljust(width) for value, width in zip(row, widths))

        lines = [render(columns), "-+-".join("-" * width for width in widths)]
        lines.extend(render(row) for row in values)
        if not values:
            lines.append("(no rows)")
        return "\n".join(lines)

    def __init__(self, master, on_refresh: Callable = None, on_cancel: Callable = None, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        self.on_refresh = on_refresh
        self.on_cancel = on_cancel

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 6))
        ctk.CTkLabel(
            header, text="🔎 Preview",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")
        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(side="right")
        ctk.CTkButton(
            actions, text="Cancel", width=58, height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["bg_tertiary"], hover_color=COLORS["accent_red"],
            text_color=COLORS["text_secondary"], corner_radius=4,
            command=lambda: self.on_cancel() if self.on_cancel else None,
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            actions, text="Refresh", width=64, height=24,
            font=ctk.CTkFont(size=10),
            fg_color=COLORS["bg_tertiary"], hover_color=COLORS["accent_blue"],
            text_color=COLORS["text_secondary"], corner_radius=4,
            command=lambda: self.on_refresh() if self.on_refresh else None,
        ).pack(side="left")

        self.status_label = ctk.CTkLabel(
            self, text="Add files to preview projected output",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"], anchor="w",
        )
        self.status_label.pack(fill="x", padx=12, pady=(0, 4))
        self.preview_text = ctk.CTkTextbox(
            self, fg_color=COLORS["bg_dark"], text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family="Consolas", size=10), corner_radius=6,
        )
        self.preview_text.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.preview_text.configure(state="disabled")

    def update_preview(self, preview: dict):
        columns = preview.get("columns", [])
        rows = preview.get("rows", [])
        text = self.format_preview(columns, rows)
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", END)
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")
        metadata = preview.get("metadata", {})
        if metadata.get("cancelled"):
            status = f"Cancelled after scanning {metadata.get('rows_scanned', 0):,} row(s)"
        elif metadata.get("scan_truncated"):
            status = (
                f"Read-only sample: showing {len(rows):,}; scanned "
                f"{metadata.get('rows_scanned', 0):,} row(s) within the preview budget"
            )
        else:
            status = f"Read-only sample: {len(rows):,} row(s) scanned within the preview budget"
        self.status_label.configure(text=status)

    def reset(self, message: str = "Add files to preview projected output"):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", END)
        self.preview_text.configure(state="disabled")
        self.status_label.configure(text=message)


class QualityPanel(ctk.CTkFrame):
    """Faceted quality profile, raw-row inspector, and reviewed repairs."""

    @staticmethod
    def format_profile(report: dict | None, query: str = "") -> str:
        if not report:
            return "No quality profile yet. Select files and choose Profile."
        query = str(query or "").strip().lower()
        source_rows = report.get("source_rows_scanned", report.get("rows_scanned", 0))
        rows = report.get("rows_scanned", 0)
        header = [
            f"Rows profiled: {rows:,} | source rows scanned: {source_rows:,}",
            f"Bounded scan: {'yes' if report.get('bounded') else 'no'} | "
            f"truncated: {'yes' if report.get('scan_truncated') else 'no'}",
        ]
        facet_filter = report.get("facet_filter")
        if facet_filter:
            header.append(
                f"Facet filter: {facet_filter.get('column', '')}={facet_filter.get('value', '')!r}"
            )
        lines = header + ["", "Column quality signals (raw text is preserved; types are advisory):"]
        visible = 0
        for column in report.get("columns", []):
            facets = column.get("facets", [])
            searchable = " ".join([
                str(column.get("name", "")),
                *(str(item.get("value", "")) for item in facets),
            ]).lower()
            if query and query not in searchable:
                continue
            visible += 1
            numeric = column.get("numeric")
            numeric_text = ""
            if numeric:
                numeric_text = (
                    f" numeric=count:{numeric.get('count', 0):,}"
                    f" min:{numeric.get('min')} max:{numeric.get('max')}"
                    f" mean:{numeric.get('mean')}"
                )
            lines.append(
                f"{column.get('name', '')} | rows={column.get('row_count', 0):,}"
                f" non-empty={column.get('non_empty_count', 0):,}"
                f" blank={column.get('blank_count', 0):,}"
                f" null={column.get('null_count', 0):,}"
                f" unique={column.get('unique_count', 0):,}"
                f"{' (lower bound)' if not column.get('unique_count_exact', True) else ''}"
                f" type={column.get('inferred_type', 'empty')}"
                f" confidence={column.get('type_confidence', 0):.1%}{numeric_text}"
            )
            samples = column.get("raw_samples", column.get("samples", []))
            lines.append(f"  samples: {', '.join(repr(value) for value in samples) or '(none)'}")
            facet_text = ", ".join(
                f"{item.get('value', '')!r} ({item.get('count', 0):,})" for item in facets
            ) or "(none)"
            if column.get("facets_truncated"):
                facet_text += ", ..."
            lines.append(f"  facets: {facet_text}")
        if not visible:
            lines.append("(no columns match the current filter)")
        return "\n".join(lines)

    def __init__(
        self,
        master,
        on_profile: Callable = None,
        on_inspect: Callable = None,
        on_edit: Callable = None,
        **kwargs,
    ):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        self.on_profile = on_profile
        self.on_inspect = on_inspect
        self.on_edit = on_edit
        self.profile_report = None
        self.repair_edits = []

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            header, text="Data Quality",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")
        ctk.CTkButton(
            header, text="Profile", width=72, height=25,
            font=ctk.CTkFont(size=10), fg_color=COLORS["accent_blue"],
            hover_color=COLORS["accent_blue_hover"], corner_radius=5,
            command=lambda: self.on_profile() if self.on_profile else None,
        ).pack(side="right")

        ctk.CTkLabel(
            self,
            text="Inspect distributions, drill into a facet, and record exact reviewed text edits."
                 " The global Undo/Redo controls include these edits.",
            font=ctk.CTkFont(size=10), text_color=COLORS["text_muted"],
            anchor="w", justify="left", wraplength=500,
        ).pack(fill="x", padx=12, pady=(0, 6))

        filter_row = ctk.CTkFrame(self, fg_color="transparent")
        filter_row.pack(fill="x", padx=12, pady=(0, 5))
        self.profile_filter = ctk.CTkEntry(
            filter_row, width=170, height=28,
            placeholder_text="Find column or facet",
            font=ctk.CTkFont(size=10), fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"], text_color=COLORS["text_primary"],
        )
        self.profile_filter.pack(side="left", padx=(0, 5))
        self.facet_filter = ctk.CTkEntry(
            filter_row, width=190, height=28,
            placeholder_text="Facet filter: column=value",
            font=ctk.CTkFont(size=10), fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"], text_color=COLORS["text_primary"],
        )
        self.facet_filter.pack(side="left")
        self.profile_filter.bind("<KeyRelease>", lambda _event: self._render_profile())

        self.profile_status = ctk.CTkLabel(
            self, text="No profile loaded", font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"], anchor="w",
        )
        self.profile_status.pack(fill="x", padx=12, pady=(0, 3))
        self.profile_text = ctk.CTkTextbox(
            self, height=220, fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_primary"],
            font=ctk.CTkFont(family="Consolas", size=9), corner_radius=6,
        )
        self.profile_text.pack(fill="both", expand=True, padx=12, pady=(0, 7))
        self.profile_text.configure(state="disabled")

        inspect_header = ctk.CTkFrame(self, fg_color="transparent")
        inspect_header.pack(fill="x", padx=12, pady=(0, 3))
        ctk.CTkLabel(
            inspect_header, text="Row inspection (global 1-based row)",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text_secondary"], anchor="w",
        ).pack(side="left")
        self.inspect_row_entry = ctk.CTkEntry(
            inspect_header, width=70, height=25, placeholder_text="Row",
            font=ctk.CTkFont(size=10), fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"], text_color=COLORS["text_primary"],
        )
        self.inspect_row_entry.insert(0, "1")
        self.inspect_row_entry.pack(side="right", padx=(5, 0))
        ctk.CTkButton(
            inspect_header, text="Inspect", width=65, height=25,
            font=ctk.CTkFont(size=10), fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"], text_color=COLORS["text_secondary"],
            corner_radius=5,
            command=lambda: self.on_inspect(self.inspect_row_entry.get()) if self.on_inspect else None,
        ).pack(side="right")
        self.inspection_text = ctk.CTkTextbox(
            self, height=72, fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(family="Consolas", size=9), corner_radius=6,
        )
        self.inspection_text.pack(fill="x", padx=12, pady=(0, 7))
        self.inspection_text.configure(state="disabled")

        repair_header = ctk.CTkFrame(self, fg_color="transparent")
        repair_header.pack(fill="x", padx=12, pady=(0, 3))
        ctk.CTkLabel(
            repair_header, text="Reviewed repairs (text replacements)",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=COLORS["text_secondary"], anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            repair_header, text="Raw text is compared when Expected is set",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"], anchor="e",
        ).pack(side="right")

        repair_form = ctk.CTkFrame(self, fg_color="transparent")
        repair_form.pack(fill="x", padx=12, pady=(0, 4))
        fields = (
            ("Row", 48), ("Column", 100), ("Expected old", 120),
            ("Replacement", 120), ("Reason", 120),
        )
        self.repair_entries = []
        for placeholder, width in fields:
            entry = ctk.CTkEntry(
                repair_form, width=width, height=27, placeholder_text=placeholder,
                font=ctk.CTkFont(size=9), fg_color=COLORS["bg_dark"],
                border_color=COLORS["border"], text_color=COLORS["text_primary"],
            )
            entry.pack(side="left", padx=(0, 3))
            self.repair_entries.append(entry)
        ctk.CTkButton(
            repair_form, text="Add", width=50, height=27,
            font=ctk.CTkFont(size=9), fg_color=COLORS["accent_green"],
            hover_color=COLORS["accent_green_hover"], corner_radius=5,
            command=self._add_repair,
        ).pack(side="left")

        repair_actions = ctk.CTkFrame(self, fg_color="transparent")
        repair_actions.pack(fill="x", padx=12, pady=(0, 3))
        self.remove_index = ctk.CTkEntry(
            repair_actions, width=55, height=25, placeholder_text="#",
            font=ctk.CTkFont(size=9), fg_color=COLORS["bg_dark"],
            border_color=COLORS["border"], text_color=COLORS["text_primary"],
        )
        self.remove_index.pack(side="right", padx=(4, 0))
        ctk.CTkButton(
            repair_actions, text="Remove #", width=72, height=25,
            font=ctk.CTkFont(size=9), fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"], text_color=COLORS["text_secondary"],
            corner_radius=5, command=self._remove_repair,
        ).pack(side="right")
        self.repair_text = ctk.CTkTextbox(
            self, height=68, fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(family="Consolas", size=9), corner_radius=6,
        )
        self.repair_text.pack(fill="x", padx=12, pady=(0, 4))
        self.repair_text.configure(state="disabled")
        self.status_label = ctk.CTkLabel(
            self, text="Reviewed edits are applied before filters/transforms and written to the manifest.",
            font=ctk.CTkFont(size=9), text_color=COLORS["text_muted"], anchor="w",
        )
        self.status_label.pack(fill="x", padx=12, pady=(0, 8))

    def _set_text(self, widget, value: str):
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _render_profile(self):
        query = self.profile_filter.get() if hasattr(self, "profile_filter") else ""
        self._set_text(self.profile_text, self.format_profile(self.profile_report, query))

    def _render_repairs(self):
        if not self.repair_edits:
            text = "(no reviewed edits)"
        else:
            text = "\n".join(
                f"{index}. row={edit['row']} column={edit['column']!r} "
                f"expected={edit.get('expected_old')!r} replacement={edit['value']!r} "
                f"reason={edit.get('reason', '')!r}"
                for index, edit in enumerate(self.repair_edits, 1)
            )
        self._set_text(self.repair_text, text)

    def _add_repair(self):
        row_entry, column_entry, expected_entry, replacement_entry, reason_entry = self.repair_entries
        try:
            row = int(row_entry.get().strip())
        except ValueError:
            self.status_label.configure(text="Repair row must be a positive integer")
            return
        edit = {
            "row": row,
            "column": column_entry.get(),
            "value": replacement_entry.get(),
            "reason": reason_entry.get() or "reviewed correction",
        }
        if expected_entry.get() != "":
            edit["expected_old"] = expected_entry.get()
        try:
            self.repair_edits = normalize_repairs(self.repair_edits + [edit])
        except QualityError as exc:
            self.status_label.configure(text=f"Repair not added: {exc}")
            return
        self._render_repairs()
        for entry in self.repair_entries:
            entry.delete(0, END)
        if self.on_edit:
            self.on_edit(self.get_edits())
        self.status_label.configure(text=f"Recorded {len(self.repair_edits):,} reviewed edit(s)")

    def _remove_repair(self):
        try:
            index = int(self.remove_index.get().strip())
        except ValueError:
            self.status_label.configure(text="Remove # must be a positive edit number")
            return
        if index < 1 or index > len(self.repair_edits):
            self.status_label.configure(text="That reviewed edit number does not exist")
            return
        del self.repair_edits[index - 1]
        self.remove_index.delete(0, END)
        self._render_repairs()
        if self.on_edit:
            self.on_edit(self.get_edits())
        self.status_label.configure(text=f"Recorded {len(self.repair_edits):,} reviewed edit(s)")

    def get_edits(self) -> list[dict]:
        return copy.deepcopy(self.repair_edits)

    def set_edits(self, edits) -> None:
        try:
            self.repair_edits = normalize_repairs(edits or [])
        except QualityError as exc:
            self.repair_edits = []
            self.status_label.configure(text=f"Invalid saved repairs: {exc}")
        self._render_repairs()

    def get_facet_filter(self) -> tuple[str | None, str | None]:
        value = self.facet_filter.get().strip()
        if not value:
            return None, None
        if "=" not in value:
            raise QualityError("Facet filter must use column=value")
        column, facet = value.split("=", 1)
        if not column.strip():
            raise QualityError("Facet filter column cannot be empty")
        return column.strip(), facet

    def update_profile(self, report: dict):
        self.profile_report = copy.deepcopy(report)
        self._render_profile()
        source_rows = report.get("source_rows_scanned", report.get("rows_scanned", 0))
        self.profile_status.configure(
            text=f"Profile ready: {report.get('rows_scanned', 0):,} matching row(s), "
                 f"{source_rows:,} source row(s) scanned"
        )

    def update_inspection(self, inspection: dict | None):
        if not inspection:
            self._set_text(self.inspection_text, "No matching row was found.")
            return
        lines = [
            f"row {inspection['row_number']:,} | source: {inspection['source_file']}",
            "column                  raw text                              inferred type",
        ]
        for value in inspection.get("values", []):
            raw = str(value.get("raw", "")).replace("\r", "\\r").replace("\n", "\\n")
            lines.append(
                f"{value.get('column', '')[:22]:22} {raw[:36]!r:38} {value.get('inferred_type', 'empty')}"
            )
        self._set_text(self.inspection_text, "\n".join(lines))

    def reset_profile(self):
        self.profile_report = None
        self._render_profile()
        self._set_text(self.inspection_text, "Select a row to inspect source text.")
        self.profile_status.configure(text="No profile loaded")


class StatsPanel(ctk.CTkFrame):
    """Processing statistics display."""
    
    def __init__(self, master, **kwargs):
        if "fg_color" not in kwargs:
            kwargs["fg_color"] = COLORS["bg_secondary"]
        if "corner_radius" not in kwargs:
            kwargs["corner_radius"] = 8
        super().__init__(master, **kwargs)
        
        self.labels = {}
        
        stats_config = [
            ("files_processed", "Files Processed", COLORS["accent_blue"]),
            ("total_rows_read", "Rows Read", COLORS["text_primary"]),
            ("rows_filtered", "Rows Filtered", COLORS["accent_orange"]),
            ("duplicates_removed", "Duplicates Removed", COLORS["accent_purple"]),
            ("final_row_count", "Final Rows", COLORS["accent_green"]),
        ]
        
        for i, (key, label, color) in enumerate(stats_config):
            frame = ctk.CTkFrame(self, fg_color="transparent")
            frame.pack(fill="x", padx=12, pady=(12 if i == 0 else 2, 2 if i < 4 else 12))
            
            ctk.CTkLabel(
                frame, text=label, font=ctk.CTkFont(size=11),
                text_color=COLORS["text_muted"]
            ).pack(side="left")
            
            val_label = ctk.CTkLabel(
                frame, text="—", font=ctk.CTkFont(size=12, weight="bold"),
                text_color=color
            )
            val_label.pack(side="right")
            self.labels[key] = val_label

        ctk.CTkLabel(
            self, text="Column Summary", font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text_secondary"], anchor="w",
        ).pack(fill="x", padx=12, pady=(2, 4))
        self.summary_text = ctk.CTkTextbox(
            self, height=96, fg_color=COLORS["bg_dark"],
            text_color=COLORS["text_secondary"],
            font=ctk.CTkFont(family="Consolas", size=9), corner_radius=6,
        )
        self.summary_text.pack(fill="x", padx=12, pady=(0, 10))
        self.summary_text.configure(state="disabled")
    
    def update(self, stats: ProcessingStats):
        for key, label in self.labels.items():
            val = getattr(stats, key, 0)
            label.configure(text=f"{val:,}")
        lines = []
        for column, summary in stats.column_summary.items():
            lines.append(
                f"{column[:18]:18} rows={summary.get('row_count', 0):>6,} "
                f"distinct={summary.get('distinct_count', 0):>6,} "
                f"type={summary.get('inferred_type', 'empty')}"
            )
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", END)
        self.summary_text.insert("1.0", "\n".join(lines) if lines else "(no column summary)")
        self.summary_text.configure(state="disabled")
    
    def reset(self):
        for label in self.labels.values():
            label.configure(text="—")
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", END)
        self.summary_text.configure(state="disabled")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class CSVPowerToolApp:
    """Main application."""
    
    def __init__(self):
        if DND_AVAILABLE:
            self.root = TkinterDnD.Tk()
        else:
            self.root = ctk.CTk()
        
        self.root.title(APP_NAME)
        self.root.geometry("1200x850")
        self.root.minsize(1000, 700)
        
        COLORS.update(DARK_COLORS)
        ctk.set_appearance_mode("dark")
        self.root.configure(bg=COLORS["bg_dark"])
        
        self.engine: CSVEngine = None
        self.engine_service = EngineService(CSVEngine)
        self.processing = False
        self.appearance_mode = StringVar(value="Dark")
        self._config_overrides = {}
        self._preview_job = None
        self._preview_generation = 0
        self._preview_engine = None
        self._quality_generation = 0
        self._quality_engine = None
        self.history = None
        self.workflow_history_path = Path.home() / ".csv-power-tool" / "workflow-history.json"
        
        self._build_ui()
        self.history = ConfigHistory(self._config_to_data())
        self._update_history_buttons()
        self.root.bind_all("<ButtonRelease-1>", self._on_ui_edit, add="+")
        self.root.bind_all("<KeyRelease>", self._on_ui_edit, add="+")
        
        if DND_AVAILABLE:
            self._setup_dnd()
    
    def _build_ui(self):
        # Main container
        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=16)
        
        # Header
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x", pady=(0, 12))
        
        title_frame = ctk.CTkFrame(header, fg_color="transparent")
        title_frame.pack(side="left")
        
        ctk.CTkLabel(
            title_frame, text=APP_NAME,
            font=ctk.CTkFont(size=26, weight="bold"),
            text_color=COLORS["text_primary"]
        ).pack(anchor="w")
        
        ctk.CTkLabel(
            title_frame, text="Combine • Filter • Transform • Deduplicate • Export",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"]
        ).pack(anchor="w")
        
        # Version
        ver_badge = ctk.CTkFrame(header, fg_color=COLORS["bg_tertiary"], corner_radius=12)
        ver_badge.pack(side="right")
        ctk.CTkLabel(ver_badge, text=f"v{APP_VERSION}", font=ctk.CTkFont(size=11),
                     text_color=COLORS["text_muted"]).pack(padx=12, pady=4)

        appearance_frame = ctk.CTkFrame(header, fg_color="transparent")
        appearance_frame.pack(side="right", padx=(0, 10))
        ctk.CTkLabel(
            appearance_frame, text="Theme", font=ctk.CTkFont(size=10),
            text_color=COLORS["text_muted"],
        ).pack(side="left", padx=(0, 6))
        ctk.CTkOptionMenu(
            appearance_frame, variable=self.appearance_mode,
            values=["Dark", "Light", "System"], width=86, height=26,
            font=ctk.CTkFont(size=10), fg_color=COLORS["bg_tertiary"],
            button_color=COLORS["bg_hover"], dropdown_fg_color=COLORS["bg_secondary"],
            command=self._set_appearance_mode,
        ).pack(side="left")
        
        # Content: 3 columns
        content = ctk.CTkFrame(main, fg_color="transparent")
        content.pack(fill="both", expand=True)
        
        # Left column: Files
        left_col = ctk.CTkFrame(content, fg_color="transparent", width=280)
        left_col.pack(side="left", fill="both", padx=(0, 8))
        left_col.pack_propagate(False)
        
        self.file_panel = FileListPanel(left_col, on_change=self._on_files_changed)
        self.file_panel.pack(fill="both", expand=True)
        
        # Middle column: Configuration tabs
        mid_col = ctk.CTkFrame(content, fg_color="transparent")
        mid_col.pack(side="left", fill="both", expand=True, padx=8)
        
        self.tabview = ctk.CTkTabview(
            mid_col, fg_color=COLORS["bg_secondary"],
            segmented_button_fg_color=COLORS["bg_tertiary"],
            segmented_button_selected_color=COLORS["accent_blue"],
            segmented_button_selected_hover_color=COLORS["accent_blue_hover"],
            segmented_button_unselected_color=COLORS["bg_tertiary"],
            segmented_button_unselected_hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=8
        )
        self.tabview.pack(fill="both", expand=True)
        
        # Create tabs
        tab_columns = self.tabview.add("Columns")
        tab_sort = self.tabview.add("Sort")
        tab_dedupe = self.tabview.add("Dedupe")
        tab_filter = self.tabview.add("Filter")
        tab_transform = self.tabview.add("Transform")
        tab_quality = self.tabview.add("Quality")
        tab_output = self.tabview.add("Output")
        
        # Tab contents
        self.column_panel = ColumnPanel(
            tab_columns, fg_color="transparent", on_order_change=self._schedule_preview
        )
        self.column_panel.pack(fill="both", expand=True)
        
        self.sort_panel = SortPanel(tab_sort, fg_color="transparent")
        self.sort_panel.pack(fill="both", expand=True)
        
        self.dedupe_panel = DedupePanel(tab_dedupe, fg_color="transparent")
        self.dedupe_panel.pack(fill="both", expand=True)
        
        self.filter_panel = FilterPanel(tab_filter, fg_color="transparent")
        self.filter_panel.pack(fill="both", expand=True)
        
        self.transform_panel = TransformPanel(tab_transform, fg_color="transparent")
        self.transform_panel.pack(fill="both", expand=True)

        self.quality_panel = QualityPanel(
            tab_quality,
            on_profile=self._profile_quality,
            on_inspect=self._inspect_quality_row,
            on_edit=self._quality_edits_changed,
            fg_color="transparent",
        )
        self.quality_panel.pack(fill="both", expand=True)
        
        self.output_panel = OutputPanel(tab_output, fg_color="transparent")
        self.output_panel.pack(fill="both", expand=True)
        
        # Right column: Log and Stats
        right_col = ctk.CTkFrame(content, fg_color="transparent", width=300)
        right_col.pack(side="right", fill="both", padx=(8, 0))
        right_col.pack_propagate(False)
        
        self.stats_panel = StatsPanel(right_col)
        self.stats_panel.pack(fill="x", pady=(0, 8))

        self.preview_panel = PreviewPanel(
            right_col,
            on_refresh=self._refresh_preview,
            on_cancel=self._cancel_preview,
            height=260,
        )
        self.preview_panel.pack(fill="x", pady=(0, 8))
        self.preview_panel.pack_propagate(False)
        
        self.log_panel = LogPanel(right_col)
        self.log_panel.pack(fill="both", expand=True)
        
        # Bottom: Progress and buttons
        bottom = ctk.CTkFrame(main, fg_color="transparent")
        bottom.pack(fill="x", pady=(12, 0))
        
        # Progress
        progress_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        progress_frame.pack(fill="x", pady=(0, 8))
        
        self.progress_label = ctk.CTkLabel(
            progress_frame, text="Ready",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_muted"]
        )
        self.progress_label.pack(anchor="w", pady=(0, 4))
        
        self.progress_bar = ctk.CTkProgressBar(
            progress_frame, height=8,
            fg_color=COLORS["bg_tertiary"],
            progress_color=COLORS["accent_green"],
            corner_radius=4
        )
        self.progress_bar.pack(fill="x")
        self.progress_bar.set(0)
        
        # Buttons
        btn_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        btn_frame.pack(fill="x")
        
        # Preset buttons
        preset_frame = ctk.CTkFrame(btn_frame, fg_color="transparent")
        preset_frame.pack(side="left")
        
        ctk.CTkButton(
            preset_frame, text="💾 Save Config", font=ctk.CTkFont(size=11),
            height=36, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=6, command=self._save_config
        ).pack(side="left", padx=(0, 4))
        
        ctk.CTkButton(
            preset_frame, text="📂 Load Config", font=ctk.CTkFont(size=11),
            height=36, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=6, command=self._load_config
        ).pack(side="left")

        self.undo_btn = ctk.CTkButton(
            preset_frame, text="↶ Undo", font=ctk.CTkFont(size=11),
            height=36, width=64, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"], text_color=COLORS["text_secondary"],
            corner_radius=6, command=self._undo, state="disabled",
        )
        self.undo_btn.pack(side="left", padx=(8, 2))

        self.redo_btn = ctk.CTkButton(
            preset_frame, text="↷ Redo", font=ctk.CTkFont(size=11),
            height=36, width=64, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["bg_hover"], text_color=COLORS["text_secondary"],
            corner_radius=6, command=self._redo, state="disabled",
        )
        self.redo_btn.pack(side="left", padx=(2, 0))
        
        # Action buttons
        action_frame = ctk.CTkFrame(btn_frame, fg_color="transparent")
        action_frame.pack(side="right")
        
        self.cancel_btn = ctk.CTkButton(
            action_frame, text="Cancel", font=ctk.CTkFont(size=13),
            height=42, width=100, fg_color=COLORS["bg_tertiary"],
            hover_color=COLORS["accent_red"],
            text_color=COLORS["text_primary"],
            corner_radius=8, command=self._cancel,
            state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=(0, 8))
        
        self.process_btn = ctk.CTkButton(
            action_frame, text="▶  Process Files",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=42, width=160,
            fg_color=COLORS["accent_green"],
            hover_color=COLORS["accent_green_hover"],
            corner_radius=8, command=self._process
        )
        self.process_btn.pack(side="left")
    
    def _setup_dnd(self):
        def drop(event):
            files = self.root.tk.splitlist(event.data)
            added = self.file_panel.add_files([Path(f) for f in files])
            if added:
                self.log_panel.log(f"Added {added} file(s) via drag & drop", "info")
        
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind('<<Drop>>', drop)
    
    def _on_files_changed(self):
        if self.file_panel.files:
            columns = CSVEngine(ProcessingConfig()).discover_columns_bounded(self.file_panel.files)
            self.column_panel.set_columns(columns)
            self.sort_panel.set_columns(columns)
            self.dedupe_panel.set_columns(columns)
            self.filter_panel.set_columns(columns)
            self.transform_panel.set_columns(columns)
            if hasattr(self, "quality_panel"):
                self.quality_panel.reset_profile()
            self._schedule_preview()
        else:
            self.preview_panel.reset()
            if hasattr(self, "quality_panel"):
                self.quality_panel.reset_profile()

    def _set_appearance_mode(self, value: str):
        mode = value.lower()
        ctk.set_appearance_mode(mode)
        active_mode = ctk.get_appearance_mode().lower() if mode == "system" else mode
        COLORS.update(LIGHT_COLORS if active_mode == "light" else DARK_COLORS)
        config_data = self._config_to_data()
        files = list(self.file_panel.files)
        for child in self.root.winfo_children():
            child.destroy()
        self.root.configure(bg=COLORS["bg_dark"])
        self._build_ui()
        self._apply_config_data(config_data)
        self.file_panel.files = files
        self.file_panel._refresh()
        if files:
            self._on_files_changed()
        self._update_history_buttons()

    def _schedule_preview(self):
        if not hasattr(self, "preview_panel"):
            return
        if self._preview_job is not None:
            try:
                self.root.after_cancel(self._preview_job)
            except Exception:
                pass
        self._preview_job = self.root.after(250, self._refresh_preview)

    def _cancel_preview(self):
        self._preview_generation += 1
        if self._preview_engine is not None:
            self._preview_engine.cancel()
        if hasattr(self, "preview_panel"):
            self.preview_panel.status_label.configure(text="Cancelling read-only preview…")

    def _preview_progress(self, generation: int, value: float, status: str):
        if generation != self._preview_generation:
            return
        self.root.after(
            0,
            lambda: self.preview_panel.status_label.configure(
                text=f"{status} ({value:.0f}%)"
            ) if generation == self._preview_generation else None,
        )

    def _refresh_preview(self):
        self._preview_job = None
        if self.processing or not self.file_panel.files:
            return
        if self._preview_engine is not None:
            self._preview_engine.cancel()
        files = list(self.file_panel.files)
        config = self._build_config()
        self._preview_generation += 1
        generation = self._preview_generation
        self.preview_panel.status_label.configure(text="Preparing bounded read-only preview…")

        def run():
            preview_engine = CSVEngine(
                config,
                progress_callback=lambda value, status: self._preview_progress(
                    generation, value, status
                ),
            )
            self._preview_engine = preview_engine
            try:
                preview = preview_engine.preview(files, limit=100)
                self.root.after(
                    0,
                    lambda: self.preview_panel.update_preview(preview)
                    if generation == self._preview_generation else None,
                )
            except Exception as exc:
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda message=error_message: self.preview_panel.reset(f"Preview error: {message}")
                    if generation == self._preview_generation else None,
                )
            finally:
                if self._preview_engine is preview_engine:
                    self._preview_engine = None

        threading.Thread(target=run, daemon=True).start()

    def _profile_quality(self):
        if self.processing:
            self.quality_panel.profile_status.configure(text="Finish or cancel processing before profiling")
            return
        if not self.file_panel.files:
            self.quality_panel.profile_status.configure(text="Add files before profiling")
            return
        try:
            filter_column, filter_value = self.quality_panel.get_facet_filter()
        except QualityError as exc:
            self.quality_panel.profile_status.configure(text=str(exc))
            return
        if self._quality_engine is not None:
            self._quality_engine.cancel()
        self._quality_generation += 1
        generation = self._quality_generation
        files = list(self.file_panel.files)
        config = self._build_config()
        self.quality_panel.profile_status.configure(text="Profiling bounded raw rows…")

        def progress(value, status):
            self.root.after(
                0,
                lambda: self.quality_panel.profile_status.configure(
                    text=f"{status} ({value:.0f}%)"
                ) if generation == self._quality_generation else None,
            )

        def run():
            quality_engine = CSVEngine(config, progress_callback=progress)
            self._quality_engine = quality_engine
            try:
                report = quality_engine.profile(
                    files,
                    scan_limit=config.quality_scan_rows,
                    facet_limit=config.quality_facet_limit,
                    max_distinct_values=config.quality_max_distinct_values,
                    sample_limit=config.quality_sample_limit,
                    filter_column=filter_column,
                    filter_value=filter_value,
                )
                self.root.after(
                    0,
                    lambda: self.quality_panel.update_profile(report)
                    if generation == self._quality_generation else None,
                )
                if quality_engine.stats.errors:
                    message = quality_engine.stats.errors[0]
                    self.root.after(
                        0,
                        lambda: self.quality_panel.profile_status.configure(
                            text=f"Profile completed with an input error: {message}"
                        ) if generation == self._quality_generation else None,
                    )
            except Exception as exc:
                message = str(exc)
                self.root.after(
                    0,
                    lambda: self.quality_panel.profile_status.configure(
                        text=f"Profile error: {message}"
                    ) if generation == self._quality_generation else None,
                )
            finally:
                if self._quality_engine is quality_engine:
                    self._quality_engine = None

        threading.Thread(target=run, daemon=True).start()

    def _inspect_quality_row(self, row_number):
        if self.processing:
            self.quality_panel.profile_status.configure(text="Finish or cancel processing before inspecting")
            return
        if not self.file_panel.files:
            self.quality_panel.profile_status.configure(text="Add files before inspecting a row")
            return
        if self._quality_engine is not None:
            self._quality_engine.cancel()
        self._quality_generation += 1
        generation = self._quality_generation
        files = list(self.file_panel.files)
        config = self._build_config()
        self.quality_panel.profile_status.configure(text=f"Inspecting raw row {row_number}…")

        def run():
            quality_engine = CSVEngine(config)
            self._quality_engine = quality_engine
            try:
                inspection = quality_engine.inspect_row(files, row_number)
                self.root.after(
                    0,
                    lambda: self.quality_panel.update_inspection(inspection)
                    if generation == self._quality_generation else None,
                )
                self.root.after(
                    0,
                    lambda: self.quality_panel.profile_status.configure(
                        text=(f"Inspected raw row {inspection['row_number']:,}"
                              if inspection else "Row not found")
                    ) if generation == self._quality_generation else None,
                )
            except Exception as exc:
                message = str(exc)
                self.root.after(
                    0,
                    lambda: self.quality_panel.profile_status.configure(
                        text=f"Inspection error: {message}"
                    ) if generation == self._quality_generation else None,
                )
            finally:
                if self._quality_engine is quality_engine:
                    self._quality_engine = None

        threading.Thread(target=run, daemon=True).start()

    def _quality_edits_changed(self, _edits):
        self._on_ui_edit()

    def _update_progress(self, value: float, status: str):
        self.progress_bar.set(value / 100)
        self.progress_label.configure(text=status)
        self.root.update_idletasks()
    
    def _build_config(self) -> ProcessingConfig:
        config = ProcessingConfig()
        
        # Columns
        mode, selected = self.column_panel.get_config()
        config.columns_mode = mode
        config.selected_columns = selected
        config.column_order = self.column_panel.get_column_order()
        
        # Sort
        enabled, rules, case_sens, numeric = self.sort_panel.get_config()
        config.sort_enabled = enabled
        config.sort_columns = rules
        config.sort_case_sensitive = case_sens
        config.sort_numeric_aware = numeric
        
        # Dedupe
        (
            enabled,
            columns,
            keep,
            fuzzy_enabled,
            fuzzy_threshold,
            aggregate_mode,
            aggregate_separator,
        ) = self.dedupe_panel.get_config()
        config.dedupe_enabled = enabled
        config.dedupe_columns = columns
        config.dedupe_keep = keep
        config.dedupe_fuzzy_enabled = fuzzy_enabled
        config.dedupe_fuzzy_threshold = fuzzy_threshold
        config.dedupe_aggregate_mode = aggregate_mode
        config.dedupe_aggregate_separator = aggregate_separator
        
        # Filter
        filters, logic = self.filter_panel.get_config()
        config.filters = filters
        config.filter_logic = logic
        
        # Transform
        trim, case, empty = self.transform_panel.get_config()
        config.trim_whitespace = trim
        config.case_transform = case
        config.empty_value = empty
        config.header_normalize = self.transform_panel.get_header_normalize()
        config.column_transforms = self.transform_panel.get_column_transforms()

        config.repair_edits = self.quality_panel.get_edits()

        # Output
        output_config = self.output_panel.get_config()
        config.output_delimiter = output_config["delimiter"]
        config.output_encoding = output_config["encoding"]
        config.output_quoting = output_config["quoting"]
        config.include_header = output_config["include_header"]
        config.line_ending = output_config["line_ending"]

        for key, value in self._config_overrides.items():
            if hasattr(config, key):
                setattr(config, key, copy.deepcopy(value))

        return config

    def _config_to_data(self) -> dict:
        return asdict(self._build_config())

    def _apply_config_data(self, data: dict):
        self._config_overrides = {
            key: copy.deepcopy(value)
            for key, value in data.items()
            if key not in {
                "columns_mode", "selected_columns", "column_order",
                "sort_enabled", "sort_columns", "sort_case_sensitive", "sort_numeric_aware",
                "dedupe_enabled", "dedupe_columns", "dedupe_keep", "dedupe_fuzzy_enabled",
                "dedupe_fuzzy_threshold", "dedupe_aggregate_mode", "dedupe_aggregate_separator",
                "filters", "filter_logic", "trim_whitespace", "case_transform", "empty_value",
                "header_normalize", "column_transforms", "output_delimiter", "output_encoding",
                "output_quoting", "include_header", "line_ending", "repair_edits",
            }
        }

        self.column_panel.mode.set(data.get("columns_mode", "all"))
        self.column_panel.selected = set(data.get("selected_columns", []))
        requested_order = data.get("column_order", [])
        if requested_order:
            known = [column for column in requested_order if column in self.column_panel.columns]
            self.column_panel.columns = known + [
                column for column in self.column_panel.columns if column not in known
            ]
        self.column_panel._refresh()

        self.sort_panel.enabled.set(data.get("sort_enabled", False))
        self.sort_panel.sort_rules = [tuple(rule) for rule in data.get("sort_columns", [])]
        self.sort_panel.case_sensitive.set(data.get("sort_case_sensitive", False))
        self.sort_panel.numeric_aware.set(data.get("sort_numeric_aware", True))
        self.sort_panel._refresh_rules()

        self.dedupe_panel.enabled.set(data.get("dedupe_enabled", True))
        self.dedupe_panel.selected_columns = set(data.get("dedupe_columns", []))
        self.dedupe_panel.keep_mode.set(data.get("dedupe_keep", "first"))
        self.dedupe_panel.use_all_columns.set(not bool(data.get("dedupe_columns", [])))
        self.dedupe_panel.fuzzy_enabled.set(data.get("dedupe_fuzzy_enabled", False))
        self.dedupe_panel.fuzzy_threshold.set(data.get("dedupe_fuzzy_threshold", 90))
        self.dedupe_panel.aggregate_mode.set(data.get("dedupe_aggregate_mode", "none"))
        self.dedupe_panel.aggregate_separator.set(data.get("dedupe_aggregate_separator", "; "))
        self.dedupe_panel._toggle_column_selection()

        self.filter_panel.filters = [tuple(rule) for rule in data.get("filters", [])]
        self.filter_panel.logic.set(data.get("filter_logic", "and"))
        self.filter_panel._refresh()

        self.transform_panel.trim_whitespace.set(data.get("trim_whitespace", True))
        self.transform_panel.case_transform.set(data.get("case_transform", "none"))
        self.transform_panel.empty_value.set(data.get("empty_value", ""))
        self.transform_panel.header_normalize.set(data.get("header_normalize", "none"))
        self.transform_panel.column_transforms = [tuple(item) for item in data.get("column_transforms", [])]
        self.transform_panel._refresh_transforms()

        delimiter = data.get("output_delimiter", ",")
        self.output_panel.delimiter.set("\\t (Tab)" if delimiter == "\t" else delimiter)
        self.output_panel.encoding.set(data.get("output_encoding", "utf-8"))
        self.output_panel.quoting.set(data.get("output_quoting", "minimal"))
        self.output_panel.include_header.set(data.get("include_header", True))
        line_ending = data.get("line_ending", "auto")
        self.output_panel.line_ending.set(
            "unix (LF)" if line_ending == "unix" else
            "windows (CRLF)" if line_ending == "windows" else "auto"
        )
        self.quality_panel.set_edits(data.get("repair_edits", []))

    def _on_ui_edit(self, _event=None):
        if self.history is None or self.processing:
            return
        self.history.record(self._config_to_data())
        self._update_history_buttons()
        self._schedule_preview()

    def _update_history_buttons(self):
        if not self.history or not hasattr(self, "undo_btn"):
            return
        self.undo_btn.configure(state="normal" if self.history.can_undo else "disabled")
        self.redo_btn.configure(state="normal" if self.history.can_redo else "disabled")

    def _undo(self):
        state = self.history.undo() if self.history else None
        if state is not None:
            self._apply_config_data(state)
            self._update_history_buttons()
            self.log_panel.log("Preset edit undone", "info")
            self._schedule_preview()

    def _redo(self):
        state = self.history.redo() if self.history else None
        if state is not None:
            self._apply_config_data(state)
            self._update_history_buttons()
            self.log_panel.log("Preset edit redone", "info")
            self._schedule_preview()
    
    def _process(self):
        if not self.file_panel.files:
            self.log_panel.log("No files selected", "error")
            return
        
        output_config = self.output_panel.get_config()
        output_path = output_config["output_path"]
        
        if not output_path:
            output_path = str(Path.home() / "combined_output.csv")
            self.output_panel.output_path.set(output_path)
        
        self.processing = True
        self._set_ui_state(True)
        self.stats_panel.reset()
        self.progress_bar.set(0)
        self.log_panel.clear()
        self.log_panel.log("Starting processing...", "info")
        
        config = self._build_config()
        
        def run():
            self.engine = self.engine_service.create_engine(
                config,
                progress_callback=lambda v, s: self.root.after(0, lambda: self._update_progress(v, s)),
                log_callback=lambda m, level: self.root.after(0, lambda: self.log_panel.log(m, level))
            )
            
            stats = self.engine.process(self.file_panel.files, Path(output_path))
            self.root.after(0, lambda: self._complete(stats))
        
        threading.Thread(target=run, daemon=True).start()
    
    def _cancel(self):
        if self.engine:
            self.engine.cancel()
            self.log_panel.log("Cancelling...", "warning")
        if self._quality_engine:
            self._quality_generation += 1
            self._quality_engine.cancel()
            self.quality_panel.profile_status.configure(text="Cancelling quality operation…")
    
    def _complete(self, stats: ProcessingStats):
        self.processing = False
        self._set_ui_state(False)
        self.stats_panel.update(stats)
        
        if stats.final_row_count > 0:
            self.log_panel.log(f"✓ Complete! {stats.final_row_count:,} rows saved", "success")
        else:
            self.log_panel.log("Processing completed with no output", "warning")
    
    def _set_ui_state(self, processing: bool):
        state = "disabled" if processing else "normal"
        self.process_btn.configure(state=state)
        self.cancel_btn.configure(state="normal" if processing else "disabled")
        if hasattr(self, "quality_panel"):
            self.quality_panel.profile_status.configure(
                text="Processing in progress" if processing else self.quality_panel.profile_status.cget("text")
            )
    
    def _save_config(self):
        file = filedialog.asksaveasfilename(
            title="Save Configuration",
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")]
        )
        if file:
            data = self._config_to_data()
            workflow = build_workflow(
                data,
                [str(path) for path in self.file_panel.files],
                self.output_panel.output_path.get(),
                APP_VERSION,
                input_files=self.file_panel.files,
            )
            try:
                write_workflow(file, workflow)
                record = append_history(self.workflow_history_path, workflow)
            except WorkflowError as exc:
                self.log_panel.log(f"Workflow save error: {exc}", "error")
                return
            if self.history:
                self.history.record(data)
                self._update_history_buttons()
            changed = ", ".join(record["changed_fields"]) if record["changed_fields"] else "initial workflow"
            self.log_panel.log(
                f"Workflow saved: {Path(file).name} ({len(workflow['operations'])} operations; {changed})",
                "success",
            )
    
    def _load_config(self):
        file = filedialog.askopenfilename(
            title="Load Configuration",
            filetypes=[("JSON Files", "*.json")]
        )
        if file:
            try:
                workflow = load_workflow(file)
                data = extract_config(workflow)
                self._apply_config_data(data)
                replay_output = workflow_output(workflow)
                if replay_output:
                    self.output_panel.output_path.set(replay_output)
                replay_files = [Path(path) for path in workflow_inputs(workflow) if Path(path).is_file()]
                if replay_files:
                    self.file_panel.files = replay_files
                    self.file_panel._refresh()
                    self._on_files_changed()
                if self.history:
                    self.history.record(self._config_to_data())
                    self._update_history_buttons()
                
                self.log_panel.log(
                    f"Workflow loaded: {Path(file).name} ({len(operation_types(workflow))} operations)",
                    "success",
                )
                self._schedule_preview()
                
            except WorkflowError as e:
                self.log_panel.log(f"Error loading workflow: {e}", "error")
    
    def run(self):
        self.root.mainloop()


# ══════════════════════════════════════════════════════════════════════════════
# CLI MODE
# ══════════════════════════════════════════════════════════════════════════════



# The HTTP transport now lives in csv_power_tool.api. Keep these names at the
# launcher boundary so existing imports and CLI callers remain compatible.
UploadRequestHandler = upload_api.UploadRequestHandler
UploadHTTPServer = upload_api.UploadHTTPServer
UploadRequestError = upload_api.UploadRequestError


def create_upload_server(
    config: ProcessingConfig,
    host: str = "127.0.0.1",
    port: int = 0,
    auth_token: str | None = None,
):
    return upload_api.create_upload_server(
        config,
        host,
        port,
        auth_token,
        engine_factory=CSVEngine,
        supported_input_suffixes=SUPPORTED_INPUT_SUFFIXES,
        app_name=APP_NAME,
        app_version=APP_VERSION,
    )


def serve_upload_server(
    config: ProcessingConfig,
    host: str = "127.0.0.1",
    port: int = 0,
    auth_token: str | None = None,
):
    return upload_api.serve_upload_server(
        config,
        host,
        port,
        auth_token,
        engine_factory=CSVEngine,
        supported_input_suffixes=SUPPORTED_INPUT_SUFFIXES,
        app_name=APP_NAME,
        app_version=APP_VERSION,
    )


def register_git_driver() -> None:
    """Register a local, unsigned three-way CSV merge driver in Git."""
    import subprocess

    script = Path(__file__).resolve()
    driver = (
        f'"{sys.executable}" "{script}" '
        "--three-way-base %O --three-way-ours %A --three-way-theirs %B --output %A"
    )
    commands = [
        ["git", "config", "--local", "merge.csvpower.name", "CSV Power Tool three-way merge"],
        ["git", "config", "--local", "merge.csvpower.driver", driver],
        ["git", "config", "--local", "merge.csvpower.recursive", "binary"],
    ]
    for command in commands:
        subprocess.run(command, check=True)
    print("Registered the local csvpower merge driver. Add '*.csv merge=csvpower' to .gitattributes.")


def cli_main(argv=None):
    """Headless CLI mode: csv_power_tool --config preset.json --inputs *.csv --output combined.csv"""
    import argparse
    import glob as globmod

    parser = argparse.ArgumentParser(
        prog="csv_power_tool",
        description="CSV Power Tool - Professional-grade CSV combiner and processor (CLI mode)",
    )
    parser.add_argument("--config", "-c", help="JSON preset configuration file")
    parser.add_argument("--replay", help="Replay a versioned workflow document")
    parser.add_argument("--save-workflow", help="Write the current run as a versioned workflow document")
    parser.add_argument("--workflow-history", help="Append successful runs to bounded workflow history")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the replayable workflow without writing output")
    parser.add_argument("--inputs", "-i", nargs="+",
                        help="Input files, folders, or glob patterns (e.g. *.csv)")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--delimiter", "-d", default=None, help="Output delimiter")
    parser.add_argument("--encoding", "-e", default=None, help="Output encoding")
    parser.add_argument("--no-header", action="store_true", help="Exclude header row")
    parser.add_argument("--collision-policy", choices=["replace", "fail", "backup"],
                        default=None, help="Existing output handling policy")
    parser.add_argument("--manifest", dest="run_manifest_path",
                        help="Write the audit manifest to this path instead of output.manifest.json")
    parser.add_argument("--no-manifest", action="store_true", help="Disable the audit manifest")
    parser.add_argument("--no-dedupe", action="store_true", help="Disable deduplication")
    parser.add_argument("--fuzzy-dedupe-threshold", type=int, metavar="50-100",
                        help="Enable fuzzy dedupe at the supplied similarity threshold")
    parser.add_argument("--dedupe-aggregate", choices=["max", "min", "sum", "concat"],
                        help="Aggregate non-key duplicate columns with the selected mode")
    parser.add_argument("--dedupe-aggregate-separator", default="; ",
                        help="Separator for --dedupe-aggregate concat")
    parser.add_argument("--dedupe-preview", help="Write a JSON duplicate preview without requiring an output file")
    parser.add_argument("--preview", help="Write a bounded, read-only projected preview JSON artifact")
    parser.add_argument("--preview-rows", type=int, default=100,
                        help="Rows to return in a bounded preview")
    parser.add_argument("--preview-scan-rows", type=int, default=5_000,
                        help="Maximum rows scanned by a bounded preview")
    parser.add_argument("--preview-scan-bytes", type=int, default=8 * 1024 * 1024,
                        help="Maximum sampled cell bytes retained by a bounded preview")
    parser.add_argument("--preview-columns", type=int, default=256,
                        help="Maximum columns retained by a bounded preview")
    parser.add_argument("--preview-cell-bytes", type=int, default=16 * 1024,
                        help="Maximum UTF-8 bytes retained per preview cell")
    parser.add_argument("--filter", action="append", metavar="COL:OP:VALUE",
                        help="Add a filter rule. Operators include between, fuzzy, regex, contains")
    parser.add_argument("--sort", nargs="+", metavar="COL[:asc|desc]",
                        help="Sort by columns, e.g. name:asc age:desc")
    parser.add_argument("--columns", nargs="+", help="Include only these columns")
    parser.add_argument("--exclude-columns", nargs="+", help="Exclude these columns")
    parser.add_argument("--header-normalize", choices=["none", "trim", "lowercase", "snake_case"],
                        default=None, help="Header normalization mode")
    parser.add_argument("--invalid-row-policy", choices=["fail", "warn", "quarantine"],
                        default=None, help="Malformed CSV/JSONL row handling policy")
    parser.add_argument("--quarantine", dest="quarantine_path",
                        help="Write quarantined malformed rows as JSON Lines")
    parser.add_argument("--max-input-bytes", type=int, help="Maximum bytes accepted per input file")
    parser.add_argument("--max-decompressed-bytes", type=int,
                        help="Maximum expanded workbook bytes")
    parser.add_argument("--max-input-rows", type=int, help="Maximum rows accepted per input file")
    parser.add_argument("--max-input-columns", type=int, help="Maximum columns accepted per input file")
    parser.add_argument("--max-cell-bytes", type=int, help="Maximum UTF-8 bytes accepted in one cell")
    parser.add_argument("--max-json-nesting", type=int, help="Maximum JSON nesting depth")
    parser.add_argument("--schema-mode", choices=["union", "intersection", "first_file"],
                        default=None, help="Schema unification mode")
    parser.add_argument("--backend", choices=["auto", "python", "polars"], default=None,
                        help="Text parsing backend; polars is recommended for large in-memory jobs")
    parser.add_argument("--stream-batch-rows", type=int, default=None,
                        help="Rows held per Parquet streaming batch")
    parser.add_argument("--column-template", help="Use this input's column order as the output template")
    parser.add_argument("--source-column", help="Add a provenance column containing each source file")
    parser.add_argument("--source-path", action="store_true", help="Store the full source path instead of the file name")
    parser.add_argument("--schema-report", help="Write a JSON schema-drift, sample, and type report")
    parser.add_argument("--schema-contract", help="Validate inputs against a Frictionless Table Schema JSON file")
    parser.add_argument("--export-schema", help="Infer and write a first-version Frictionless Table Schema JSON file")
    parser.add_argument("--validation-mode", choices=["strict", "advisory", "quarantine"], default=None,
                        help="Schema contract handling: fail, warn, or omit invalid rows")
    parser.add_argument("--validation-report", help="Write a machine-readable schema validation report")
    parser.add_argument("--validate-only", action="store_true",
                        help="Validate against --schema-contract without writing processed output")
    parser.add_argument("--profile", help="Write a bounded faceted data-quality profile JSON artifact")
    parser.add_argument("--quality-scan-rows", type=int, default=None,
                        help="Maximum rows scanned for a quality profile")
    parser.add_argument("--quality-facet-limit", type=int, default=None,
                        help="Top values retained per quality-profile facet")
    parser.add_argument("--quality-distinct-limit", type=int, default=None,
                        help="Maximum distinct values tracked per quality-profile column")
    parser.add_argument("--quality-sample-limit", type=int, default=None,
                        help="Sample values retained per quality-profile column")
    parser.add_argument("--quality-filter-column",
                        help="Profile only rows whose raw value matches --quality-filter-value")
    parser.add_argument("--quality-filter-value",
                        help="Exact raw value used with --quality-filter-column")
    parser.add_argument("--repair-edits", help="JSON file containing reviewed 1-based row/cell repairs")
    parser.add_argument("--repair-report", help="Write the applied repair report to this JSON path")
    parser.add_argument("--sql", help="Run DuckDB SQL against input_0, input_1, ...")
    parser.add_argument("--sql-file", help="Read the DuckDB SQL query from a UTF-8 file")
    parser.add_argument("--redact-sensitive", action="store_true", help="Redact likely email, phone, SSN, card, and secret fields")
    parser.add_argument("--redaction-token", default=None, help="Replacement text used by --redact-sensitive")
    parser.add_argument("--unpivot", nargs="+", metavar="COLUMN", help="Unpivot these columns into name/value rows")
    parser.add_argument("--unpivot-name", default="variable", help="Unpivoted column containing source column names")
    parser.add_argument("--unpivot-value", default="value", help="Unpivoted column containing source values")
    parser.add_argument("--pivot-index", nargs="+", metavar="COLUMN", help="Index columns for a pivot")
    parser.add_argument("--pivot-column", help="Column whose values become pivoted columns")
    parser.add_argument("--pivot-value", help="Column whose values populate pivoted columns")
    parser.add_argument("--pivot-aggregate", choices=["first", "sum", "min", "max", "concat"], default="first")
    parser.add_argument("--pivot-separator", default="; ", help="Separator for pivot concat aggregation")
    parser.add_argument("--join-on", nargs="+", metavar="COLUMN", help="Join two or more inputs on these columns")
    parser.add_argument(
        "--join-type", choices=["inner", "left", "right", "outer", "full", "anti", "semi"],
        default=None, help="Join policy; anti/semi return unmatched/matched left rows",
    )
    parser.add_argument("--join-report", help="Write a machine-readable join validation/conflict report")
    parser.add_argument("--join-conflict-policy", choices=["keep-both", "fail"], default=None,
                        help="Shared non-key cells: retain both values or fail safely")
    parser.add_argument("--key-normalization", choices=["exact", "trim", "casefold", "trim-casefold"],
                        default=None, help="Key normalization used by joins and three-way merges")
    parser.add_argument("--three-way-base", help="Base input for a three-way keyed merge")
    parser.add_argument("--three-way-ours", help="Ours input for a three-way keyed merge")
    parser.add_argument("--three-way-theirs", help="Theirs input for a three-way keyed merge")
    parser.add_argument("--key-columns", nargs="+", metavar="COLUMN", help="Key columns for a three-way merge")
    parser.add_argument("--merge-report", help="Write a machine-readable three-way merge report")
    parser.add_argument("--conflict-resolution", choices=["fail", "ours", "theirs", "base", "mark"], default=None,
                        help="Three-way conflict policy; fail requires explicit review before destructive resolution")
    parser.add_argument("--register-git-driver", action="store_true", help="Register the local csvpower merge driver")
    parser.add_argument("--serve", action="store_true", help="Start the loopback-only upload API")
    parser.add_argument("--host", default="127.0.0.1", help="Upload API host; only localhost is accepted")
    parser.add_argument("--port", type=int, default=0, help="Upload API port; 0 selects an available port")
    parser.add_argument("--upload-token", help="Use this upload API token; otherwise generate one per server run")
    parser.add_argument("--no-stream", action="store_true", help="Disable bounded-memory streaming path")
    parser.add_argument("--watch", action="store_true",
                        help="Watch inputs and re-run whenever matching files change")
    parser.add_argument("--watch-interval", type=float, default=2.0,
                        help="Polling interval for --watch, in seconds")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress log output")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} v{APP_VERSION}")

    args = parser.parse_args(argv)

    if args.register_git_driver:
        try:
            register_git_driver()
        except Exception as exc:
            print(f"Error registering git driver: {exc}", file=sys.stderr)
            sys.exit(3)
        return

    if args.config and args.replay:
        parser.error("--config and --replay are mutually exclusive")

    workflow_source = args.replay or args.config
    workflow_document = None
    if workflow_source:
        try:
            workflow_document = load_workflow(workflow_source)
            if not args.inputs:
                args.inputs = workflow_inputs(workflow_document)
            if not args.output:
                args.output = workflow_output(workflow_document)
        except WorkflowError as exc:
            parser.error(str(exc))

    if (not args.serve and not args.inputs and not args.dry_run and not args.validate_only
            and not args.export_schema
            and not (args.three_way_base or args.three_way_ours or args.three_way_theirs)):
        parser.error("--inputs, --config, or --replay is required unless --register-git-driver is used")
    if not args.serve and not args.dry_run and not args.output and not args.dedupe_preview \
            and not args.preview and not args.profile and not args.validate_only and not args.export_schema:
        parser.error("--output is required unless --dry-run or --dedupe-preview is used")
    if (args.join_on or args.three_way_base or args.three_way_ours or args.three_way_theirs) and not args.output:
        parser.error("--output is required for join and three-way merge operations")
    if args.sql and args.sql_file:
        parser.error("--sql and --sql-file are mutually exclusive")
    if args.join_report and not args.join_on:
        parser.error("--join-report requires --join-on")
    if args.merge_report and not (args.three_way_base or args.three_way_ours or args.three_way_theirs):
        parser.error("--merge-report requires three-way merge inputs")

    def expand_inputs(patterns):
        input_files = []
        seen = set()
        for pattern in patterns:
            p = Path(pattern)
            candidates = []
            if p.is_dir():
                candidates = [child for child in p.rglob("*") if child.is_file()]
            else:
                expanded = globmod.glob(pattern, recursive=True)
                if expanded:
                    candidates = [Path(f) for f in expanded]
                elif p.exists():
                    candidates = [p]
                else:
                    print(f"Warning: No files matched: {pattern}", file=sys.stderr)

            for candidate in candidates:
                if candidate.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
                    continue
                resolved = candidate.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    input_files.append(candidate)
        return sorted(input_files, key=lambda path: str(path).lower())

    # Build config
    config = ProcessingConfig()

    # Load preset if provided
    if workflow_document is not None:
        try:
            data = extract_config(workflow_document)
            for key, value in data.items():
                if hasattr(config, key):
                    setattr(config, key, value)
        except WorkflowError as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            sys.exit(3)

    if args.schema_contract:
        try:
            config.schema_contract = load_schema(args.schema_contract)
        except SchemaError as exc:
            parser.error(str(exc))
    elif config.schema_contract:
        try:
            config.schema_contract = normalize_schema(config.schema_contract)
        except SchemaError as exc:
            parser.error(str(exc))
    if args.validation_mode is not None:
        config.schema_validation_mode = args.validation_mode
    if args.validation_report:
        config.schema_validation_report_path = args.validation_report
    if args.validate_only:
        config.schema_validate_only = True
    if (args.validation_mode is not None or args.validation_report or config.schema_validate_only) \
            and not config.schema_contract:
        parser.error("--schema-contract or a workflow schema_contract is required for schema validation")
    if args.repair_edits:
        try:
            config.repair_edits = load_repairs(args.repair_edits)
        except QualityError as exc:
            parser.error(str(exc))
    if args.repair_report:
        config.repair_report_path = args.repair_report
    if args.repair_report and not config.repair_edits:
        parser.error("--repair-report requires --repair-edits or workflow repair_edits")
    if config.repair_edits and not args.output:
        parser.error("Reviewed repairs require --output")
    if bool(args.quality_filter_column) != bool(args.quality_filter_value):
        parser.error("--quality-filter-column and --quality-filter-value must be provided together")
    if config.repair_edits and (args.sql or args.sql_file or args.join_on
                                or args.three_way_base or args.three_way_ours or args.three_way_theirs):
        parser.error("Reviewed repairs are supported by the normal processing path only")
    if args.key_normalization is not None:
        config.key_normalization = args.key_normalization
    if args.join_type is not None:
        config.join_type = args.join_type
    if args.join_on:
        config.join_key_columns = list(args.join_on)
    if args.join_conflict_policy is not None:
        config.join_conflict_policy = args.join_conflict_policy
    if args.join_report:
        config.join_report_path = args.join_report
    if args.key_columns:
        config.merge_key_columns = list(args.key_columns)
    if args.conflict_resolution is not None:
        config.merge_conflict_resolution = args.conflict_resolution
    if args.merge_report:
        config.merge_report_path = args.merge_report

    # Apply CLI overrides
    if args.delimiter:
        delim = args.delimiter
        if delim.lower() == "tab" or delim == "\\t":
            delim = "\t"
        config.output_delimiter = delim

    if args.encoding:
        config.output_encoding = args.encoding

    if args.no_header:
        config.include_header = False
    if args.collision_policy is not None:
        config.output_collision_policy = args.collision_policy
    if args.no_manifest:
        config.run_manifest_enabled = False
    if args.run_manifest_path:
        config.run_manifest_path = args.run_manifest_path

    if args.no_dedupe:
        config.dedupe_enabled = False

    if args.fuzzy_dedupe_threshold is not None:
        if not 50 <= args.fuzzy_dedupe_threshold <= 100:
            print("Error: --fuzzy-dedupe-threshold must be between 50 and 100", file=sys.stderr)
            sys.exit(3)
        config.dedupe_enabled = True
        config.dedupe_fuzzy_enabled = True
        config.dedupe_fuzzy_threshold = args.fuzzy_dedupe_threshold

    if args.dedupe_aggregate:
        config.dedupe_enabled = True
        config.dedupe_aggregate_mode = args.dedupe_aggregate
        config.dedupe_aggregate_separator = args.dedupe_aggregate_separator

    if args.columns:
        config.columns_mode = "select"
        config.selected_columns = args.columns

    if args.exclude_columns:
        config.columns_mode = "exclude"
        config.selected_columns = args.exclude_columns

    if args.header_normalize is not None:
        config.header_normalize = args.header_normalize
    if args.invalid_row_policy is not None:
        config.invalid_row_policy = args.invalid_row_policy
    if args.quarantine_path:
        config.quarantine_path = args.quarantine_path
    for name in (
        "max_input_bytes",
        "max_decompressed_bytes",
        "max_input_rows",
        "max_input_columns",
        "max_cell_bytes",
        "max_json_nesting",
    ):
        value = getattr(args, name, None)
        if value is not None:
            setattr(config, name, value)
    if args.schema_mode is not None:
        config.schema_mode = args.schema_mode
    if args.backend is not None:
        config.engine_backend = args.backend
    if args.stream_batch_rows is not None:
        if args.stream_batch_rows < 1:
            parser.error("--stream-batch-rows must be at least 1")
        config.stream_batch_rows = args.stream_batch_rows
    for name, argument in (
        ("quality_scan_rows", args.quality_scan_rows),
        ("quality_facet_limit", args.quality_facet_limit),
        ("quality_max_distinct_values", args.quality_distinct_limit),
        ("quality_sample_limit", args.quality_sample_limit),
    ):
        if argument is not None:
            if argument < 1:
                parser.error(f"--{name.replace('_', '-')} must be at least 1")
            setattr(config, name, argument)
    if args.no_stream:
        config.streaming_enabled = False
    if args.column_template:
        config.column_template = args.column_template
    if args.source_column:
        config.source_column = args.source_column
        config.source_value = "path" if args.source_path else "name"
    if args.redact_sensitive:
        config.redact_sensitive = True
    if args.redaction_token is not None:
        config.redaction_token = args.redaction_token
    if args.unpivot:
        config.unpivot_columns = args.unpivot
        config.unpivot_name_column = args.unpivot_name
        config.unpivot_value_column = args.unpivot_value
    if args.pivot_column or args.pivot_value:
        if not args.pivot_column or not args.pivot_value:
            print("Error: --pivot-column and --pivot-value must be supplied together", file=sys.stderr)
            sys.exit(3)
        config.pivot_index_columns = args.pivot_index or []
        config.pivot_column = args.pivot_column
        config.pivot_value_column = args.pivot_value
        config.pivot_aggregate = args.pivot_aggregate
        config.pivot_separator = args.pivot_separator

    if args.filter:
        for spec in args.filter:
            parts = spec.split(":", 2)
            if len(parts) != 3:
                print(f"Error: Invalid filter format: {spec}", file=sys.stderr)
                sys.exit(3)
            col, operator, value = parts
            if operator not in CSVEngine.FILTER_OPERATORS:
                print(f"Error: Unknown filter operator: {operator}", file=sys.stderr)
                sys.exit(3)
            config.filters.append((col, operator, value))

    if args.sort:
        config.sort_enabled = True
        config.sort_columns = []
        for s in args.sort:
            if ":" in s:
                col, direction = s.rsplit(":", 1)
                ascending = direction.lower() != "desc"
            else:
                col = s
                ascending = True
            config.sort_columns.append((col, ascending))

    if args.serve:
        try:
            return serve_upload_server(config, args.host, args.port, args.upload_token)
        except (OSError, ValueError) as exc:
            print(f"Error starting upload server: {exc}", file=sys.stderr)
            return 3

    # Log callback
    def log_msg(message, level="info"):
        if args.quiet:
            return
        prefix = {"info": "  ", "success": "OK", "warning": "!!", "error": "XX"}.get(level, "  ")
        print(f"[{prefix}] {message}", file=sys.stderr)

    def progress_msg(value, status):
        if args.verbose and not args.quiet:
            print(f"  ... {status} ({value:.0f}%)", file=sys.stderr)

    output_path = Path(args.output) if args.output else None

    def current_workflow(input_files):
        patterns = args.inputs or [str(path) for path in input_files]
        metadata = {}
        if workflow_source:
            metadata["replay_source"] = str(workflow_source)
        return build_workflow(
            asdict(config),
            patterns,
            output_path,
            APP_VERSION,
            input_files=input_files,
            metadata=metadata,
        )

    def read_operation_file(engine, path):
        all_columns = set()
        column_order = []
        rows = engine._read_file(Path(path), all_columns, column_order)
        return rows, column_order

    def build_dedupe_preview(engine, input_files):
        all_rows = []
        all_columns = set()
        column_order = []
        for path in input_files:
            all_rows.extend(engine._read_file(path, all_columns, column_order))
        final_columns = engine._with_transform_columns(engine._get_final_columns(column_order))
        if config.filters:
            all_rows = engine._apply_filters(all_rows)
        all_rows = engine._apply_transformations(all_rows, final_columns)
        all_rows, final_columns = engine._apply_reshape(all_rows, final_columns)
        return {
            "columns": final_columns,
            "rows_read": len(all_rows),
            "preview": engine.preview_duplicates(all_rows, final_columns),
        }

    def write_operation_report(path, report):
        if not path:
            return True
        try:
            _write_json_atomic(path, report)
            return True
        except (OSError, TypeError, ValueError) as exc:
            print(f"Error writing operation report: {exc}", file=sys.stderr)
            return False

    def run_join(input_files):
        if len(input_files) < 2:
            print("Error: --join-on requires at least two input files", file=sys.stderr)
            return 3
        key_columns = list(args.join_on or config.join_key_columns)
        if not key_columns:
            print("Error: --join-on requires at least one key column", file=sys.stderr)
            return 3
        join_type = args.join_type or config.join_type
        engine = CSVEngine(config, progress_callback=progress_msg, log_callback=log_msg)
        engine._manifest_input_files = [Path(path) for path in input_files]
        left_rows, left_columns = read_operation_file(engine, input_files[0])
        stages = []
        aggregate_report = {
            "format": "csv-power-tool-join-report",
            "version": 1,
            "operation": "join",
            "join_type": join_type,
            "key_columns": key_columns,
            "key_normalization": config.key_normalization,
            "conflict_policy": config.join_conflict_policy,
            "stages": stages,
            "conflict_count": 0,
            "conflicts": [],
            "conflicts_truncated": False,
            "validation": {"valid": True, "errors": [], "warnings": []},
            "deterministic_order": "left input order, then right unmatched input order per stage",
        }
        for path in input_files[1:]:
            right_rows, right_columns = read_operation_file(engine, path)
            try:
                stage = analyze_join(
                    left_rows,
                    right_rows,
                    key_columns,
                    join_type,
                    config.key_normalization,
                    config.join_conflict_policy,
                )
            except JoinError as exc:
                print(f"Error validating join keys: {exc}", file=sys.stderr)
                return 3
            stage["left_source"] = stages[-1].get("right_source", input_files[0]) if stages else str(input_files[0])
            stage["right_source"] = str(path)
            stages.append(stage)
            aggregate_report["conflict_count"] += stage.get("conflict_count", 0)
            for conflict in stage.get("conflicts", []):
                if len(aggregate_report["conflicts"]) >= 1_000:
                    aggregate_report["conflicts_truncated"] = True
                    break
                aggregate_report["conflicts"].append({
                    **conflict,
                    "stage": len(stages),
                    "left_source": stage["left_source"],
                    "right_source": stage["right_source"],
                })
            aggregate_report["conflicts_truncated"] = (
                aggregate_report["conflicts_truncated"] or stage.get("conflicts_truncated", False)
            )
            aggregate_report["validation"]["errors"].extend(stage["validation"].get("errors", []))
            aggregate_report["validation"]["warnings"].extend(stage["validation"].get("warnings", []))
            if not stage["validation"]["valid"]:
                aggregate_report["validation"]["valid"] = False
                if not args.quiet:
                    print("XX Join key validation failed; no output was written", file=sys.stderr)
                engine.stats.join_report = aggregate_report
                write_operation_report(config.join_report_path, aggregate_report)
                return 3
            if config.join_conflict_policy == "fail" and stage.get("conflict_count"):
                aggregate_report["validation"]["valid"] = False
                aggregate_report["validation"]["errors"].append(
                    f"stage has {stage['conflict_count']:,} conflicting shared cell(s)"
                )
                engine.stats.join_report = aggregate_report
                write_operation_report(config.join_report_path, aggregate_report)
                print("Error: join conflict policy fail blocked output", file=sys.stderr)
                return 3
            try:
                left_rows, left_columns = CSVEngine.join_rows(
                    left_rows,
                    right_rows,
                    key_columns,
                    join_type,
                    key_normalization=config.key_normalization,
                    conflict_policy=config.join_conflict_policy,
                )
            except JoinError as exc:
                print(f"Error executing join: {exc}", file=sys.stderr)
                return 3

        if engine.stats.errors:
            aggregate_report["validation"]["valid"] = False
            aggregate_report["validation"]["errors"].extend(engine.stats.errors)
            engine.stats.join_report = aggregate_report
            write_operation_report(config.join_report_path, aggregate_report)
            return 3
        if config.filters:
            left_rows = engine._apply_filters(left_rows)
        left_rows = engine._apply_transformations(left_rows, left_columns)
        left_rows = [engine._redact_row(row) for row in left_rows]
        aggregate_report["output_columns"] = list(left_columns)
        aggregate_report["output_row_count"] = len(left_rows)
        aggregate_report["stage_count"] = len(stages)
        aggregate_report["conflicts_truncated"] = aggregate_report["conflicts_truncated"] or any(
            stage.get("conflicts_truncated", False) for stage in stages
        )
        engine.stats.join_report = aggregate_report
        if not write_operation_report(config.join_report_path, aggregate_report):
            return 3
        engine.stats.unique_columns = len(left_columns)
        engine.stats.final_row_count = len(left_rows)
        engine._compute_column_summary(left_rows, [config.column_mapping.get(c, c) for c in left_columns])
        engine._write_output(left_rows, left_columns, output_path)
        if not args.quiet:
            print(
                f"  Joined rows: {engine.stats.final_row_count:,}; "
                f"cardinality: {stages[-1].get('cardinality', 'n/a') if stages else 'n/a'}; "
                f"conflicts: {aggregate_report['conflict_count']:,}",
                file=sys.stderr,
            )
        return 3 if engine.stats.errors else 0

    def run_three_way_merge():
        paths = [args.three_way_base, args.three_way_ours, args.three_way_theirs]
        if not all(paths):
            print("Error: --three-way-base, --three-way-ours, and --three-way-theirs are required together", file=sys.stderr)
            return 3
        key_columns = list(args.key_columns or config.merge_key_columns)
        resolution = config.merge_conflict_resolution
        engine = CSVEngine(config, progress_callback=progress_msg, log_callback=log_msg)
        engine._manifest_input_files = [Path(path) for path in paths]
        loaded = [read_operation_file(engine, path)[0] for path in paths]
        try:
            report = analyze_three_way(
                loaded[0], loaded[1], loaded[2], key_columns,
                config.key_normalization, resolution,
            )
        except JoinError as exc:
            print(f"Error validating merge keys: {exc}", file=sys.stderr)
            return 3
        report.setdefault("sources", {})
        for side, path in zip(("base", "ours", "theirs"), paths):
            report["sources"].setdefault(side, {"side": side})["source"] = str(path)
        engine.stats.merge_report = report
        if not report["validation"]["valid"]:
            if not write_operation_report(config.merge_report_path, report):
                return 3
            print("Error: merge key validation failed; no output was written", file=sys.stderr)
            return 3
        if resolution == "fail" and report.get("conflict_count"):
            if not write_operation_report(config.merge_report_path, report):
                return 3
            print(
                f"Error: {report['conflict_count']:,} merge conflict(s) require explicit resolution; "
                "use --conflict-resolution ours|theirs|base|mark",
                file=sys.stderr,
            )
            return 3
        try:
            rows, conflicts, columns, report = CSVEngine.three_way_merge_rows(
                loaded[0],
                loaded[1],
                loaded[2],
                key_columns,
                resolution,
                key_normalization=config.key_normalization,
                return_diagnostics=True,
            )
        except JoinError as exc:
            print(f"Error executing three-way merge: {exc}", file=sys.stderr)
            return 3
        report.setdefault("sources", {})
        for side, path in zip(("base", "ours", "theirs"), paths):
            report["sources"].setdefault(side, {"side": side})["source"] = str(path)
        report["output_columns"] = list(columns)
        report["output_row_count"] = len(rows)
        engine.stats.merge_report = report
        if not write_operation_report(config.merge_report_path, report):
            return 3
        engine.stats.unique_columns = len(columns)
        engine.stats.final_row_count = len(rows)
        engine._compute_column_summary(rows, columns)
        engine._write_output(rows, columns, output_path)
        if conflicts and not args.quiet:
            print(
                f"!! Three-way merge produced {len(conflicts):,} conflict group(s) using {resolution}",
                file=sys.stderr,
            )
        return 3 if engine.stats.errors else 0

    def run_sql(input_files):
        query = args.sql
        if args.sql_file:
            try:
                query = Path(args.sql_file).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"Error reading --sql-file: {exc}", file=sys.stderr)
                return 3
        if not query or not query.strip():
            print("Error: SQL query cannot be empty", file=sys.stderr)
            return 3
        engine = CSVEngine(config, progress_callback=progress_msg, log_callback=log_msg)
        engine._manifest_input_files = [Path(path) for path in input_files]
        try:
            rows, columns = engine.sql_query(input_files, query)
        except Exception as exc:
            print(f"Error executing SQL: {exc}", file=sys.stderr)
            return 3
        engine.stats.files_processed = len(input_files)
        engine.stats.total_rows_read = len(rows)
        engine.stats.unique_columns = len(columns)
        engine.stats.final_row_count = len(rows)
        engine._compute_column_summary(rows, columns)
        engine._write_output(rows, columns, output_path)
        if not args.quiet:
            print(f"  SQL rows: {len(rows):,}", file=sys.stderr)
        return 3 if engine.stats.errors else 0

    def run_once(input_files):
        if not input_files:
            print("Error: No input files found", file=sys.stderr)
            return 1

        engine = CSVEngine(config, progress_callback=progress_msg, log_callback=log_msg)

        if args.export_schema:
            all_rows = []
            all_columns = set()
            column_order = []
            for path in input_files:
                all_rows.extend(engine._read_file(path, all_columns, column_order))
            try:
                write_schema(args.export_schema, infer_schema(all_rows, column_order))
            except (OSError, SchemaError, ValueError) as exc:
                print(f"Error exporting schema: {exc}", file=sys.stderr)
                return 3
            if not args.quiet:
                print(f"  Schema contract: {args.export_schema}", file=sys.stderr)
            if not args.output:
                return 3 if engine.stats.errors else 0

        if config.schema_validate_only:
            stats = engine.validate_schema(input_files)
            if not args.quiet:
                report = stats.schema_validation
                print(
                    f"  Schema validation: {report.get('error_count', 0):,} error(s), "
                    f"{report.get('valid_row_count', 0):,} valid row(s)",
                    file=sys.stderr,
                )
            return 3 if stats.errors else 0

        if args.sql or args.sql_file:
            return run_sql(input_files)

        if args.schema_report:
            engine.write_schema_report(input_files, Path(args.schema_report))
            if not args.quiet:
                print(f"  Schema report: {args.schema_report}", file=sys.stderr)

        if args.profile:
            quality_profile = engine.profile(
                input_files,
                scan_limit=config.quality_scan_rows,
                facet_limit=config.quality_facet_limit,
                max_distinct_values=config.quality_max_distinct_values,
                sample_limit=config.quality_sample_limit,
                filter_column=args.quality_filter_column,
                filter_value=args.quality_filter_value,
            )
            try:
                write_quality_report(args.profile, quality_profile)
            except (OSError, TypeError, ValueError) as exc:
                print(f"Error writing quality profile: {exc}", file=sys.stderr)
                return 3
            if not args.quiet:
                print(
                    f"  Quality profile: {args.profile} ({quality_profile['rows_scanned']:,} row(s))",
                    file=sys.stderr,
                )
            if not args.output:
                return 3 if engine.stats.errors else 0

        if args.preview:
            preview = engine.preview(
                input_files,
                limit=args.preview_rows,
                budget=PreviewBudget(
                    row_limit=args.preview_rows,
                    scan_row_limit=args.preview_scan_rows,
                    scan_byte_limit=args.preview_scan_bytes,
                    column_limit=args.preview_columns,
                    cell_byte_limit=args.preview_cell_bytes,
                ),
            )
            preview_payload = {
                "format": "csv-power-tool-preview",
                "version": 1,
                "columns": preview["columns"],
                "rows": preview["rows"],
                "metadata": preview["metadata"],
                "stats": asdict(preview["stats"]),
            }
            try:
                _write_json_atomic(args.preview, preview_payload)
            except (OSError, TypeError, ValueError) as exc:
                print(f"Error writing bounded preview: {exc}", file=sys.stderr)
                return 3
            if not args.quiet:
                print(
                    f"  Bounded preview: {args.preview} ({len(preview['rows']):,} row(s), "
                    f"{preview['metadata']['rows_scanned']:,} scanned)",
                    file=sys.stderr,
                )
            if not args.output:
                return 3 if engine.stats.errors else 0

        if args.dedupe_preview:
            preview_report = build_dedupe_preview(engine, input_files)
            preview_path = Path(args.dedupe_preview)
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            with open(preview_path, "w", encoding="utf-8") as handle:
                json.dump(preview_report, handle, indent=2, ensure_ascii=False)
            if not args.quiet:
                print(
                    f"  Dedupe preview: {preview_report['preview']['duplicate_count']:,} row(s) in "
                    f"{preview_report['preview']['group_count']:,} group(s)",
                    file=sys.stderr,
                )
            if not args.output:
                return 3 if engine.stats.errors else 0

        if args.join_on or config.join_key_columns:
            return run_join(input_files)

        if not args.quiet:
            print(f"CSV Power Tool - Processing {len(input_files)} file(s)...", file=sys.stderr)

        stats = engine.process(input_files, output_path)

        if not args.quiet:
            print("\nResults:", file=sys.stderr)
            print(f"  Files processed:    {stats.files_processed}", file=sys.stderr)
            print(f"  Files skipped:      {stats.files_skipped}", file=sys.stderr)
            print(f"  Total rows read:    {stats.total_rows_read:,}", file=sys.stderr)
            print(f"  Rows filtered:      {stats.rows_filtered:,}", file=sys.stderr)
            print(f"  Duplicates removed: {stats.duplicates_removed:,}", file=sys.stderr)
            print(f"  Final row count:    {stats.final_row_count:,}", file=sys.stderr)
            print(f"  Output: {output_path}", file=sys.stderr)
            if stats.schema_validation:
                print(
                    f"  Schema errors:      {stats.schema_validation.get('error_count', 0):,}",
                    file=sys.stderr,
                )

        if stats.files_processed == 0:
            return 1
        if stats.errors:
            return 3
        return 0

    def execute_once(input_files, runner=None):
        if not input_files:
            return run_once(input_files) if runner is None else runner(input_files)
        workflow = current_workflow(input_files)
        try:
            if args.save_workflow:
                write_workflow(args.save_workflow, workflow)
            if not args.quiet:
                print(f"  Workflow operations: {', '.join(operation_types(workflow))}", file=sys.stderr)
        except WorkflowError as exc:
            print(f"Error writing workflow: {exc}", file=sys.stderr)
            return 3

        exit_code = (runner or run_once)(input_files)
        if exit_code == 0 and args.workflow_history:
            try:
                record = append_history(args.workflow_history, workflow)
                if not args.quiet and record["changed_fields"]:
                    print(
                        f"  Workflow changed fields: {', '.join(record['changed_fields'])}",
                        file=sys.stderr,
                    )
            except WorkflowError as exc:
                print(f"Error writing workflow history: {exc}", file=sys.stderr)
                return 3
        return exit_code

    if args.three_way_base or args.three_way_ours or args.three_way_theirs:
        sys.exit(execute_once(
            [Path(path) for path in (args.three_way_base, args.three_way_ours, args.three_way_theirs) if path],
            lambda _input_files: run_three_way_merge(),
        ))

    def input_signature(input_files):
        signature = []
        for path in input_files:
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((str(path.resolve()), stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    if args.watch:
        last_signature = None
        last_exit = 0
        try:
            while True:
                watched_files = expand_inputs(args.inputs)
                signature = input_signature(watched_files)
                if signature != last_signature:
                    if watched_files:
                        last_exit = execute_once(watched_files)
                    elif not args.quiet:
                        print("CSV Power Tool - waiting for input files...", file=sys.stderr)
                    last_signature = signature
                time.sleep(max(args.watch_interval, 0.1))
        except KeyboardInterrupt:
            sys.exit(last_exit)

    input_files = expand_inputs(args.inputs or [])
    if args.dry_run:
        if not input_files:
            print("Error: No input files found for dry-run", file=sys.stderr)
            sys.exit(1)
        workflow = current_workflow(input_files)
        try:
            if args.save_workflow:
                write_workflow(args.save_workflow, workflow)
        except WorkflowError as exc:
            print(f"Error writing workflow: {exc}", file=sys.stderr)
            sys.exit(3)
        print(canonical_json(workflow), end="")
        sys.exit(0)
    sys.exit(execute_once(input_files))


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # If CLI arguments are present, run headless; otherwise launch GUI
    if len(sys.argv) > 1:
        from csv_power_tool.cli import main as cli_entry

        sys.exit(cli_entry())
    else:
        if GUI_IMPORT_ERROR is not None:
            print(
                "GUI mode requires customtkinter. Install dependencies with: "
                "python -m pip install -r requirements.txt",
                file=sys.stderr,
            )
            sys.exit(4)
        from csv_power_tool.gui import launch

        launch(CSVPowerToolApp)
