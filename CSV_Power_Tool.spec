# -*- mode: python ; coding: utf-8 -*-
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

datas = []
binaries = []
hiddenimports = ['zoneinfo', '_zoneinfo', 'uuid', '_duckdb']
for package in ('openpyxl', 'et_xmlfile', 'pyarrow', 'polars', 'duckdb', 'rapidfuzz', 'darkdetect', 'packaging'):
    datas += collect_data_files(package, include_py_files=True)
    datas += copy_metadata(package)
    binaries += collect_dynamic_libs(package)
datas += collect_data_files('_polars_runtime_32', include_py_files=True)
datas += copy_metadata('polars-runtime-32')
runtime_root = Path(importlib.util.find_spec('_polars_runtime_32').origin).parent
binaries.append((str(runtime_root / '_polars_runtime.pyd'), '_polars_runtime_32'))
duckdb_binary = Path(importlib.util.find_spec('_duckdb').origin)
binaries.append((str(duckdb_binary), '.'))


a = Analysis(
    ['CSV_Consolidator.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CSV_Power_Tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
