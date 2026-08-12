"""Versioned, deterministic workflow documents and bounded history."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


WORKFLOW_FORMAT = "csv-power-tool-workflow"
WORKFLOW_VERSION = 2
HISTORY_FORMAT = "csv-power-tool-workflow-history"
HISTORY_VERSION = 2
LEGACY_WORKFLOW_VERSION = 1
LEGACY_HISTORY_VERSION = 1
DEFAULT_HISTORY_LIMIT = 50


class WorkflowError(ValueError):
    """Actionable workflow schema or persistence error."""


def canonical_json(value) -> str:
    """Serialize JSON data deterministically for files and identity hashes."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, separators=(",", ": ")) + "\n"


def _workflow_hash(document: dict) -> str:
    payload = copy.deepcopy(document)
    payload.pop("workflow_sha256", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture_file_metadata(paths: Iterable[str | Path]) -> list[dict]:
    """Capture replay diagnostics without failing a workflow save for missing inputs."""

    metadata = []
    for raw_path in paths:
        path = Path(raw_path)
        item = {"path": str(path)}
        try:
            stat = path.stat()
            item.update({
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": _file_sha256(path),
            })
        except OSError as exc:
            item["error"] = str(exc)
        metadata.append(item)
    return metadata


def operations_from_config(config: dict, output_path: str | None, input_patterns: list[str]) -> list[dict]:
    """Build a stable ordered operation list from the engine configuration."""

    return [
        {"type": "input-selection", "patterns": list(input_patterns)},
        {
            "type": "schema",
            "mode": config.get("schema_mode", "union"),
            "header_normalize": config.get("header_normalize", "none"),
            "column_template": config.get("column_template", ""),
        },
        {
            "type": "quality-repair",
            "edit_count": len(config.get("repair_edits", [])),
            "report_path": config.get("repair_report_path", ""),
        },
        {
            "type": "join-merge-audit",
            "key_normalization": config.get("key_normalization", "trim-casefold"),
            "join_type": config.get("join_type", "inner"),
            "join_keys": config.get("join_key_columns", []),
            "join_conflict_policy": config.get("join_conflict_policy", "keep-both"),
            "join_report_path": config.get("join_report_path", ""),
            "merge_keys": config.get("merge_key_columns", []),
            "merge_conflict_resolution": config.get("merge_conflict_resolution", "fail"),
            "merge_report_path": config.get("merge_report_path", ""),
        },
        {"type": "filter", "logic": config.get("filter_logic", "and"), "rules": config.get("filters", [])},
        {"type": "transform", "rules": config.get("column_transforms", [])},
        {
            "type": "dedupe",
            "enabled": config.get("dedupe_enabled", True),
            "columns": config.get("dedupe_columns", []),
            "keep": config.get("dedupe_keep", "first"),
            "fuzzy": config.get("dedupe_fuzzy_enabled", False),
            "threshold": config.get("dedupe_fuzzy_threshold", 90),
            "aggregate": config.get("dedupe_aggregate_mode", "none"),
        },
        {
            "type": "sort",
            "enabled": config.get("sort_enabled", False),
            "columns": config.get("sort_columns", []),
        },
        {
            "type": "reshape",
            "unpivot": config.get("unpivot_columns", []),
            "pivot": config.get("pivot_column", ""),
            "pivot_value": config.get("pivot_value_column", ""),
            "pivot_aggregate": config.get("pivot_aggregate", "first"),
        },
        {
            "type": "output",
            "path": output_path,
            "delimiter": config.get("output_delimiter", ","),
            "encoding": config.get("output_encoding", "utf-8"),
            "collision_policy": config.get("output_collision_policy", "replace"),
        },
    ]


def build_workflow(
    config: dict,
    input_patterns: Iterable[str | Path] = (),
    output_path: str | Path | None = None,
    tool_version: str = "unknown",
    input_files: Iterable[str | Path] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Create a versioned workflow document from a legacy config dictionary."""

    patterns = [str(path) for path in input_patterns]
    output = str(output_path) if output_path else None
    document = {
        "format": WORKFLOW_FORMAT,
        "version": WORKFLOW_VERSION,
        "tool_version": tool_version,
        "inputs": {
            "patterns": patterns,
            "files": capture_file_metadata(input_files or patterns),
        },
        "output": {
            "path": output,
            "collision_policy": config.get("output_collision_policy", "replace"),
        },
        "operations": operations_from_config(config, output, patterns),
        "config": copy.deepcopy(config),
        "metadata": copy.deepcopy(metadata or {}),
    }
    document["workflow_sha256"] = _workflow_hash(document)
    return document


def _validate_workflow_payload(data: dict) -> None:
    if not isinstance(data.get("config"), dict):
        raise WorkflowError("Workflow config must be an object")
    if not isinstance(data.get("inputs"), dict) or not isinstance(data["inputs"].get("patterns", []), list):
        raise WorkflowError("Workflow inputs.patterns must be a list")
    if not isinstance(data.get("operations"), list) or not data["operations"]:
        raise WorkflowError("Workflow operations must be a non-empty list")


def _normalize_workflow_payload(data: dict) -> dict:
    _validate_workflow_payload(data)
    normalized = copy.deepcopy(data)
    expected_hash = normalized.get("workflow_sha256")
    normalized["workflow_sha256"] = _workflow_hash(normalized)
    if expected_hash and expected_hash != normalized["workflow_sha256"]:
        raise WorkflowError("Workflow identity hash does not match its contents")
    return normalized


def migrate_workflow(data: dict) -> dict:
    """Upgrade a supported workflow document and recompute its identity hash.

    Version 2 records the schema revision in metadata. The migration is
    intentionally additive, so the execution configuration and ordered
    operations retain their version-1 meaning while the new document gets a
    new, correctly recomputed identity hash.
    """

    if not isinstance(data, dict):
        raise WorkflowError("Workflow must be a JSON object")
    if data.get("format") != WORKFLOW_FORMAT:
        raise WorkflowError(f"Unsupported workflow format: {data.get('format')!r}")
    version = data.get("version")
    if version == WORKFLOW_VERSION:
        return _normalize_workflow_payload(data)
    if version == LEGACY_WORKFLOW_VERSION:
        migrated = _normalize_workflow_payload(data)
        metadata = migrated.get("metadata", {})
        if not isinstance(metadata, dict):
            raise WorkflowError("Workflow metadata must be an object")
        metadata = copy.deepcopy(metadata)
        metadata.setdefault("migrated_from_version", LEGACY_WORKFLOW_VERSION)
        metadata["schema_revision"] = WORKFLOW_VERSION
        migrated["metadata"] = metadata
        migrated["version"] = WORKFLOW_VERSION
        migrated.pop("workflow_sha256", None)
        return _normalize_workflow_payload(migrated)
    if isinstance(version, int) and version > WORKFLOW_VERSION:
        raise WorkflowError(
            f"Workflow version {version} is newer than supported version {WORKFLOW_VERSION}; "
            "upgrade CSV Power Tool before replaying it"
        )
    raise WorkflowError(
        f"Unsupported workflow version {version!r}; supported versions are "
        f"{LEGACY_WORKFLOW_VERSION} and {WORKFLOW_VERSION}"
    )


def normalize_workflow(data: dict, tool_version: str = "unknown") -> dict:
    """Validate a workflow and migrate plain-config or version-1 documents."""

    if not isinstance(data, dict):
        raise WorkflowError("Workflow must be a JSON object")
    if "format" not in data:
        return build_workflow(
            data,
            tool_version=tool_version,
            metadata={"migrated_from": "legacy-config"},
        )
    return migrate_workflow(data)


def load_workflow(path: str | Path) -> dict:
    workflow_path = Path(path)
    try:
        data = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Unable to read workflow {workflow_path}: {exc}") from exc
    return normalize_workflow(data)


def extract_config(document: dict) -> dict:
    return copy.deepcopy(normalize_workflow(document)["config"])


def workflow_inputs(document: dict) -> list[str]:
    return [str(value) for value in normalize_workflow(document)["inputs"].get("patterns", [])]


def workflow_output(document: dict) -> str | None:
    value = normalize_workflow(document).get("output", {}).get("path")
    return str(value) if value else None


def write_workflow(path: str | Path, document: dict) -> Path:
    workflow_path = Path(path)
    normalized = normalize_workflow(document)
    _atomic_json_write(workflow_path, normalized)
    return workflow_path


def changed_fields(previous: dict | None, current: dict) -> list[str]:
    if not previous:
        return []
    keys = ("tool_version", "inputs", "output", "operations", "config")
    return [key for key in keys if previous.get(key) != current.get(key)]


def operation_types(document: dict) -> list[str]:
    return [str(operation.get("type", "unknown")) for operation in normalize_workflow(document)["operations"]]


def append_history(
    path: str | Path,
    document: dict,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> dict:
    """Append a validated workflow to bounded atomic history storage."""

    history_path = Path(path)
    normalized = normalize_workflow(document)
    maximum = max(1, int(limit))
    records = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"Unable to read workflow history {history_path}: {exc}") from exc
        history = normalize_history(history)
        records = history["records"]

    previous = records[-1]["workflow"] if records else None
    record = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "changed_fields": changed_fields(previous, normalized),
        "workflow": normalized,
    }
    records.append(record)
    payload = {
        "format": HISTORY_FORMAT,
        "version": HISTORY_VERSION,
        "limit": maximum,
        "records": records[-maximum:],
    }
    _atomic_json_write(history_path, payload)
    return record


def normalize_history(data: dict) -> dict:
    """Validate and migrate a workflow history document before it is written."""

    if not isinstance(data, dict):
        raise WorkflowError("Workflow history must be a JSON object")
    if data.get("format") != HISTORY_FORMAT:
        raise WorkflowError(f"Unsupported workflow history format: {data.get('format')!r}")
    version = data.get("version")
    if version not in {LEGACY_HISTORY_VERSION, HISTORY_VERSION}:
        if isinstance(version, int) and version > HISTORY_VERSION:
            raise WorkflowError(
                f"Workflow history version {version} is newer than supported version {HISTORY_VERSION}; "
                "upgrade CSV Power Tool before appending to it"
            )
        raise WorkflowError(
            f"Unsupported workflow history version {version!r}; expected {HISTORY_VERSION}"
        )
    records = data.get("records", [])
    if not isinstance(records, list):
        raise WorkflowError("Workflow history records must be a list")
    normalized_records = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("workflow"), dict):
            raise WorkflowError("Workflow history contains an invalid record")
        normalized_record = copy.deepcopy(record)
        normalized_record["workflow"] = normalize_workflow(record["workflow"])
        normalized_records.append(normalized_record)

    normalized = copy.deepcopy(data)
    normalized["records"] = normalized_records
    if version == LEGACY_HISTORY_VERSION:
        normalized["version"] = HISTORY_VERSION
        metadata = normalized.get("metadata", {})
        if not isinstance(metadata, dict):
            raise WorkflowError("Workflow history metadata must be an object")
        metadata = copy.deepcopy(metadata)
        metadata.setdefault("migrated_from_version", LEGACY_HISTORY_VERSION)
        normalized["metadata"] = metadata
    normalized.setdefault("limit", DEFAULT_HISTORY_LIMIT)
    try:
        normalized["limit"] = max(1, int(normalized["limit"]))
    except (TypeError, ValueError) as exc:
        raise WorkflowError("Workflow history limit must be a positive integer") from exc
    return normalized


def load_history(path: str | Path) -> dict:
    history_path = Path(path)
    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Unable to read workflow history {history_path}: {exc}") from exc
    return normalize_history(data)


def _atomic_json_write(path: Path, payload: dict) -> None:
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
            handle.write(canonical_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
