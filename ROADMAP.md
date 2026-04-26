# CSV Power Tool — Roadmap

Python/customtkinter CSV combiner and processor. Multi-column sort, 11 filter operators, dedup, transforms, flexible output, configuration presets. Drag-drop, dark theme.

## Planned Features

### Engine
- **Swap the core to polars** (current implementation appears to use stdlib `csv`) — 10×+ speedup on large files; keep pandas as optional import
- **Streaming pipeline** — process files larger than RAM (iterator-of-rows throughout)
- **Auto-detect delimiter / quote / encoding** on add (current README says supported; harden with `chardet` fallback + row-probe)
- **Header normalization** — strip BOM, trim, lowercase, snake_case as optional transform
- **Schema unification** — when files differ in columns: union / intersection / explicit-mapping modes
- **Excel `.xlsx` and Parquet** inputs and outputs

### Transforms
- **Compute column** — simple expression language (e.g., `total = qty * price`)
- **Split column** by delimiter into N children
- **Merge columns** with separator
- **Date parsing** with format-autodetect + timezone normalization
- **Trim / case / replace** per-column rather than global only
- **Regex replace** as a first-class transform (not just filter)

### Filter / Sort
- `Between` numeric + date operators
- `In list` / `Not in list` with paste-multi-value dialog
- Fuzzy-match operator (rapidfuzz, optional)
- Stable multi-key sort with locale-aware collation (German/French diacritics)

### Dedup
- **Fuzzy dedup** — group by column similarity ≥ threshold (rapidfuzz)
- **Keep-aggregate** — on duplicate rows, keep max/min/sum/concat of non-key columns
- **Preview** — show what will be dropped before committing

### CLI
- `csv_power_tool --config preset.json --inputs *.csv --output combined.csv` headless mode
- Exit codes: 0 OK, 1 no input matched, 2 schema mismatch, 3 filter/transform error
- Watch mode — re-run when a watched folder changes

### UI / UX
- Preview pane — first 100 rows of the *projected* output update live as settings change
- Column-level row-count & distinct-count summary
- Drag-reorder output columns
- Undo/redo of preset edits
- Dark / Light / System toggle

### Packaging
- PyInstaller one-file + code sign
- MSI via wix
- winget manifest + Chocolatey package

## Competitive Research
- **Power Query (Excel 365)** — gold standard for Excel users, folder combine, refresh. Our edge: cross-platform, open source, no Office licence.
- **csv-merge ([deviousasti/csv-merge](https://github.com/deviousasti/csv-merge))** — drag-drop GUI, `(Source)` column tracking, key-based combine. Worth emulating the source-column feature.
- **CSV Combiner (csvcombiner.com)** — browser-based, client-side only. Good reference for "zero install" appeal; we already run local.
- **Terminal pipelines** (`head/tail/cat`, `miller`, `csvkit`) — power users reach here for scripting. Ship a CLI so CSV Power Tool bridges GUI and scriptable workflows.

## Nice-to-Haves
- JSON Lines + NDJSON input/output
- SQL window over loaded files via DuckDB (`SELECT * FROM read_csv_auto('*.csv')`)
- Pivot / unpivot wizard
- Column-type inference report before processing (flags mixed types)
- Drop "sensitive" columns heuristic — detect SSN / credit-card / email patterns, redact option
- Web-upload target — drag a file to a local-only `http://127.0.0.1:PORT`, same engine runs

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
- **Recursive folder drop** (deviousasti/csv-merge) — drop a project folder; tool walks subdirs and collects all `*.csv` / `*.tsv`. Useful for the "Project/Outputs/BOM.csv" pattern.
- **Automatic `(Source)` column** (deviousasti/csv-merge) — injected column preserves which file each row came from; complements the existing "filename / full path as column" feature with sensible defaults.
- **Column-order-from-selected-file** (deviousasti/csv-merge) — when files have different column orders, pick one file as the canonical template and reorder the rest.
- **Key-based combining modes** (deviousasti/csv-merge) — when merging by a key column, choose set-union (discard duplicates), concat-with-separator (`"a; b; c"`), or sum for numeric columns. Complements the current "deduplicate" feature with richer collapse semantics.
- **Continuous/watched-folder mode** (jlumbroso/csv-merge) — watch a folder and re-emit the merged output whenever any source file changes; ideal for lab instruments and log exporters.
- **3-way diff/merge with git driver** (sctweedie/csvdiff3) — register as `.gitattributes merge=csvdiff3` so CSVs in git actually merge cleanly between branches.
- **Browser-side joiner as a second delivery target** (csv-joiner) — same core engine compiled to WASM for a zero-install web version; nice distribution upsell.
- **Join-type selector (INNER/LEFT/RIGHT/OUTER)** (csv-joiner) — beyond concat/merge, support SQL-style joins across two-plus files on a key column with conflict-resolution strategy.
- **Schema-drift report** — before merging, emit a report of every column, which files have it, and sample values; prevents silent data loss when one file's header was spelled differently.

### Patterns & Architectures Worth Studying
- **Pandas-free core, pyarrow-backed** — several modern CSV tools have moved off pandas to polars/pyarrow for 5–10x speed on multi-GB files; since CSV Power Tool already uses pandas, a polars backend as opt-in would unlock very large merges.
- **Streaming row iterator with bounded memory** (richardARPANET/csv-merge) — stream-emit one file at a time to the writer instead of concatenating in memory; critical for files >RAM.
- **Git-merge driver registration** (sctweedie/csvdiff3) — ship a small PowerShell script that registers the tool as a git merge driver with one command (`csvpower --register-git-driver`).
- **Delimiter/encoding detection with `chardet` + `csv.Sniffer`** (multiple) — show a confidence score and let the user override before merge, rather than silently misdetecting.
- **Live-preview pane** (csv-joiner, deviousasti) — top-N rows of the projected merged output updates as options change; catches order/column-mismatch bugs before writing GB of data.
