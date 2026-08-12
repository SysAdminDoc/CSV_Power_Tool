import unittest

from CSV_Consolidator import DARK_COLORS, LIGHT_COLORS
from csv_power_tool.gui_accessibility import (
    accessible_description,
    contrast_ratio,
    focus_contract_snapshot,
    set_accessible_name,
    validate_theme_contrast,
)
from csv_power_tool.i18n import (
    DEFAULT_LOCALE,
    appearance_choices,
    appearance_label,
    locale_choices,
    normalize_appearance_mode,
    normalize_locale,
    set_locale,
    tr,
)


class GuiContractTests(unittest.TestCase):
    def tearDown(self):
        set_locale(DEFAULT_LOCALE)

    def test_theme_text_combinations_meet_normal_text_contrast(self):
        dark = validate_theme_contrast(DARK_COLORS)
        light = validate_theme_contrast(LIGHT_COLORS)
        self.assertGreaterEqual(min(dark.values()), 4.5)
        self.assertGreaterEqual(min(light.values()), 4.5)

    def test_contrast_ratio_is_symmetric(self):
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#000000"), 21.0)
        self.assertAlmostEqual(
            contrast_ratio("#0f172a", "#ffffff"),
            contrast_ratio("#ffffff", "#0f172a"),
        )

    def test_locale_catalog_has_fallback_and_supported_labels(self):
        self.assertEqual(normalize_locale("es-MX"), "es")
        self.assertEqual(normalize_locale("unknown"), DEFAULT_LOCALE)
        self.assertIn("English", locale_choices())
        self.assertIn("Español", locale_choices())
        self.assertEqual(tr("process_files", "es"), "Procesar archivos")
        self.assertEqual(tr("missing_shell_key", "es"), "missing_shell_key")

    def test_locale_selection_is_process_local_and_reversible(self):
        self.assertEqual(set_locale("Español"), "es")
        self.assertEqual(tr("input_files"), "Archivos de entrada")
        self.assertEqual(set_locale("English"), "en")
        self.assertEqual(tr("input_files"), "Input Files")

    def test_appearance_labels_round_trip_through_localized_catalog(self):
        self.assertEqual(normalize_appearance_mode("Light"), "light")
        self.assertEqual(normalize_appearance_mode("Claro"), "light")
        self.assertEqual(appearance_label("system", "es"), "Sistema")
        self.assertEqual(appearance_choices("en"), ["Dark", "Light", "System"])

    def test_accessible_description_is_stable_for_focus_contracts(self):
        class _Widget:
            def cget(self, key):
                return {"text": "Run", "state": "normal"}.get(key, "")

            def winfo_children(self):
                return []

        widget = _Widget()
        set_accessible_name(widget, "Run", "Keyboard control 1")
        self.assertEqual(accessible_description(widget), "Keyboard control 1")
        self.assertEqual(focus_contract_snapshot(widget, "#00ffff"), [])


if __name__ == "__main__":
    unittest.main()
