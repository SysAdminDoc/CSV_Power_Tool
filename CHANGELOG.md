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
- CLI: Added format-explicit CSV/TSV/JSONL stdin/stdout pipes, versioned stats/error JSON artifacts, stderr-only human logs, and documented exit codes including cancellation 130.
- SQL: Added format-aware CSV/TSV/TXT, XLSX, Parquet, and JSONL/NDJSON adapters with named-view schema/limitation reports, read-only local execution, bounded source/result rows and cells, memory/time budgets, interruptible failures, and manifest/CLI report integration.
- Contracts: Exposed validated source-to-output column mappings through GUI configuration, workflows, schema reports, manifests, and repeated `--rename SOURCE=TARGET` CLI options; ambiguous or stale mappings now fail before output replacement.
- Performance: Added an explicit non-streaming Parquet materialization budget with actionable admission failures and execution-mode reporting; Arrow-batched streaming remains available for compatible operations.
- API: Published the versioned loopback contract at `/contract`, added health metadata, stable error correlation, run IDs, documented limits, and independent raw/multipart/error/concurrency/cleanup coverage.
- Workflow: Added atomic version-1-to-current migrations for workflow and history documents, identity-hash recomputation, and actionable rejection of unsupported future versions.
- Dependencies: Promoted the validated tkinterdnd2, chardet, packaging, PyArrow, DuckDB, setuptools, and PyInstaller pins; added Python 3.14 CI coverage, lock/environment reporting, and a CycloneDX 1.5 SBOM. CustomTkinter remains at the known 5.2.2 pin pending isolated graphical validation.
- GUI: Extended the accessibility/i18n contract with localized appearance labels, stable accessible descriptions, focus-contract snapshots, focus restoration after shell rebuilds, and a 100/125/150% DPI smoke matrix across dark/light/system themes and responsive layouts.
- Watch: Added settled-file debouncing, replacement/truncation/deletion detection, workflow/configuration fingerprints, atomic restart-safe state, run IDs, and explicit CLI controls for settle windows and state paths.

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

## Roadmap archive — 2026-08-10 — ROADMAP.md

<details>
<summary>Original roadmap snapshot</summary>

