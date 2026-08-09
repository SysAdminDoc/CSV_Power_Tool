"""Schema-aware join and three-way merge planning primitives."""

from __future__ import annotations

import copy
from collections import Counter

from .quality import infer_value_type


JOIN_REPORT_FORMAT = "csv-power-tool-join-report"
MERGE_REPORT_FORMAT = "csv-power-tool-merge-report"
REPORT_VERSION = 1
KEY_NORMALIZATIONS = {"exact", "trim", "casefold", "trim-casefold"}
JOIN_TYPES = {"inner", "left", "right", "outer", "full", "anti", "semi"}
JOIN_CONFLICT_POLICIES = {"keep-both", "fail"}
MERGE_RESOLUTIONS = {"fail", "ours", "theirs", "base", "mark"}
MAX_DETAIL_RECORDS = 1_000


class JoinError(ValueError):
    """Actionable key validation, join, or merge error."""


def _raw(value) -> str:
    return "" if value is None else str(value)


def _normalize(value, normalization: str) -> str:
    if normalization not in KEY_NORMALIZATIONS:
        raise JoinError(
            f"Unsupported key normalization {normalization!r}; "
            f"choose one of {', '.join(sorted(KEY_NORMALIZATIONS))}"
        )
    text = _raw(value)
    if normalization in {"trim", "trim-casefold"}:
        text = text.strip()
    if normalization in {"casefold", "trim-casefold"}:
        text = text.casefold()
    return text


def _rows_equal(left: dict | None, right: dict | None) -> bool:
    if left is None or right is None:
        return left is right
    columns = set(left) | set(right)
    return all(_raw(left.get(column, "")) == _raw(right.get(column, "")) for column in columns)


def _key_diagnostics(
    rows: list[dict],
    key_columns: list[str],
    normalization: str,
    side: str,
    max_details: int,
) -> dict:
    if not key_columns:
        raise JoinError("At least one key column is required")
    if len(set(key_columns)) != len(key_columns):
        raise JoinError("Key columns must be unique")

    present_columns = {str(column) for row in rows for column in row}
    index = {}
    missing_examples = []
    type_counts = {column: Counter() for column in key_columns}
    normalization_examples = []
    normalization_coercion_count = 0
    missing_rows = 0

    for row_index, row in enumerate(rows, 1):
        key_values = []
        missing = False
        for column in key_columns:
            if column not in row or row[column] is None:
                missing = True
                raw_value = ""
            else:
                raw_value = _raw(row[column])
            normalized = _normalize(raw_value, normalization)
            if not normalized:
                missing = True
            else:
                type_counts[column][infer_value_type(raw_value)] += 1
            if raw_value != normalized and normalized:
                normalization_coercion_count += 1
                if len(normalization_examples) < max_details:
                    normalization_examples.append({
                        "side": side,
                        "row": row_index,
                        "column": column,
                        "raw": raw_value,
                        "normalized": normalized,
                    })
            key_values.append(normalized)

        if missing:
            missing_rows += 1
            if len(missing_examples) < max_details:
                missing_examples.append(row_index)
            continue
        index.setdefault(tuple(key_values), []).append(row_index - 1)

    duplicate_keys = []
    duplicate_row_count = 0
    for key, indexes in index.items():
        if len(indexes) < 2:
            continue
        duplicate_row_count += len(indexes)
        if len(duplicate_keys) < max_details:
            duplicate_keys.append({
                "key": list(key),
                "rows": [index + 1 for index in indexes[:max_details]],
                "row_count": len(indexes),
            })

    inferred_types = {}
    for column, counts in type_counts.items():
        inferred_types[column] = (
            max(counts, key=lambda name: (counts[name], name)) if counts else "empty"
        )

    report = {
        "side": side,
        "row_count": len(rows),
        "key_columns": list(key_columns),
        "key_columns_present": [column for column in key_columns if column in present_columns],
        "missing_key_columns": [column for column in key_columns if column not in present_columns],
        "rows_missing_key": missing_rows,
        "missing_key_examples": missing_examples,
        "key_type_counts": {
            column: dict(sorted(counts.items())) for column, counts in type_counts.items()
        },
        "inferred_key_types": inferred_types,
        "normalization_coercion_count": normalization_coercion_count,
        "normalization_coercion_examples": normalization_examples,
        "key_count": len(index),
        "duplicate_key_count": sum(1 for indexes in index.values() if len(indexes) > 1),
        "duplicate_row_count": duplicate_row_count,
        "duplicate_keys": duplicate_keys,
    }
    return {"report": report, "index": index}


