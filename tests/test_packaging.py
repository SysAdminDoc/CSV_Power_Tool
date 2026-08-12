import importlib.util
import json
import unittest
from pathlib import Path


BUILD_PATH = Path(__file__).resolve().parents[1] / "packaging" / "build.py"
SPEC = importlib.util.spec_from_file_location("csv_power_tool_packaging_build", BUILD_PATH)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class PackagingContractTests(unittest.TestCase):
    def test_all_release_version_surfaces_are_consistent(self):
        versions = build.validate_version_consistency()

        self.assertEqual(len(set(versions.values())), 1)
        self.assertEqual(versions["launcher"], "3.2.0")

    def test_dependency_manifest_also_emits_cyclonedx_sbom(self):
        manifest_path = build.write_dependency_manifest()

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sbom = json.loads(build.SBOM.read_text(encoding="utf-8"))
        self.assertEqual(manifest["format"], "csv-power-tool-dependency-manifest")
        self.assertTrue(manifest["components"])
        self.assertIn("lock_matches_environment", manifest)
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["specVersion"], "1.5")
        self.assertEqual(sbom["metadata"]["component"]["version"], "3.2.0")
        self.assertTrue(all(component["purl"].startswith("pkg:pypi/") for component in sbom["components"]))


if __name__ == "__main__":
    unittest.main()
