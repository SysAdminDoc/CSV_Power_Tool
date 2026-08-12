"""Small, dependency-free catalog for the GUI shell.

The processing engine remains language-neutral.  GUI modules use this catalog
for visible shell labels and fall back to English when a locale is incomplete
or unavailable, so adding a locale cannot make a control silently disappear.
"""

from __future__ import annotations

from typing import Mapping

DEFAULT_LOCALE = "en"
_CURRENT_LOCALE = DEFAULT_LOCALE

LOCALE_LABELS: Mapping[str, str] = {
    "en": "English",
    "es": "Español",
}

TRANSLATIONS: Mapping[str, Mapping[str, str]] = {
    "en": {
        "input_files": "Input Files",
        "column_selection": "Column Selection",
        "sorting": "Sorting",
        "deduplication": "Deduplication",
        "filters": "Filters",
        "transformations": "Transformations",
        "output_settings": "Output Settings",
        "processing_log": "Processing Log",
        "preview": "Preview",
        "data_quality": "Data Quality",
        "add_files": "Add Files",
        "add_folder": "Add Folder",
        "clear": "Clear",
        "remove": "Remove",
        "move": "Move",
        "drag_drop_files": "Drag and drop supported files here\nor use the buttons below",
        "select_input_files": "Select Input Files",
        "select_folder": "Select Folder",
        "theme": "Theme",
        "language": "Language",
        "scale": "Scale",
        "dark": "Dark",
        "light": "Light",
        "system": "System",
        "ascending": "A to Z",
        "descending": "Z to A",
        "ready": "Ready",
        "keyboard_help": "Keyboard: Tab or Shift+Tab to navigate; Alt+P to process; Escape to cancel",
        "save_config": "Save Configuration",
        "load_config": "Load Configuration",
        "undo": "Undo",
        "redo": "Redo",
        "cancel": "Cancel",
        "process_files": "Process Files",
        "refresh": "Refresh",
        "all_columns": "All Columns",
        "include_selected": "Include Selected",
        "exclude_selected": "Exclude Selected",
        "select_all": "Select All",
        "select_none": "Select None",
        "add_sort_rule": "Add Sort Rule",
        "add_filter": "Add Filter",
        "add_transform": "Add Transform",
        "clear_all": "Clear All",
        "no_columns": "No columns",
        "no_sort_rules": "No sort rules defined",
        "no_filters": "No filters defined",
        "no_transforms": "No per-column transforms defined",
        "discover_columns": "Add files to discover columns",
        "preview_empty": "Add files to preview projected output",
        "file": "File",
        "column": "Column",
        "file_count_one": "{count} file",
        "file_count_many": "{count} files",
        "column_count_none": "No columns",
        "column_count_many": "{count} columns",
        "case_sensitive": "Case sensitive",
        "numeric_aware": "Numeric-aware",
        "keep": "Keep:",
        "first": "First",
        "last": "Last",
        "none": "None",
        "use_all_columns": "Use all columns for comparison",
        "fuzzy_duplicate_matching": "Fuzzy duplicate matching",
        "threshold": "Threshold",
        "aggregate": "Aggregate:",
        "separator": "Separator",
        "and": "AND",
        "or": "OR",
        "value": "Value...",
        "trim_whitespace": "Trim whitespace",
        "case": "Case:",
        "headers": "Headers:",
        "replace_empty_cells": "Replace empty cells with:",
        "leave_blank": "(leave blank)",
        "per_column_transforms": "Per-Column Transforms",
        "output_name": "Output name",
        "search": "Search...",
        "replace_with": "Replace with...",
        "edit_number": "#",
        "delimiter": "Delimiter:",
        "encoding": "Encoding:",
        "quoting": "Quoting:",
        "line_end": "Line End:",
        "include_header_row": "Include header row",
        "output_file": "Output File:",
        "select_output_file": "Select output file...",
        "browse": "Browse",
        "save_as": "Save As",
        "clear_log": "Clear",
        "cancel_preview": "Cancel",
        "profile": "Profile",
        "quality_description": (
            "Inspect distributions, drill into a facet, and record exact reviewed text edits. "
            "The global Undo/Redo controls include these edits."
        ),
        "find_column_or_facet": "Find column or facet",
        "facet_filter": "Facet filter: column=value",
        "no_profile_loaded": "No profile loaded",
        "row_inspection": "Row inspection (global 1-based row)",
        "row": "Row",
        "inspect": "Inspect",
        "reviewed_repairs": "Reviewed repairs (text replacements)",
        "raw_text_expected": "Raw text is compared when Expected is set",
        "expected_old": "Expected old",
        "replacement": "Replacement",
        "reason": "Reason",
        "add": "Add",
        "remove_number": "Remove #",
        "reviewed_edits_status": (
            "Reviewed edits are applied before filters/transforms and written to the manifest."
        ),
        "no_reviewed_edits": "(no reviewed edits)",
        "profile_ready": "Profile ready: {matched:,} matching row(s), {source:,} source row(s) scanned",
        "no_matching_row": "No matching row was found.",
        "inspection_header": "row {row:,} | source: {source}",
        "inspection_columns": "column                  raw text                              inferred type",
        "no_row_selected": "Select a row to inspect source text.",
        "repair_row_invalid": "Repair row must be a positive integer",
        "repair_not_added": "Repair not added: {message}",
        "edits_recorded": "Recorded {count:,} reviewed edit(s)",
        "remove_index_invalid": "Remove # must be a positive edit number",
        "remove_index_missing": "That reviewed edit number does not exist",
        "invalid_saved_repairs": "Invalid saved repairs: {message}",
        "column_summary": "Column Summary",
        "files_processed": "Files Processed",
        "rows_read": "Rows Read",
        "rows_filtered": "Rows Filtered",
        "duplicates_removed": "Duplicates Removed",
        "final_rows": "Final Rows",
        "combine_description": "Combine · Filter · Transform · Deduplicate · Export",
        "preview_cancelled": "Cancelled after scanning {rows} row(s)",
        "preview_limited": (
            "Read-only sample: showing {shown} row(s); scanned {scanned} row(s) within the preview budget"
        ),
        "preview_complete": "Read-only sample: {rows} row(s) scanned within the preview budget",
        "preview_cancelling": "Cancelling read-only preview…",
        "preview_preparing": "Preparing bounded read-only preview…",
        "quality_processing": "Processing in progress",
        "quality_finish_first": "Finish or cancel processing before profiling",
        "quality_add_files": "Add files before profiling",
        "quality_profiling": "Profiling bounded raw rows…",
        "quality_inspecting": "Inspecting raw row {row}…",
        "quality_finish_inspect": "Finish or cancel processing before inspecting",
        "quality_add_files_inspect": "Add files before inspecting a row",
        "processing_starting": "Starting processing...",
        "processing_no_files": "No files selected",
        "processing_complete": "Complete! {rows} rows saved",
        "processing_no_output": "Processing completed with no output",
        "no_columns_discovered": "No columns discovered.",
        "no_rows": "(no rows)",
        "preview_error": "Preview error: {message}",
        "quality_error": "Quality error: {message}",
        "quality_input_error": "Profile completed with an input error: {message}",
        "inspection_error": "Inspection error: {message}",
        "inspected_row": "Inspected raw row {row}",
        "row_not_found": "Row not found",
        "preset_undo": "Preset edit undone",
        "preset_redo": "Preset edit redone",
        "finish_before_profiling": "Finish or cancel processing before profiling",
        "add_files_before_profiling": "Add files before profiling",
        "finish_before_inspecting": "Finish or cancel processing before inspecting",
        "add_files_before_inspecting": "Add files before inspecting a row",
        "no_files_selected": "No files selected",
        "cancelling": "Cancelling...",
        "processing_in_progress": "Processing in progress",
        "profile_error": "Profile error: {message}",
        "profile_input_error": "Profile completed with an input error: {message}",
        "inspect_error": "Inspection error: {message}",
        "inspect_started": "Inspecting raw row {row}…",
        "inspect_complete": "Inspected raw row {row:,}",
        "profile_started": "Profiling bounded raw rows…",
        "cancel_quality": "Cancelling quality operation…",
        "preview_started": "Preparing bounded read-only preview…",
        "cancel_preview_status": "Cancelling read-only preview…",
        "no_quality_profile": "No quality profile yet. Select files and choose Profile.",
        "no_column_summary": "(no column summary)",
        "not_available": "—",
        "files_added_via_drop": "Added {count:,} file(s) via drag and drop",
        "no_files_selected_error": "No files selected",
        "processing_started": "Starting processing...",
        "cancel_started": "Cancelling...",
        "processing_saved": "Complete! {rows:,} rows saved",
        "processing_empty": "Processing completed with no output",
        "workflow_save_error": "Workflow save error: {message}",
        "workflow_load_error": "Error loading workflow: {message}",
        "workflow_saved": "Workflow saved: {name} ({operations} operations; {changed})",
        "workflow_loaded": "Workflow loaded: {name} ({operations} operations)",
        "initial_workflow": "initial workflow",
    },
    "es": {
        "input_files": "Archivos de entrada",
        "column_selection": "Selección de columnas",
        "sorting": "Ordenación",
        "deduplication": "Eliminación de duplicados",
        "filters": "Filtros",
        "transformations": "Transformaciones",
        "output_settings": "Ajustes de salida",
        "processing_log": "Registro de procesamiento",
        "preview": "Vista previa",
        "data_quality": "Calidad de datos",
        "add_files": "Añadir archivos",
        "add_folder": "Añadir carpeta",
        "clear": "Limpiar",
        "remove": "Quitar",
        "move": "Mover",
        "drag_drop_files": "Arrastra archivos compatibles aquí\no usa los botones de abajo",
        "select_input_files": "Seleccionar archivos de entrada",
        "select_folder": "Seleccionar carpeta",
        "theme": "Tema",
        "language": "Idioma",
        "scale": "Escala",
        "dark": "Oscuro",
        "light": "Claro",
        "system": "Sistema",
        "ascending": "A a Z",
        "descending": "Z a A",
        "ready": "Listo",
        "keyboard_help": "Teclado: Tab o Mayús+Tab para navegar; Alt+P para procesar; Escape para cancelar",
        "save_config": "Guardar configuración",
        "load_config": "Cargar configuración",
        "undo": "Deshacer",
        "redo": "Rehacer",
        "cancel": "Cancelar",
        "process_files": "Procesar archivos",
        "refresh": "Actualizar",
        "all_columns": "Todas las columnas",
        "include_selected": "Incluir seleccionadas",
        "exclude_selected": "Excluir seleccionadas",
        "select_all": "Seleccionar todo",
        "select_none": "No seleccionar",
        "add_sort_rule": "Añadir regla de ordenación",
        "add_filter": "Añadir filtro",
        "add_transform": "Añadir transformación",
        "clear_all": "Limpiar todo",
        "no_columns": "Sin columnas",
        "no_sort_rules": "No hay reglas de ordenación",
        "no_filters": "No hay filtros definidos",
        "no_transforms": "No hay transformaciones por columna",
        "discover_columns": "Añade archivos para descubrir columnas",
        "preview_empty": "Añade archivos para previsualizar la salida",
        "file": "Archivo",
        "column": "Columna",
        "file_count_one": "{count} archivo",
        "file_count_many": "{count} archivos",
        "column_count_none": "Sin columnas",
        "column_count_many": "{count} columnas",
        "case_sensitive": "Distinguir mayúsculas",
        "numeric_aware": "Con prioridad numérica",
        "keep": "Conservar:",
        "first": "Primero",
        "last": "Último",
        "none": "Ninguno",
        "use_all_columns": "Usar todas las columnas para comparar",
        "fuzzy_duplicate_matching": "Coincidencia difusa de duplicados",
        "threshold": "Umbral",
        "aggregate": "Agregación:",
        "separator": "Separador",
        "and": "Y",
        "or": "O",
        "value": "Valor...",
        "trim_whitespace": "Recortar espacios",
        "case": "Mayúsculas:",
        "headers": "Encabezados:",
        "replace_empty_cells": "Sustituir celdas vacías por:",
        "leave_blank": "(dejar en blanco)",
        "per_column_transforms": "Transformaciones por columna",
        "output_name": "Nombre de salida",
        "search": "Buscar...",
        "replace_with": "Reemplazar por...",
        "edit_number": "#",
        "delimiter": "Delimitador:",
        "encoding": "Codificación:",
        "quoting": "Comillas:",
        "line_end": "Fin de línea:",
        "include_header_row": "Incluir fila de encabezado",
        "output_file": "Archivo de salida:",
        "select_output_file": "Seleccionar archivo de salida...",
        "browse": "Examinar",
        "save_as": "Guardar como",
        "clear_log": "Limpiar",
        "cancel_preview": "Cancelar",
        "profile": "Perfilar",
        "quality_description": (
            "Inspecciona distribuciones, explora una faceta y registra ediciones de texto revisadas. "
            "Los controles globales Deshacer/Rehacer incluyen estas ediciones."
        ),
        "find_column_or_facet": "Buscar columna o faceta",
        "facet_filter": "Filtro de faceta: columna=valor",
        "no_profile_loaded": "No hay perfil cargado",
        "row_inspection": "Inspección de fila (fila global desde 1)",
        "row": "Fila",
        "inspect": "Inspeccionar",
        "reviewed_repairs": "Reparaciones revisadas (reemplazos de texto)",
        "raw_text_expected": "El texto original se compara cuando se indica Esperado",
        "expected_old": "Valor esperado",
        "replacement": "Reemplazo",
        "reason": "Motivo",
        "add": "Añadir",
        "remove_number": "Quitar #",
        "reviewed_edits_status": (
            "Las ediciones revisadas se aplican antes de filtros/transformaciones y se escriben en el manifiesto."
        ),
        "no_reviewed_edits": "(sin ediciones revisadas)",
        "profile_ready": "Perfil listo: {matched:,} fila(s) coincidente(s), {source:,} fila(s) de origen exploradas",
        "no_matching_row": "No se encontró ninguna fila coincidente.",
        "inspection_header": "fila {row:,} | origen: {source}",
        "inspection_columns": "columna                 texto original                       tipo inferido",
        "no_row_selected": "Selecciona una fila para inspeccionar el texto de origen.",
        "repair_row_invalid": "La fila de reparación debe ser un entero positivo",
        "repair_not_added": "No se añadió la reparación: {message}",
        "edits_recorded": "Se registraron {count:,} edición(es) revisada(s)",
        "remove_index_invalid": "Quitar # debe ser un número de edición positivo",
        "remove_index_missing": "Ese número de edición revisada no existe",
        "invalid_saved_repairs": "Reparaciones guardadas no válidas: {message}",
        "column_summary": "Resumen de columnas",
        "files_processed": "Archivos procesados",
        "rows_read": "Filas leídas",
        "rows_filtered": "Filas filtradas",
        "duplicates_removed": "Duplicados eliminados",
        "final_rows": "Filas finales",
        "combine_description": "Combinar · Filtrar · Transformar · Deduplicar · Exportar",
        "preview_cancelled": "Cancelado tras explorar {rows} fila(s)",
        "preview_limited": (
            "Muestra de solo lectura: se muestran {shown} fila(s); se exploraron {scanned} dentro del límite"
        ),
        "preview_complete": "Muestra de solo lectura: {rows} fila(s) exploradas dentro del límite",
        "preview_cancelling": "Cancelando la vista previa de solo lectura…",
        "preview_preparing": "Preparando vista previa acotada de solo lectura…",
        "quality_processing": "Procesamiento en curso",
        "quality_finish_first": "Termina o cancela el procesamiento antes de perfilar",
        "quality_add_files": "Añade archivos antes de perfilar",
        "quality_profiling": "Perfilando filas sin procesar con límite…",
        "quality_inspecting": "Inspeccionando la fila {row}…",
        "quality_finish_inspect": "Termina o cancela el procesamiento antes de inspeccionar",
        "quality_add_files_inspect": "Añade archivos antes de inspeccionar una fila",
        "processing_starting": "Iniciando procesamiento...",
        "processing_no_files": "No hay archivos seleccionados",
        "processing_complete": "¡Completado! {rows} filas guardadas",
        "processing_no_output": "El procesamiento terminó sin salida",
        "no_columns_discovered": "No se descubrieron columnas.",
        "no_rows": "(sin filas)",
        "preview_error": "Error de vista previa: {message}",
        "quality_error": "Error de calidad: {message}",
        "quality_input_error": "El perfil terminó con un error de entrada: {message}",
        "inspection_error": "Error de inspección: {message}",
        "inspected_row": "Fila sin procesar inspeccionada: {row}",
        "row_not_found": "Fila no encontrada",
        "preset_undo": "Edición del preset deshecha",
        "preset_redo": "Edición del preset rehecha",
        "finish_before_profiling": "Termina o cancela el procesamiento antes de perfilar",
        "add_files_before_profiling": "Añade archivos antes de perfilar",
        "finish_before_inspecting": "Termina o cancela el procesamiento antes de inspeccionar",
        "add_files_before_inspecting": "Añade archivos antes de inspeccionar una fila",
        "no_files_selected": "No hay archivos seleccionados",
        "cancelling": "Cancelando...",
        "processing_in_progress": "Procesamiento en curso",
        "profile_error": "Error de perfil: {message}",
        "profile_input_error": "El perfil terminó con un error de entrada: {message}",
        "inspect_error": "Error de inspección: {message}",
        "inspect_started": "Inspeccionando la fila sin procesar {row}…",
        "inspect_complete": "Fila sin procesar inspeccionada: {row:,}",
        "profile_started": "Perfilando filas sin procesar con límite…",
        "cancel_quality": "Cancelando la operación de calidad…",
        "preview_started": "Preparando vista previa acotada de solo lectura…",
        "cancel_preview_status": "Cancelando la vista previa de solo lectura…",
        "no_quality_profile": "Aún no hay perfil de calidad. Selecciona archivos y elige Perfilar.",
        "no_column_summary": "(sin resumen de columnas)",
        "not_available": "—",
        "files_added_via_drop": "Se añadieron {count:,} archivo(s) mediante arrastrar y soltar",
        "no_files_selected_error": "No hay archivos seleccionados",
        "processing_started": "Iniciando procesamiento...",
        "cancel_started": "Cancelando...",
        "processing_saved": "¡Completado! Se guardaron {rows:,} filas",
        "processing_empty": "El procesamiento terminó sin salida",
        "workflow_save_error": "Error al guardar el flujo: {message}",
        "workflow_load_error": "Error al cargar el flujo: {message}",
        "workflow_saved": "Flujo guardado: {name} ({operations} operaciones; {changed})",
        "workflow_loaded": "Flujo cargado: {name} ({operations} operaciones)",
        "initial_workflow": "flujo inicial",
    },
}


