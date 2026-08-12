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
SBOM = DIST / "CSV_Power_Tool.cdx.json"
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


def _project_versions() -> dict[str, str]:
    launcher = (ROOT / "CSV_Consolidator.py").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    wix = (ROOT / "packaging" / "CSV_Power_Tool.wxs").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    patterns = {
        "launcher": (launcher, r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE),
        "pyproject": (pyproject, r'^version\s*=\s*["\']([^"\']+)["\']', re.MULTILINE),
        "wix": (wix, r'Version="([^"]+)"'),
        "readme": (readme, r'Version-v([^"-]+)-'),
        "changelog": (changelog, r'^## \[v([^\]]+)\]', re.MULTILINE),
    }
    versions = {}
    for name, (text, pattern, *flags) in patterns.items():
        match = re.search(pattern, text, flags[0] if flags else 0)
        if not match:
            raise RuntimeError(f"Unable to find project version in {name}")
        versions[name] = match.group(1)
    return versions


def validate_version_consistency(expected: str | None = None) -> dict[str, str]:
    versions = _project_versions()
    expected_version = expected or next(iter(versions.values()))
    mismatches = {name: version for name, version in versions.items() if version != expected_version}
    if mismatches:
        details = ", ".join(f"{name}={version}" for name, version in versions.items())
        raise RuntimeError(f"Project version mismatch (expected {expected_version}): {details}")
    return versions


def _locked_components() -> list[dict]:
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
            installed_version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            license_name = "UNKNOWN"
            installed_version = None
        normalized_name = re.sub(r"[-_.]+", "-", name).lower()
        component = {
            "name": name,
            "version": version,
            "license": license_name,
            "installed_version": installed_version,
            "scope": "runtime",
            "purl": f"pkg:pypi/{normalized_name}@{version}",
        }
        components.append(component)
    return components


def _write_cyclonedx_sbom(components: list[dict], app_version: str) -> Path:
    sbom_components = []
    for component in components:
        item = {
            "type": "library",
            "bom-ref": component["purl"],
            "name": component["name"],
            "version": component["version"],
            "scope": "required",
            "purl": component["purl"],
        }
        if component["license"] != "UNKNOWN":
            item["licenses"] = [{"license": {"name": component["license"]}}]
        sbom_components.append(item)
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "CSV Power Tool",
                "version": app_version,
            }
        },
        "components": sbom_components,
    }
    SBOM.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"CycloneDX SBOM: {SBOM}")
    print(f"sha256: {sha256(SBOM)}")
    return SBOM


def write_dependency_manifest() -> Path:
    app_version = validate_version_consistency()["launcher"]
    components = _locked_components()

    manifest = {
        "format": "csv-power-tool-dependency-manifest",
        "version": 1,
        "tool": "CSV Power Tool",
        "tool_version": app_version,
        "components": components,
        "lock_matches_environment": all(
            component["installed_version"] == component["version"]
            for component in components
        ),
    }
    DIST.mkdir(parents=True, exist_ok=True)
    DEPENDENCY_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_cyclonedx_sbom(components, app_version)
    print(f"dependency manifest: {DEPENDENCY_MANIFEST}")
    print(f"sha256: {sha256(DEPENDENCY_MANIFEST)}")
    return DEPENDENCY_MANIFEST


def build_exe() -> Path:
    validate_version_consistency()
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
