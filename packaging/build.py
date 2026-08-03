#!/usr/bin/env python3
"""Build the unsigned CSV Power Tool Windows artifacts."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXE = DIST / "CSV_Power_Tool.exe"
MSI = DIST / "CSV_Power_Tool.msi"


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_exe() -> Path:
    run([
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "CSV_Power_Tool",
        "CSV_Consolidator.py",
    ])
    if not EXE.exists():
        raise RuntimeError(f"PyInstaller did not produce {EXE}")
    run([str(EXE), "--version"])
    print(f"unsigned exe: {EXE} ({EXE.stat().st_size:,} bytes)")
    print(f"sha256: {sha256(EXE)}")
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
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the executable before packaging")
    args = parser.parse_args()
    if args.rebuild or not EXE.exists():
        build_exe()
    else:
        print(f"reusing existing unsigned exe: {EXE}")
    if args.msi:
        build_msi()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