def _type_mismatches(diagnostics: dict[str, dict]) -> list[dict]:
    columns = set()
    for item in diagnostics.values():
        columns.update(item["report"]["key_columns"])
    mismatches = []
    for column in sorted(columns):
        by_side = {}
        observed = set()
        for side, item in diagnostics.items():
            types = item["report"]["key_type_counts"].get(column, {})
            values = sorted(types)
            by_side[side] = values
            observed.update(values)
        if len(observed) > 1:
            mismatches.append({"column": column, "types_by_side": by_side})
    return mismatches


def _validation(diagnostics: dict[str, dict], *, reject_missing_rows: bool) -> tuple[bool, list[str], list[str]]:
    errors = []
    warnings = []
    for side, item in diagnostics.items():
        report = item["report"]
        for column in report["missing_key_columns"]:
            errors.append(f"{side} is missing key column {column!r}")
        if report["rows_missing_key"]:
            message = f"{side} has {report['rows_missing_key']:,} row(s) with missing/blank key values"
            (errors if reject_missing_rows else warnings).append(message)
        if report["duplicate_key_count"]:
            warnings.append(
                f"{side} has {report['duplicate_key_count']:,} duplicate key group(s)"
            )
    return not errors, errors, warnings


def _join_plan(
    left_rows: list[dict],
    right_rows: list[dict],
    key_columns: list[str],
    join_type: str,
    normalization: str,
    conflict_policy: str,
    max_details: int,
) -> dict:
    if join_type not in JOIN_TYPES:
        raise JoinError(f"Unsupported join type: {join_type}")
    if conflict_policy not in JOIN_CONFLICT_POLICIES:
        raise JoinError(f"Unsupported join conflict policy: {conflict_policy}")
    normalized_join_type = "outer" if join_type == "full" else join_type
    diagnostics = {
        "left": _key_diagnostics(left_rows, key_columns, normalization, "left", max_details),
        "right": _key_diagnostics(right_rows, key_columns, normalization, "right", max_details),
    }
    left_index = diagnostics["left"]["index"]
    right_index = diagnostics["right"]["index"]
    matched_keys = [key for key in left_index if key in right_index]
    matched_left_rows = sum(len(left_index[key]) for key in matched_keys)
    matched_right_rows = sum(len(right_index[key]) for key in matched_keys)
    left_duplicates = any(len(left_index[key]) > 1 for key in matched_keys)
    right_duplicates = any(len(right_index[key]) > 1 for key in matched_keys)
    if left_duplicates and right_duplicates:
        cardinality = "many-to-many"
    elif left_duplicates or right_duplicates:
        cardinality = "one-to-many"
    else:
        cardinality = "one-to-one"

    left_columns = list(dict.fromkeys(column for row in left_rows for column in row))
    right_columns = list(dict.fromkeys(column for row in right_rows for column in row))
    conflicts = []
    conflict_count = 0
    comparable_columns = [
        column for column in right_columns
        if column in left_columns and column not in key_columns
    ]
    for key in matched_keys:
        for left_index_value in left_index[key]:
            for right_index_value in right_index[key]:
                left = left_rows[left_index_value]
                right = right_rows[right_index_value]
                for column in comparable_columns:
                    left_value = _raw(left.get(column, ""))
                    right_value = _raw(right.get(column, ""))
                    if left_value == right_value:
                        continue
                    conflict_count += 1
                    if len(conflicts) < max_details:
                        conflicts.append({
                            "key": list(key),
                            "left_row": left_index_value + 1,
                            "right_row": right_index_value + 1,
                            "column": column,
                            "left_value": left_value,
                            "right_value": right_value,
                            "resolution": "keep-both",
                        })

    type_mismatches = _type_mismatches(diagnostics)
    valid, errors, warnings = _validation(diagnostics, reject_missing_rows=False)
    report = {
        "format": JOIN_REPORT_FORMAT,
        "version": REPORT_VERSION,
        "operation": "join",
        "join_type": normalized_join_type,
        "key_columns": list(key_columns),
        "key_normalization": normalization,
        "conflict_policy": conflict_policy,
        "validation": {
            "valid": valid,
            "errors": errors,
            "warnings": warnings,
        },
        "sources": {
            "left": diagnostics["left"]["report"],
            "right": diagnostics["right"]["report"],
        },
        "cardinality": cardinality,
        "matched_key_count": len(matched_keys),
        "matched_left_rows": matched_left_rows,
        "matched_right_rows": matched_right_rows,
        "unmatched_left_rows": len(left_rows) - matched_left_rows,
        "unmatched_right_rows": len(right_rows) - matched_right_rows,
        "type_mismatches": type_mismatches,
        "coercions": {
            "normalization_count": (
                diagnostics["left"]["report"]["normalization_coercion_count"]
                + diagnostics["right"]["report"]["normalization_coercion_count"]
            ),
            "type_mismatch_count": len(type_mismatches),
        },
        "conflict_count": conflict_count,
        "conflicts": conflicts,
        "conflicts_truncated": conflict_count > len(conflicts),
        "deterministic_order": "left input order, then right unmatched input order",
    }
    return {
        "report": report,
        "left_index": left_index,
        "right_index": right_index,
        "left_columns": left_columns,
        "right_columns": right_columns,
    }