```markdown
# CSV Power Tool — Roadmap

Python/customtkinter CSV combiner and processor. Multi-column sort, 16 filter operators, dedup, per-column transforms, compute columns, flexible output, configuration presets, CLI mode. Drag-drop, dark theme.

## Planned Features

### Engine

### Filter / Sort

### Dedup

### CLI

### UI / UX

### Packaging

## Competitive Research
- **Power Query (Excel 365)** — gold standard for Excel users, folder combine, refresh. Our edge: cross-platform, open source, no Office licence.
- **csv-merge ([deviousasti/csv-merge](https://github.com/deviousasti/csv-merge))** — drag-drop GUI, `(Source)` column tracking, key-based combine. Worth emulating the source-column feature.
- **CSV Combiner (csvcombiner.com)** — browser-based, client-side only. Good reference for "zero install" appeal; we already run local.
- **Terminal pipelines** (`head/tail/cat`, `miller`, `csvkit`) — power users reach here for scripting. Ship a CLI so CSV Power Tool bridges GUI and scriptable workflows.

## Nice-to-Haves

## Open-Source Research (Round 2)

### Related OSS Projects
- **deviousasti/csv-merge** — https://github.com/deviousasti/csv-merge — Drag-and-drop GUI, folder recursion, column-order selection, `(Source)` column injection, key-based combining with union/concat/sum.
- **ParthBapaye1/csv-merge_tool** — https://github.com/ParthBapaye1/csv-merge_tool — Drag-and-drop fork variant.
- **jlumbroso/csv-merge** — https://github.com/jlumbroso/csv-merge — Continuous/watched-folder merge.
- **richardARPANET/csv-merge** — https://github.com/richardARPANET/csv-merge — Clean CLI reference.
- **behroozk/csv-merger** — https://github.com/behroozk/csv-merger — Minimal multi-file combiner.
- **sctweedie/csvdiff3** — https://github.com/sctweedie/csvdiff3 — 3-way diff/merge for CSV; git-merge driver compatible.
- **sensorfactdev/csv-joiner** — https://sensorfactdev.github.io/csv-joiner/ — Browser-side joiner with live preview.
- **GitHub topic: csv-combine** — https://github.com/topics/csv-combine — catalog of alternatives.

### Features to Borrow

### Patterns & Architectures Worth Studying

## Research-Driven Additions

- [ ] P2 — Add an accessible, localized, responsive GUI shell
  Why: The fixed customtkinter layout, emoji-heavy labels, and absence of a string catalog or accessibility contract make the product harder to use with keyboard navigation, high-DPI settings, assistive technology, or a non-English locale.
  Evidence: The repository’s 125% DPI environment and customtkinter’s scaling support make geometry a real acceptance concern; active desktop competitors expose keyboard-first and readable data views, while the current GUI has no focused accessibility/i18n test layer.
  Touches: GUI layout/widgets, string resources, themes, keyboard bindings, tests, README accessibility notes.
  Acceptance: Provide complete keyboard traversal/focus order, visible focus states, non-emoji accessible labels, scalable/resizable layouts, measured light/dark contrast, tooltip/status alternatives, externalized strings with fallback, and a smoke test at the supported DPI/theme combinations without stealing interactive focus.
  Complexity: L

- [ ] P2 — Make watch mode debounced and restart-safe
  Why: Polling file changes can process a file while it is still being written, duplicate work, or mishandle rotation/truncation, undermining automation trust.
  Evidence: The current watch mode polls mtimes/sizes; `jlumbroso/csv-merge` and CEESVEE follow-mode patterns show the need for settled-file, rotation, pause, and restart semantics.
  Touches: watch loop, run manifest/state handling, atomic output path, CLI/UI status, tests, README watch documentation.
  Acceptance: Wait for a configurable stable size/mtime window; detect replacement, rotation, truncation, and deletion; avoid duplicate processing using file identity plus workflow hash; persist/recover last successful run metadata; coordinate with atomic output and cancellation; test rapid writes, partial files, restart, and rotation.
  Complexity: M

- [ ] P2 — Align README, screenshots, packaging docs, and release surface with v3.2.0
  Why: The checked-in screenshot shows a v2-era empty interface while the code, CLI, and README describe v3.2.0. Stale visuals and incomplete package instructions weaken trust in the features that already exist.
  Evidence: Repository inspection found the version mismatch; qsv and OpenRefine release histories show documentation drift and package/runtime issues are recurring maintenance work; marketplace publication remains blocked and must not be implied as shipped.
  Touches: `README.md`, `screenshot.png`, `packaging/build.py`, WiX/package documentation, release checklist.
  Acceptance: Regenerate a current screenshot through invisible isolated verification, document the actual GUI/CLI/API/SQL inputs and outputs, document unsigned EXE/MSI and SHA-256 verification, make version strings/checks automated, and clearly label winget/Chocolatey/code-signing work as blocked rather than available.
  Complexity: M

- [ ] P3 — Define a safe transform plugin extension surface
  Why: Miller UDFs, RBQL UDFs, and VisiData plugins show demand for extensibility, but loading arbitrary Python from a data workflow creates a new code-execution boundary.
  Evidence: Competitor extension models are useful; the PyArrow and DuckDB security findings show why capability and trust boundaries must be explicit.
  Touches: plugin manifest/schema, discovery and policy code, CLI/GUI settings, packaging, tests, README extension documentation.
  Acceptance: Specify a versioned manifest and capability list before loading code; require explicit per-plugin opt-in and show provenance in the run manifest; default packaged builds to no third-party execution; reject incompatible or unsigned-by-policy plugins with actionable errors; document that this is trusted local code, not a sandbox; add discovery, denial, and compatibility tests.
  Complexity: L
```

</details>
