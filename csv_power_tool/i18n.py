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
