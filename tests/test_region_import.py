"""Regression tests for import-time configuration side effects."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RegionImportTests(unittest.TestCase):
    def test_import_does_not_require_config_in_current_directory(self):
        source_root = Path(__file__).resolve().parents[1] / "src"
        environment = os.environ.copy()
        existing_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(source_root), existing_pythonpath) if part
        )

        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", "import mesh_alerts.region"],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"import failed outside repository directory:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