def analyze_join(
    left_rows: list[dict],
    right_rows: list[dict],
    key_columns: list[str],
    join_type: str = "inner",
    key_normalization: str = "trim-casefold",
    conflict_policy: str = "keep-both",
    max_details: int = MAX_DETAIL_RECORDS,
) -> dict:
    """Return a JSON-safe join validation and conflict report."""

    return _join_plan(
        left_rows,
        right_rows,
        key_columns,
        join_type,
        key_normalization,
        conflict_policy,
        max(1, int(max_details)),
    )["report"]


def execute_join(
    left_rows: list[dict],
    right_rows: list[dict],
    key_columns: list[str],
    join_type: str = "inner",
    right_suffix: str = "_right",
    key_normalization: str = "trim-casefold",
    conflict_policy: str = "keep-both",
    return_diagnostics: bool = False,
    max_details: int = MAX_DETAIL_RECORDS,
):
    """Execute a deterministic join, optionally returning its report."""

    plan = _join_plan(
        left_rows,
        right_rows,
        key_columns,
        join_type,
        key_normalization,
        conflict_policy,
        max(1, int(max_details)),
    )
    report = plan["report"]
    if not report["validation"]["valid"]:
        raise JoinError("Join key validation failed: " + "; ".join(report["validation"]["errors"]))
    if conflict_policy == "fail" and report["conflict_count"]:
        raise JoinError(
            f"Join found {report['conflict_count']:,} conflicting shared cell(s); "
            "use --join-conflict-policy keep-both or resolve the source data"
        )

    normalized_type = report["join_type"]
    left_index = plan["left_index"]
    right_index = plan["right_index"]
    left_columns = plan["left_columns"]
    right_columns = plan["right_columns"]
    right_output_names = {}
    output_columns = list(left_columns)
    for column in right_columns:
        if column in key_columns:
            continue
        candidate = column
        if candidate in output_columns:
            candidate = f"{column}{right_suffix}"
            counter = 2
            while candidate in output_columns:
                candidate = f"{column}{right_suffix}{counter}"
                counter += 1
        right_output_names[column] = candidate
        output_columns.append(candidate)

    def merge(left: dict, right: dict | None) -> dict:
        merged = {column: left.get(column, "") for column in left_columns}
        if right is None:
            return merged
        for column in right_columns:
            target = column if column in key_columns else right_output_names[column]
            if column in key_columns and not merged.get(column):
                merged[target] = right.get(column, "")
            elif column not in key_columns:
                merged[target] = right.get(column, "")
        return merged

    result = []
    matched_right = set()
    left_row_keys = {}
    for key, indexes in left_index.items():
        for index in indexes:
            left_row_keys[index] = key
    if normalized_type in {"anti", "semi"}:
        for left_index_value, left in enumerate(left_rows):
            key = left_row_keys.get(left_index_value)
            has_match = key is not None and key in right_index
            if (normalized_type == "semi" and has_match) or (normalized_type == "anti" and not has_match):
                result.append({column: left.get(column, "") for column in left_columns})
        output_columns = list(left_columns)
    else:
        for left_index_value, left in enumerate(left_rows):
            key = left_row_keys.get(left_index_value)
            matches = right_index.get(key, []) if key is not None else []
            if matches:
                for right_index_value in matches:
                    matched_right.add(right_index_value)
                    result.append(merge(left, right_rows[right_index_value]))
            elif normalized_type in {"left", "outer"}:
                result.append(merge(left, None))

        if normalized_type in {"right", "outer"}:
            for right_index_value, right in enumerate(right_rows):
                if right_index_value in matched_right:
                    continue
                result.append(merge({}, right))

    report = copy.deepcopy(report)
    report["output_columns"] = list(output_columns)
    report["output_row_count"] = len(result)
    if return_diagnostics:
        return result, output_columns, report
    return result, output_columns


