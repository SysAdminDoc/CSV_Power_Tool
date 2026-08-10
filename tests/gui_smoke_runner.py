"""Explicit opt-in GUI smoke runner for the invisible desktop harness.

This file is not discovered by unittest.  It is launched only through the
private-desktop visual-isolation harness and writes its evidence to `%TEMP%`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


smoke_path_value = os.environ.get("CSV_POWER_TOOL_GUI_SMOKE_PATH")
smoke_path = Path(smoke_path_value) if smoke_path_value else None
if smoke_path is None:
    smoke_path = Path(tempfile.gettempdir()) / "csv-power-tool-gui-smoke-source.json"
os.environ["CSV_POWER_TOOL_GUI_SMOKE"] = "1"
os.environ["CSV_POWER_TOOL_GUI_SMOKE_PATH"] = str(smoke_path)
os.environ.setdefault("CSV_POWER_TOOL_LOCALE", "es")
os.environ.setdefault("CSV_POWER_TOOL_SCALE", "125%")

smoke_path.write_text('{"status":"runner-start"}\n', encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from CSV_Consolidator import CSVPowerToolApp  # noqa: E402


CSVPowerToolApp().run()
