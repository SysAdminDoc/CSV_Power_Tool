"""Bounded data-quality profiling and reviewed text repairs."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path


QUALITY_FORMAT = "csv-power-tool-quality-profile"
QUALITY_VERSION = 1
REPAIR_FORMAT = "csv-power-tool-repair-report"
REPAIR_VERSION = 1
DEFAULT_MISSING_VALUES = {"null", "NULL"}
SUPPORTED_REPAIR_VALUE_TYPES = (str, int, float, bool)


class QualityError(ValueError):
    """Actionable profile or reviewed-repair error."""


def _text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _type_name(value: str) -> str:
    text = value.strip()
    if not text:
        return "empty"
    if text.lower() in {"true", "false", "yes", "no"}:
        return "boolean"
    if re.fullmatch(r"[-+]?\d+", text):
        return "integer"
    try:
        if math.isfinite(float(text.replace(",", ""))):
            return "number"
    except ValueError:
        pass
    try:
        date.fromisoformat(text)
        return "date"
    except ValueError:
        pass
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return "datetime"
    except ValueError:
        return "string"


def infer_value_type(value) -> str:
    """Classify a raw cell without converting or rewriting its text."""

    return _type_name(_text(value))


class QualityProfiler:
    """Incremental profiler with bounded facet, sample, and distinct storage."""

    def __init__(
        self,
        facet_limit: int = 20,
        max_distinct_values: int = 100_000,
        sample_limit: int = 5,
        missing_values=None,
    ):
        self.facet_limit = max(1, int(facet_limit))
        self.max_distinct_values = max(1, int(max_distinct_values))
        self.sample_limit = max(1, int(sample_limit))
        self.missing_values = {str(value) for value in (missing_values or DEFAULT_MISSING_VALUES)}
        self.rows_seen = 0
        self._columns = {}

    def _state(self, column: str) -> dict:
        return self._columns.setdefault(
            column,
            {
                "observed": 0,
                "non_empty": 0,
                "blank": 0,
                "null": 0,
                "distinct": set(),
                "distinct_overflow": False,
                "facets": Counter(),
                "samples": [],
                "types": Counter(),
                "numeric_count": 0,
                "numeric_min": None,
                "numeric_max": None,
                "numeric_sum": 0.0,
            },
        )

    def add_row(self, row: dict) -> None:
        self.rows_seen += 1
        for column, raw_value in row.items():
            state = self._state(str(column))
            state["observed"] += 1
            if raw_value is None:
                state["null"] += 1
                continue
            value = _text(raw_value)
            if not value.strip():
                state["blank"] += 1
                continue
            if value in self.missing_values:
                state["null"] += 1
                continue

            state["non_empty"] += 1
            if value not in state["distinct"]:
                if len(state["distinct"]) < self.max_distinct_values:
                    state["distinct"].add(value)
                else:
                    state["distinct_overflow"] = True
            if value in state["facets"]:
                state["facets"][value] += 1
            elif len(state["facets"]) < self.max_distinct_values:
                state["facets"][value] = 1
            else:
                state["distinct_overflow"] = True
            if len(state["samples"]) < self.sample_limit and value not in state["samples"]:
                state["samples"].append(value)

            kind = infer_value_type(value)
            state["types"][kind] += 1
            try:
                number = float(value.replace(",", ""))
            except ValueError:
                number = None
            if number is not None and math.isfinite(number):
                state["numeric_count"] += 1
                state["numeric_sum"] += number
                state["numeric_min"] = number if state["numeric_min"] is None else min(state["numeric_min"], number)
                state["numeric_max"] = number if state["numeric_max"] is None else max(state["numeric_max"], number)

    def report(self, *, bounded: bool = False, scan_limit: int | None = None, scan_truncated: bool = False) -> dict:
        columns = []
        for name, state in self._columns.items():
            non_empty = state["non_empty"]
            type_counts = dict(sorted(state["types"].items()))
            inferred_type = max(type_counts, key=type_counts.get) if type_counts else "empty"
            numeric = None
            if state["numeric_count"]:
                numeric = {
                    "count": state["numeric_count"],
                    "min": state["numeric_min"],
                    "max": state["numeric_max"],
                    "mean": state["numeric_sum"] / state["numeric_count"],
                }
            facets = [
                {"value": value, "count": count}
                for value, count in sorted(
                    state["facets"].items(),
                    key=lambda item: (-item[1], item[0]),
                )[: self.facet_limit]
            ]
            columns.append({
                "name": name,
                "row_count": self.rows_seen,
                "non_empty_count": non_empty,
                "blank_count": state["blank"] + self.rows_seen - state["observed"],
                "null_count": state["null"],
                "unique_count": len(state["distinct"]),
                "unique_count_exact": not state["distinct_overflow"],
                "duplicate_count": max(0, non_empty - len(state["distinct"])),
                "inferred_type": inferred_type,
                "type_confidence": round(
                    max(type_counts.values(), default=0) / max(1, non_empty),
                    4,
                ),
                "type_counts": type_counts,
                "facets": facets,
                "facets_truncated": state["distinct_overflow"],
                "samples": list(state["samples"]),
                "raw_samples": list(state["samples"]),
                "numeric": numeric,
            })
        return {
            "format": QUALITY_FORMAT,
            "version": QUALITY_VERSION,
            "bounded": bool(bounded),
            "scan_limit": scan_limit,
            "scan_truncated": bool(scan_truncated),
            "rows_scanned": self.rows_seen,
            "columns": columns,
        }


def profile_rows(
    rows: list[dict],
    *,
    facet_limit: int = 20,
    max_distinct_values: int = 100_000,
    sample_limit: int = 5,
    missing_values=None,
) -> dict:
    profiler = QualityProfiler(
        facet_limit=facet_limit,
        max_distinct_values=max_distinct_values,
        sample_limit=sample_limit,
        missing_values=missing_values,
    )
    for row in rows:
        profiler.add_row(row)
    return profiler.report()


def normalize_repairs(edits, max_edits: int = 10_000) -> list[dict]:
    if isinstance(edits, dict):
        edits = edits.get("edits")
    if not isinstance(edits, list):
        raise QualityError("Repair edits must be a JSON list or an object with an edits list")
    if len(edits) > max_edits:
        raise QualityError(f"Repair edit count exceeds the limit of {max_edits:,}")
    normalized = []
    for index, edit in enumerate(edits, 1):
        if not isinstance(edit, dict):
            raise QualityError(f"Repair edit {index} must be an object")
        row = edit.get("row")
        column = edit.get("column")
        if isinstance(row, bool) or not isinstance(row, int) or row < 1:
            raise QualityError(f"Repair edit {index} row must be a 1-based positive integer")
        if not isinstance(column, str) or not column.strip():
            raise QualityError(f"Repair edit {index} column must be a non-empty string")
        if "value" in edit:
            value = edit["value"]
        elif "replacement" in edit:
            value = edit["replacement"]
        else:
            raise QualityError(f"Repair edit {index} requires value or replacement")
        if value is not None and not isinstance(value, SUPPORTED_REPAIR_VALUE_TYPES):
            raise QualityError(f"Repair edit {index} value must be a scalar")
        expected = edit.get("expected_old")
        if expected is not None and not isinstance(expected, str):
            raise QualityError(f"Repair edit {index} expected_old must be a string")
        reason = str(edit.get("reason", "reviewed correction"))[:1_024]
        normalized.append({
            "row": row,
            "column": column.strip(),
            "value": "" if value is None else _text(value),
            "expected_old": expected,
            "reason": reason,
        })
    return normalized


def apply_repairs(rows: list[dict], edits) -> tuple[list[dict], dict]:
    normalized = normalize_repairs(edits)
    repaired = copy.deepcopy(rows)
    applied = []
    for edit in normalized:
        index = edit["row"] - 1
        if index >= len(repaired):
            raise QualityError(
                f"Repair row {edit['row']} is outside the {len(repaired):,}-row input"
            )
        row = repaired[index]
        before = _text(row.get(edit["column"], ""))
        if edit["expected_old"] is not None and before != edit["expected_old"]:
            raise QualityError(
                f"Repair row {edit['row']} column {edit['column']!r} expected "
                f"{edit['expected_old']!r}, observed {before!r}"
            )
        after = edit["value"]
        row[edit["column"]] = after
        applied.append({
            "row": edit["row"],
            "column": edit["column"],
            "before": before,
            "after": after,
            "reason": edit["reason"],
            "changed": before != after,
            "value_type": "text",
        })
    return repaired, {
        "format": REPAIR_FORMAT,
        "version": REPAIR_VERSION,
        "edit_count": len(applied),
        "edits": applied,
    }


def load_repairs(path: str | Path) -> list[dict]:
    repair_path = Path(path)
    try:
        payload = json.loads(repair_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityError(f"Unable to read repair edits {repair_path}: {exc}") from exc
    return normalize_repairs(payload)


def write_quality_report(path: str | Path, report: dict) -> Path:
    return _atomic_json_write(Path(path), report)


def _atomic_json_write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path