def _three_way_plan(
    base_rows: list[dict],
    ours_rows: list[dict],
    theirs_rows: list[dict],
    key_columns: list[str],
    normalization: str,
    resolution: str,
    max_details: int,
) -> dict:
    if not key_columns:
        sample = next(iter(ours_rows or theirs_rows or base_rows), None)
        if not sample:
            return {
                "report": {
                    "format": MERGE_REPORT_FORMAT,
                    "version": REPORT_VERSION,
                    "operation": "three-way-merge",
                    "key_columns": [],
                    "validation": {"valid": True, "errors": [], "warnings": []},
                    "conflict_count": 0,
                    "conflicts": [],
                    "sources": {
                        "base": {"side": "base", "row_count": 0},
                        "ours": {"side": "ours", "row_count": 0},
                        "theirs": {"side": "theirs", "row_count": 0},
                    },
                },
                "indexes": {"base": {}, "ours": {}, "theirs": {}},
                "columns": [],
                "order": [],
                "legacy_conflicts": [],
            }
        key_columns = [next(iter(sample))]
    if resolution not in MERGE_RESOLUTIONS:
        raise JoinError(f"Unsupported merge conflict resolution: {resolution}")
    diagnostics = {
        "base": _key_diagnostics(base_rows, key_columns, normalization, "base", max_details),
        "ours": _key_diagnostics(ours_rows, key_columns, normalization, "ours", max_details),
        "theirs": _key_diagnostics(theirs_rows, key_columns, normalization, "theirs", max_details),
    }
    indexes = {side: item["index"] for side, item in diagnostics.items()}
    valid, errors, warnings = _validation(diagnostics, reject_missing_rows=True)
    for side, item in diagnostics.items():
        if item["report"]["duplicate_key_count"]:
            valid = False
            errors.append(
                f"{side} has duplicate merge keys; three-way merge requires unique keys"
            )
    order = []
    for side in ("base", "ours", "theirs"):
        for key in indexes[side]:
            if key not in order:
                order.append(key)
    columns = list(dict.fromkeys(
        column
        for row in [*base_rows, *ours_rows, *theirs_rows]
        for column in row
    ))
    conflicts = []
    conflict_count = 0
    legacy_conflicts = []
    for key in order:
        base_row = base_rows[indexes["base"][key][0]] if key in indexes["base"] else None
        ours_row = ours_rows[indexes["ours"][key][0]] if key in indexes["ours"] else None
        theirs_row = theirs_rows[indexes["theirs"][key][0]] if key in indexes["theirs"] else None
        if _rows_equal(ours_row, theirs_row) or _rows_equal(ours_row, base_row) or _rows_equal(theirs_row, base_row):
            continue
        changed_columns = []
        for column in columns:
            base_value = _raw((base_row or {}).get(column, ""))
            ours_value = _raw((ours_row or {}).get(column, ""))
            theirs_value = _raw((theirs_row or {}).get(column, ""))
            if ours_value != theirs_value and ours_value != base_value and theirs_value != base_value:
                changed_columns.append(column)
                conflict_count += 1
                if len(conflicts) < max_details:
                    conflicts.append({
                        "key": list(key),
                        "column": column,
                        "base": base_value,
                        "ours": ours_value,
                        "theirs": theirs_value,
                        "resolution": resolution,
                    })
        if changed_columns:
            legacy_conflicts.append({"key": list(key), "columns": changed_columns})

    type_mismatches = _type_mismatches(diagnostics)
    report = {
        "format": MERGE_REPORT_FORMAT,
        "version": REPORT_VERSION,
        "operation": "three-way-merge",
        "key_columns": list(key_columns),
        "key_normalization": normalization,
        "resolution_policy": resolution,
        "requires_explicit_resolution": conflict_count > 0 and resolution == "fail",
        "validation": {"valid": valid, "errors": errors, "warnings": warnings},
        "sources": {side: item["report"] for side, item in diagnostics.items()},
        "type_mismatches": type_mismatches,
        "coercions": {
            "normalization_count": sum(
                item["report"]["normalization_coercion_count"] for item in diagnostics.values()
            ),
            "type_mismatch_count": len(type_mismatches),
        },
        "conflict_count": conflict_count,
        "conflicts": conflicts,
        "conflicts_truncated": conflict_count > len(conflicts),
        "deterministic_order": "base, then ours additions, then theirs additions",
    }
    return {
        "report": report,
        "indexes": indexes,
        "columns": columns,
        "order": order,
        "legacy_conflicts": legacy_conflicts,
    }


