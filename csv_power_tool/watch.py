"""Restart-safe polling state for CLI watch mode."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass, asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


WATCH_STATE_FORMAT = "csv-power-tool-watch-state"
WATCH_STATE_VERSION = 1
EDGE_SAMPLE_BYTES = 64 * 1024


class WatchStateError(ValueError):
    """An invalid or newer persisted watch state cannot be used safely."""


@dataclass(frozen=True)
class WatchDecision:
    action: str
    should_process: bool
    run_id: str | None = None
    deleted_paths: tuple[str, ...] = ()


def _path_key(path: str | Path) -> str:
    return str(Path(path).resolve())


def _edge_digest(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        if size <= EDGE_SAMPLE_BYTES * 2:
            digest.update(handle.read())
        else:
            digest.update(handle.read(EDGE_SAMPLE_BYTES))
            handle.seek(-EDGE_SAMPLE_BYTES, os.SEEK_END)
            digest.update(handle.read(EDGE_SAMPLE_BYTES))
    return digest.hexdigest()


def file_signature(paths: Iterable[str | Path]) -> tuple[tuple, ...]:
    """Capture identity, timestamps, size, and edge content for input files."""

    entries = []
    seen = set()
    for raw_path in paths:
        path = Path(raw_path)
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            stat = path.stat()
            entries.append(
                (
                    key,
                    int(getattr(stat, "st_dev", 0)),
                    int(getattr(stat, "st_ino", 0)),
                    int(stat.st_size),
                    int(stat.st_mtime_ns),
                    int(getattr(stat, "st_ctime_ns", 0)),
                    _edge_digest(path, stat.st_size),
                )
            )
        except (OSError, ValueError):
            continue
    return tuple(sorted(entries, key=lambda entry: entry[0].lower()))


def _signature_to_json(signature: tuple[tuple, ...]) -> list[dict]:
    return [
        {
            "path": entry[0],
            "device": entry[1],
            "inode": entry[2],
            "size": entry[3],
            "mtime_ns": entry[4],
            "ctime_ns": entry[5],
            "edge_sha256": entry[6],
        }
        for entry in signature
    ]


def _signature_from_json(value) -> tuple[tuple, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise WatchStateError("Watch state signatures must be lists")
    entries = []
    for item in value:
        if not isinstance(item, dict):
            raise WatchStateError("Watch state contains an invalid file signature")
        try:
            entries.append(
                (
                    str(item["path"]),
                    int(item.get("device", 0)),
                    int(item.get("inode", 0)),
                    int(item["size"]),
                    int(item["mtime_ns"]),
                    int(item.get("ctime_ns", 0)),
                    str(item["edge_sha256"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WatchStateError("Watch state contains an incomplete file signature") from exc
    return tuple(sorted(entries, key=lambda entry: entry[0].lower()))


def workflow_fingerprint(config, input_patterns: Iterable[str | Path], output_path) -> str:
    """Hash the inputs and processing configuration used by a watch session."""

    if is_dataclass(config):
        config = asdict(config)
    payload = {
        "config": config,
        "input_patterns": [str(pattern) for pattern in input_patterns],
        "output": str(output_path) if output_path is not None else None,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class WatchCoordinator:
    """Debounce file changes and persist the last attempted/successful run."""

    def __init__(
        self,
        state_path: str | Path,
        workflow_sha256: str,
        settle_seconds: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        if float(settle_seconds) < 0:
            raise WatchStateError("Watch settle time must be zero or greater")
        self.state_path = Path(state_path)
        self.workflow_sha256 = str(workflow_sha256)
        self.settle_seconds = float(settle_seconds)
        self.clock = clock
        self._pending_signature = None
        self._pending_since = None
        self.state = self._load_state()
        if self.state.get("workflow_sha256") != self.workflow_sha256:
            self.state["workflow_sha256"] = self.workflow_sha256
            self.state["last_attempt_signature"] = None
            self.state["last_successful_signature"] = None
            self.state["last_event"] = "workflow-changed"
            self._save()

    def _default_state(self) -> dict:
        return {
            "format": WATCH_STATE_FORMAT,
            "version": WATCH_STATE_VERSION,
            "workflow_sha256": self.workflow_sha256,
            "last_observed_signature": [],
            "last_attempt_signature": None,
            "last_successful_signature": None,
            "last_run_id": None,
            "active_run_id": None,
            "last_exit": None,
            "last_event": "initial",
            "last_deleted_paths": [],
        }

    def _load_state(self) -> dict:
        if not self.state_path.exists():
            return self._default_state()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WatchStateError(f"Unable to read watch state {self.state_path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("format") != WATCH_STATE_FORMAT:
            raise WatchStateError(f"Unsupported watch state format in {self.state_path}")
        version = data.get("version")
        if version != WATCH_STATE_VERSION:
            if isinstance(version, int) and version > WATCH_STATE_VERSION:
                raise WatchStateError(
                    f"Watch state version {version} is newer than supported version "
                    f"{WATCH_STATE_VERSION}; remove or upgrade CSV Power Tool"
                )
            raise WatchStateError(f"Unsupported watch state version {version!r}")
        for key in ("last_observed_signature", "last_attempt_signature", "last_successful_signature"):
            _signature_from_json(data.get(key))
        return data

    def _save(self) -> None:
        _atomic_json_write(self.state_path, self.state)

    def observe(self, paths: Iterable[str | Path]) -> WatchDecision:
        current = file_signature(paths)
        previous = _signature_from_json(self.state.get("last_observed_signature"))
        deleted_paths = tuple(sorted(
            {entry[0] for entry in previous} - {entry[0] for entry in current},
            key=str.lower,
        ))
        if self._pending_signature != current:
            self._pending_signature = current
            self._pending_since = self.clock()
            self.state["last_observed_signature"] = _signature_to_json(current)
            self.state["last_deleted_paths"] = list(deleted_paths)
            self.state["last_event"] = "deleted" if deleted_paths else "changed"
            self._save()
            return WatchDecision(self.state["last_event"], False, deleted_paths=deleted_paths)

        pending_since = self._pending_since if self._pending_since is not None else self.clock()
        elapsed = self.clock() - pending_since
        if elapsed < self.settle_seconds:
            self.state["last_event"] = "settling"
            self._save()
            return WatchDecision("settling", False, deleted_paths=deleted_paths)

        attempt = _signature_from_json(self.state.get("last_attempt_signature"))
        if current == attempt:
            self.state["last_event"] = "waiting" if not current else "unchanged"
            self._save()
            return WatchDecision(self.state["last_event"], False, deleted_paths=deleted_paths)

        if not current:
            self.state["last_attempt_signature"] = _signature_to_json(current)
            self.state["last_event"] = "deleted" if deleted_paths else "waiting"
            self._save()
            return WatchDecision(self.state["last_event"], False, deleted_paths=deleted_paths)

        run_id = secrets.token_hex(16)
        self.state["last_attempt_signature"] = _signature_to_json(current)
        self.state["active_run_id"] = run_id
        self.state["last_run_id"] = run_id
        self.state["last_event"] = "ready"
        self._save()
        return WatchDecision("ready", True, run_id=run_id, deleted_paths=deleted_paths)

    def mark_result(self, run_id: str, exit_code: int) -> None:
        if self.state.get("active_run_id") != run_id:
            raise WatchStateError("Watch run ID does not match the active persisted run")
        self.state["active_run_id"] = None
        self.state["last_exit"] = int(exit_code)
        self.state["completed_at"] = datetime.now(timezone.utc).isoformat()
        if int(exit_code) == 0:
            self.state["last_successful_signature"] = self.state.get("last_attempt_signature")
            self.state["last_event"] = "processed"
        else:
            self.state["last_event"] = "failed"
        self._save()
