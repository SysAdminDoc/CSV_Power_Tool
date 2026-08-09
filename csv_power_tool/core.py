"""Dependency-injected core processing boundary.

The launcher owns the mature engine implementation for compatibility, while
callers depend on this small service contract instead of constructing engine
objects inside UI or transport adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


EngineFactory = Callable[..., Any]


@dataclass(frozen=True)
class ProcessRequest:
    """Immutable input/output request passed to an engine service."""

    input_files: tuple[Path, ...]
    output_file: Path
    config: Any

    @classmethod
    def from_paths(cls, input_files: Sequence[str | Path], output_file: str | Path, config: Any):
        return cls(tuple(Path(path) for path in input_files), Path(output_file), config)


class EngineService:
    """Create and run processing engines through an injectable factory."""

    def __init__(self, engine_factory: EngineFactory):
        if not callable(engine_factory):
            raise TypeError("engine_factory must be callable")
        self.engine_factory = engine_factory

    def create_engine(self, config: Any, progress_callback=None, log_callback=None):
        return self.engine_factory(
            config,
            progress_callback=progress_callback,
            log_callback=log_callback,
        )

    def process(self, request: ProcessRequest, progress_callback=None, log_callback=None):
        engine = self.create_engine(request.config, progress_callback, log_callback)
        return engine.process(list(request.input_files), request.output_file)

