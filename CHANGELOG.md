# Changelog

All notable changes to CSV_Power_Tool will be documented in this file.

## Unreleased

- Security: Hardened the opt-in loopback upload API with per-run authentication tokens, loopback Host/Origin checks, bounded request concurrency and timeouts, multipart limits, SQL exclusion, and stable JSON errors.
- Reliability: Added configurable input byte/row/column/cell/container limits, explicit malformed-row fail/warn/quarantine policies, safe rejection of partial inputs, atomic output replacement, collision backups, and hash-based run manifests.
- Release: Locked runtime and build dependencies, added a clean-build CI lane with dependency auditing, and emitted a dependency/license manifest beside packaged executables.
- Testing: Added parser property fixtures, format/encoding coverage, cancellation checks, CLI contract tests, bounded performance coverage, and clean-package regression smoke tests.
- Architecture: Added dependency-injected core, CLI, GUI, and loopback API seams while preserving the single-file launcher and existing command-line flags.
- Workflow: Added versioned deterministic workflow documents, legacy-config migration, replay/dry-run CLI support, atomic bounded history, and GUI workflow metadata logging.
- Schema: Added first-version Frictionless Table Schema contracts with strict/advisory/quarantine validation, inferred-schema export, machine-readable diagnostics, validation-only CLI execution, and manifest/report integration.
- Performance: Replaced full-pipeline preview work with bounded read-only scans, cancellable GUI previews, a bounded JSON preview artifact, Arrow-batched Parquet input/output, and golden-equivalence/budget tests.
- Quality: Added bounded faceted data-quality profiles, exact raw-row inspection, facet filtering, reviewed text repairs with expected-old guards, undo/redo GUI integration, repair reports, and workflow/manifest provenance.
- Joins: Added schema-aware key normalization and validation, duplicate/cardinality and coercion diagnostics, deterministic anti/semi/full policies, machine-readable join reports, and safe three-way conflict resolution with fail-by-default reports.

## [v3.2.0] - 2026-08-03

- Added: JSONL/NDJSON streaming input and output.
- Added: Source-file provenance columns, canonical column templates, schema-drift reports, type inference, and delimiter/encoding confidence diagnostics.
- Added: Pivot/unpivot reshaping, SQL-style keyed joins, and keyed three-way merge primitives with conflict resolution.
- Added: Optional sensitive-value redaction and per-column processing summaries.
- Added: Explicit Polars text parsing backend for large in-memory jobs, with the existing Python and streaming paths preserved.
- Added: Non-mutating duplicate previews through the engine and `--dedupe-preview` CLI export.
- Added: Projected-output preview, per-column summary statistics, drag-reorderable columns, preset undo/redo, and Dark/Light/System appearance modes.
- Added: Reproducible unsigned PyInstaller one-file and WiX MSI build automation with SHA-256 output.
- Added: DuckDB SQL queries over named input views and a localhost-only upload/process endpoint with request cleanup.

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
