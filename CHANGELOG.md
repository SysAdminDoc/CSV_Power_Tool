# Changelog

All notable changes to CSV_Power_Tool will be documented in this file.

## [Unreleased] - 2026-08-03

- Added: JSONL/NDJSON streaming input and output.
- Added: Source-file provenance columns, canonical column templates, schema-drift reports, type inference, and delimiter/encoding confidence diagnostics.
- Added: Pivot/unpivot reshaping, SQL-style keyed joins, and keyed three-way merge primitives with conflict resolution.
- Added: Optional sensitive-value redaction and per-column processing summaries.
- Added: Explicit Polars text parsing backend for large in-memory jobs, with the existing Python and streaming paths preserved.
- Added: Non-mutating duplicate previews through the engine and `--dedupe-preview` CLI export.

## [v3.1.0] - 2026-06-27

- Added: Standard dependency metadata with `requirements.txt` and `pyproject.toml`.
- Added: Excel `.xlsx` and Parquet input/output support.
- Added: Date-aware `Between` filters with timezone normalization.
- Added: Fuzzy match filters and fuzzy dedupe threshold support.
- Added: Recursive GUI/CLI input expansion and CLI watch mode.
- Added: Bounded-memory streaming for text inputs when sorting and dedupe are disabled.
- Fixed: Runtime dependency installation was removed.
- Fixed: Compute/split/merge transform columns now reach output writers.
- Fixed: README version and setup commands.
