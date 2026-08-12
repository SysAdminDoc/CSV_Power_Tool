"""Frictionless-compatible first-version table schema contracts."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import date, datetime
from pathlib import Path


TABLE_SCHEMA_URL = "https://specs.frictionlessdata.io/table-schema/"
SCHEMA_VERSION = 1
SUPPORTED_TYPES = {"string", "integer", "number", "boolean", "date", "datetime"}
SUPPORTED_FIELD_CONSTRAINTS = {"required", "unique", "nullable"}
SUPPORTED_TOP_LEVEL = {
    "$schema", "name", "title", "description", "fields", "primaryKey", "missingValues"
}


class SchemaError(ValueError):
    """Actionable schema contract error."""


def normalize_column_mapping(mapping: dict | None) -> dict[str, str]:
    """Normalize and validate source-to-output column rename declarations."""

    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise SchemaError("Column mapping must be an object of source-to-output names")

    normalized: dict[str, str] = {}
    targets: dict[str, str] = {}
    for raw_source, raw_target in mapping.items():
        if not isinstance(raw_source, str) or not isinstance(raw_target, str):
            raise SchemaError("Column mapping names must be strings")
        source = raw_source.strip()
        target = raw_target.strip()
        if not source or not target:
            raise SchemaError("Column mapping source and target names cannot be blank")
        if source in normalized:
            raise SchemaError(f"Duplicate column mapping source: {source}")
        previous_source = targets.get(target)
        if previous_source is not None and previous_source != source:
            raise SchemaError(
                f"Column mapping target {target!r} is used by both {previous_source!r} and {source!r}"
            )
        normalized[source] = target
        targets[target] = source
    return normalized


def validate_column_mapping(mapping: dict | None, columns: list[str]) -> dict[str, str]:
    """Validate a mapping against discovered columns and effective output names."""

    normalized = normalize_column_mapping(mapping)
    known_columns = list(columns)
    unknown = sorted(set(normalized) - set(known_columns))
    if unknown:
        raise SchemaError(
            "Column mapping references unknown source column(s): " + ", ".join(unknown)
        )

    output_names: dict[str, str] = {}
    for column in known_columns:
        target = normalized.get(column, column)
        previous = output_names.get(target)
        if previous is not None and previous != column:
            raise SchemaError(
                f"Column mapping would collide: {previous!r} and {column!r} both output as {target!r}"
            )
        output_names[target] = column
    return normalized


def parse_column_mapping_assignments(assignments: list[str] | None) -> dict[str, str]:
    """Parse repeated ``SOURCE=TARGET`` CLI declarations."""

    mapping: dict[str, str] = {}
    for assignment in assignments or []:
        if not isinstance(assignment, str) or "=" not in assignment:
            raise SchemaError(
                f"Invalid column mapping {assignment!r}; expected SOURCE=TARGET"
            )
        source, target = assignment.split("=", 1)
        source = source.strip()
        target = target.strip()
        if source in mapping:
            raise SchemaError(f"Duplicate column mapping source: {source}")
        mapping[source] = target
    return normalize_column_mapping(mapping)


def normalize_schema(data: dict) -> dict:
    if not isinstance(data, dict):
        raise SchemaError("Table Schema must be a JSON object")
    unsupported = sorted(set(data) - SUPPORTED_TOP_LEVEL)
    if unsupported:
        raise SchemaError(f"Unsupported Table Schema feature(s): {', '.join(unsupported)}")
    fields = data.get("fields")
    if not isinstance(fields, list) or not fields:
        raise SchemaError("Table Schema fields must be a non-empty list")
    if "$schema" in data and not isinstance(data["$schema"], str):
        raise SchemaError("Table Schema $schema must be a string")

    normalized_fields = []
    names = set()
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("name"), str) or not field["name"].strip():
            raise SchemaError("Each Table Schema field requires a non-empty name")
        name = field["name"].strip()
        if name in names:
            raise SchemaError(f"Duplicate Table Schema field: {name}")
        names.add(name)
        field_type = field.get("type", "string")
        if field_type not in SUPPORTED_TYPES:
            raise SchemaError(f"Unsupported Table Schema field type for {name}: {field_type!r}")
        constraints = field.get("constraints", {})
        if not isinstance(constraints, dict):
            raise SchemaError(f"Constraints for {name} must be an object")
        unsupported_constraints = sorted(set(constraints) - SUPPORTED_FIELD_CONSTRAINTS)
        if unsupported_constraints:
            raise SchemaError(
                f"Unsupported constraints for {name}: {', '.join(unsupported_constraints)}"
            )
        if any(not isinstance(value, bool) for value in constraints.values()):
            raise SchemaError(f"Constraints for {name} must use boolean values")
        normalized = {"name": name, "type": field_type}
        for key in ("title", "description"):
            if key in field:
                normalized[key] = str(field[key])
        if constraints:
            normalized["constraints"] = {
                key: bool(value) for key, value in constraints.items()
            }
        normalized_fields.append(normalized)

    primary_key = data.get("primaryKey", [])
    if isinstance(primary_key, str):
        primary_key = [primary_key]
    if not isinstance(primary_key, list) or any(not isinstance(key, str) for key in primary_key):
        raise SchemaError("Table Schema primaryKey must be a field name or list of names")
    missing = [key for key in primary_key if key not in names]
    if missing:
        raise SchemaError(f"Table Schema primaryKey references unknown field(s): {', '.join(missing)}")

    missing_values = data.get("missingValues", [""])
    if not isinstance(missing_values, list) or any(not isinstance(value, str) for value in missing_values):
        raise SchemaError("Table Schema missingValues must be a list of strings")

    normalized_schema = {
        "$schema": data.get("$schema", TABLE_SCHEMA_URL),
        "fields": normalized_fields,
        "missingValues": list(missing_values),
    }
    for key in ("name", "title", "description"):
        if key in data:
            normalized_schema[key] = str(data[key])
    if primary_key:
        normalized_schema["primaryKey"] = list(primary_key)
    return normalized_schema


def load_schema(path: str | Path) -> dict:
    schema_path = Path(path)
    try:
        data = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaError(f"Unable to read Table Schema {schema_path}: {exc}") from exc
    return normalize_schema(data)


def write_schema(path: str | Path, schema: dict) -> Path:
    schema_path = Path(path)
    _atomic_json_write(schema_path, normalize_schema(schema))
    return schema_path


def infer_schema(rows: list[dict], columns: list[str]) -> dict:
    fields = []
    for column in columns:
        values = [str(row.get(column, "")) for row in rows]
        non_empty = [value for value in values if value not in {"", "null", "NULL"}]
        field_type = "string"
        if non_empty and all(re.fullmatch(r"[-+]?\d+", value) for value in non_empty):
            field_type = "integer"
        elif non_empty and all(_is_number(value) for value in non_empty):
            field_type = "number"
        elif non_empty and all(value.lower() in {"true", "false"} for value in non_empty):
            field_type = "boolean"
        constraints = {}
        if values and all(value not in {"", "null", "NULL"} for value in values):
            constraints["required"] = True
        if non_empty and len(non_empty) == len(set(non_empty)):
            constraints["unique"] = True
        field = {"name": column, "type": field_type}
        if constraints:
            field["constraints"] = constraints
        fields.append(field)
    return normalize_schema({"$schema": TABLE_SCHEMA_URL, "fields": fields})


def validate_rows(rows: list[dict], schema: dict, file_path: str | Path) -> tuple[list[dict], dict]:
    normalized = normalize_schema(schema)
    fields = normalized["fields"]
    missing_values = set(normalized.get("missingValues", [""]))
    errors = []
    invalid_indexes = set()
    unique_seen = {field["name"]: {} for field in fields if field.get("constraints", {}).get("unique")}
    primary_seen = {}
    primary_key = normalized.get("primaryKey", [])

    for index, row in enumerate(rows):
        row_number = index + 2
        for field in fields:
            name = field["name"]
            value = row.get(name, "")
            blank = value is None or str(value) in missing_values
            constraints = field.get("constraints", {})
            if blank:
                nullable = constraints.get("nullable", not constraints.get("required", False))
                if not nullable:
                    errors.append(_error(file_path, row_number, name, "required", value))
                    invalid_indexes.add(index)
                continue
            if not _matches_type(str(value), field["type"]):
                errors.append(_error(file_path, row_number, name, f"type:{field['type']}", value))
                invalid_indexes.add(index)
            if constraints.get("unique"):
                if str(value) in unique_seen[name]:
                    errors.append(_error(file_path, row_number, name, "unique", value))
                    invalid_indexes.add(index)
                unique_seen[name][str(value)] = row_number

        if primary_key:
            key = tuple(str(row.get(name, "")) for name in primary_key)
            if any(value in missing_values for value in key):
                errors.append(_error(file_path, row_number, ",".join(primary_key), "primaryKey", key))
                invalid_indexes.add(index)
            elif key in primary_seen:
                errors.append(_error(file_path, row_number, ",".join(primary_key), "primaryKey.unique", key))
                invalid_indexes.add(index)
            primary_seen[key] = row_number

    report = {
        "file": str(file_path),
        "row_count": len(rows),
        "valid_row_count": len(rows) - len(invalid_indexes),
        "invalid_row_count": len(invalid_indexes),
        "error_count": len(errors),
        "errors": errors,
        "_invalid_indexes": sorted(invalid_indexes),
    }
    return [row for index, row in enumerate(rows) if index not in invalid_indexes], report


def validation_report(reports: list[dict], mode: str, schema: dict) -> dict:
    public_reports = [
        {key: value for key, value in report.items() if key != "_invalid_indexes"}
        for report in reports
    ]
    return {
        "format": "csv-power-tool-validation-report",
        "version": SCHEMA_VERSION,
        "mode": mode,
        "schema": normalize_schema(schema),
        "files": public_reports,
        "row_count": sum(report["row_count"] for report in public_reports),
        "valid_row_count": sum(report["valid_row_count"] for report in public_reports),
        "invalid_row_count": sum(report["invalid_row_count"] for report in public_reports),
        "error_count": sum(report["error_count"] for report in public_reports),
    }


def write_validation_report(path: str | Path, report: dict) -> Path:
    report_path = Path(path)
    _atomic_json_write(report_path, report)
    return report_path


def _error(file_path, row, column, rule, observed) -> dict:
    return {
        "file": str(file_path),
        "row": row,
        "column": column,
        "cell": {"row": row, "column": column},
        "rule": rule,
        "observed_value": observed,
    }


def _matches_type(value: str, field_type: str) -> bool:
    if field_type == "string":
        return True
    if field_type == "integer":
        return bool(re.fullmatch(r"[-+]?\d+", value))
    if field_type == "number":
        return _is_number(value)
    if field_type == "boolean":
        return value.lower() in {"true", "false", "1", "0"}
    if field_type == "date":
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False
    if field_type == "datetime":
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
    return False


def _is_number(value: str) -> bool:
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