def normalize_locale(value: str | None) -> str:
    """Return a supported locale code, accepting labels and locale variants."""

    candidate = (value or DEFAULT_LOCALE).strip().lower().replace("_", "-")
    for code, label in LOCALE_LABELS.items():
        if candidate == label.lower() or candidate == code:
            return code
    language = candidate.split("-", 1)[0]
    return language if language in TRANSLATIONS else DEFAULT_LOCALE


def locale_label(value: str | None) -> str:
    """Return the display label for a locale code or locale label."""

    return LOCALE_LABELS[normalize_locale(value)]


def locale_choices() -> list[str]:
    """Return stable display labels for the locale selector."""

    return list(LOCALE_LABELS.values())


APPEARANCE_MODES = ("dark", "light", "system")


def appearance_choices(locale: str | None = None) -> list[str]:
    """Return localized labels for the appearance selector."""

    return [tr(mode, locale) for mode in APPEARANCE_MODES]


def appearance_label(value: str, locale: str | None = None) -> str:
    """Return the localized label for an appearance mode code."""

    mode = normalize_appearance_mode(value)
    return tr(mode, locale)


def normalize_appearance_mode(value: str | None) -> str:
    """Normalize a localized or legacy appearance label to its stable code."""

    candidate = (value or "dark").strip().lower()
    for mode in APPEARANCE_MODES:
        labels = {
            mode,
            TRANSLATIONS[DEFAULT_LOCALE][mode].lower(),
            *(translations.get(mode, "").lower() for translations in TRANSLATIONS.values()),
        }
        if candidate in labels:
            return mode
    return "dark"


def set_locale(value: str | None) -> str:
    """Set the process-local GUI locale and return its normalized code."""

    global _CURRENT_LOCALE
    _CURRENT_LOCALE = normalize_locale(value)
    return _CURRENT_LOCALE


def current_locale() -> str:
    return _CURRENT_LOCALE


def tr(key: str, locale: str | None = None, **values: object) -> str:
    """Translate a key, falling back to English and finally to the key itself."""

    selected = normalize_locale(locale or _CURRENT_LOCALE)
    text = TRANSLATIONS.get(selected, {}).get(key)
    if text is None:
        text = TRANSLATIONS[DEFAULT_LOCALE].get(key, key)
    return text.format(**values) if values else text