def analyze_three_way(
    base_rows: list[dict],
    ours_rows: list[dict],
    theirs_rows: list[dict],
    key_columns: list[str],
    key_normalization: str = "trim-casefold",
    resolution: str = "fail",
    max_details: int = MAX_DETAIL_RECORDS,
) -> dict:
    """Return a JSON-safe three-way validation and conflict report."""

    return _three_way_plan(
        base_rows,
        ours_rows,
        theirs_rows,
        key_columns,
        key_normalization,
        resolution,
        max(1, int(max_details)),
    )["report"]


def execute_three_way(
    base_rows: list[dict],
    ours_rows: list[dict],
    theirs_rows: list[dict],
    key_columns: list[str],
    resolution: str = "ours",
    key_normalization: str = "trim-casefold",
    return_diagnostics: bool = False,
    max_details: int = MAX_DETAIL_RECORDS,
):
    """Execute a keyed three-way merge with deterministic conflict handling."""

    plan = _three_way_plan(
        base_rows,
        ours_rows,
        theirs_rows,
        key_columns,
        key_normalization,
        resolution,
        max(1, int(max_details)),
    )
    report = plan["report"]
    if not report["validation"]["valid"]:
        raise JoinError("Merge key validation failed: " + "; ".join(report["validation"]["errors"]))
    if resolution == "fail" and report["conflict_count"]:
        raise JoinError(
            f"Three-way merge found {report['conflict_count']:,} conflict(s) that require explicit resolution; "
            "choose --conflict-resolution ours|theirs|base|mark after review"
        )

    indexes = plan["indexes"]
    columns = plan["columns"]
    merged_rows = []
    for key in plan["order"]:
        base_row = base_rows[indexes["base"][key][0]] if key in indexes["base"] else None
        ours_row = ours_rows[indexes["ours"][key][0]] if key in indexes["ours"] else None
        theirs_row = theirs_rows[indexes["theirs"][key][0]] if key in indexes["theirs"] else None
        if _rows_equal(ours_row, theirs_row):
            selected = ours_row or theirs_row
        elif _rows_equal(ours_row, base_row):
            selected = theirs_row
        elif _rows_equal(theirs_row, base_row):
            selected = ours_row
        else:
            selected = copy.deepcopy(ours_row or theirs_row or base_row or {})
            for column in columns:
                base_value = _raw((base_row or {}).get(column, ""))
                ours_value = _raw((ours_row or {}).get(column, ""))
                theirs_value = _raw((theirs_row or {}).get(column, ""))
                if ours_value == theirs_value or ours_value == base_value or theirs_value == base_value:
                    continue
                if resolution == "theirs":
                    selected[column] = theirs_value
                elif resolution == "base":
                    selected[column] = base_value
                elif resolution == "mark":
                    selected[column] = (
                        f"<<<<<<< ours\n{ours_value}\n=======\n"
                        f"{theirs_value}\n>>>>>>> theirs"
                    )
                else:
                    selected[column] = ours_value
        if selected is not None:
            merged_rows.append({column: selected.get(column, "") for column in columns})

    report = copy.deepcopy(report)
    report["output_columns"] = list(columns)
    report["output_row_count"] = len(merged_rows)
    if return_diagnostics:
        return merged_rows, plan["legacy_conflicts"], columns, report
    return merged_rows, plan["legacy_conflicts"], columns
