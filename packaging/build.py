#!/usr/bin/env python3
"""Build the unsigned CSV Power Tool Windows artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXE = DIST / "CSV_Power_Tool.exe"
MSI = DIST / "CSV_Power_Tool.msi"
DEPENDENCY_MANIFEST = DIST / "CSV_Power_Tool.dependencies.json"
SPEC = ROOT / "CSV_Power_Tool.spec"


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_dependency_manifest() -> Path:
    source = (ROOT / "CSV_Consolidator.py").read_text(encoding="utf-8")
    version_match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', source, re.MULTILINE)
    app_version = version_match.group(1) if version_match else "unknown"
    components = []
    lock_path = ROOT / "requirements.lock"
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        try:
            metadata = importlib.metadata.metadata(name)
            license_name = metadata.get("License-Expression") or metadata.get("License") or "UNKNOWN"
        except importlib.metadata.PackageNotFoundError:
            license_name = "UNKNOWN"
        components.append({
            "name": name,
            "version": version,
            "license": license_name,
            "scope": "runtime",
        })

    manifest = {
        "format": "csv-power-tool-dependency-manifest",
        "version": 1,
        "tool": "CSV Power Tool",
        "tool_version": app_version,
        "components": components,
    }
    DIST.mkdir(parents=True, exist_ok=True)
    DEPENDENCY_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"dependency manifest: {DEPENDENCY_MANIFEST}")
    print(f"sha256: {sha256(DEPENDENCY_MANIFEST)}")
    return DEPENDENCY_MANIFEST


def build_exe() -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    if EXE.exists():
        EXE.unlink()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(SPEC),
    ]
    run(command)
    if not EXE.exists():
        raise RuntimeError(f"PyInstaller did not produce {EXE}")
    run([str(EXE), "--version"])
    print(f"unsigned exe: {EXE} ({EXE.stat().st_size:,} bytes)")
    print(f"sha256: {sha256(EXE)}")
    write_dependency_manifest()
    return EXE


def build_msi() -> Path:
    if not EXE.exists():
        build_exe()
    run([
        "wix",
        "build",
        "-arch",
        "x64",
        str(ROOT / "packaging" / "CSV_Power_Tool.wxs"),
        "-d",
        f"PublishDir={DIST}",
        "-o",
        str(MSI),
    ])
    if not MSI.exists():
        raise RuntimeError(f"WiX did not produce {MSI}")
    print(f"unsigned msi: {MSI} ({MSI.stat().st_size:,} bytes)")
    print(f"sha256: {sha256(MSI)}")
    return MSI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msi", action="store_true", help="Build the WiX MSI after the executable")
    parser.add_argument("--rebuild", action="store_true", help="Explicitly rebuild the executable (the default)")
    parser.add_argument("--reuse", action="store_true", help="Reuse an existing executable instead of rebuilding")
    args = parser.parse_args()
    if not args.reuse or not EXE.exists():
        build_exe()
    else:
        print(f"reusing existing unsigned exe: {EXE}")
        if not DEPENDENCY_MANIFEST.exists():
            write_dependency_manifest()
    if args.msi:
        build_msi()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
