"""Exercise the optional pytest evidence adapter in fresh processes."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest evidence plugin is optional")
class PytestEvidenceTests(unittest.TestCase):
    def test_inventory_cannot_be_replaced_with_same_selected_test_as_execution(self):
        with tempfile.TemporaryDirectory(prefix="s6pytest-") as directory:
            root = Path(directory)
            tools = root / "tools" / "anti_trick_gate_spec"
            shutil.copytree(Path(__file__).resolve().parents[1], tools, ignore=shutil.ignore_patterns("tests", "__pycache__"))
            (root / "pytest.ini").write_text("[pytest]\nmarkers =\n    live: touches a provider\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_coverage.py").write_text(
                "import pytest\ndef test_one(): pass\ndef test_two(): pass\n"
                "@pytest.mark.live\ndef test_paid(): raise AssertionError('must never run')\n"
                "@pytest.mark.skip(reason='fixture skip')\ndef test_skip(): pass\n",
                encoding="utf-8",
            )
            sha = "4" * 40
            platform = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
            policy = {"schema": "s6-test-policy/1", "platforms": {platform: {
                "roots": ["tests"], "markexpr": "not live",
            }}}
            (root / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
            names = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
            env = {key: value for key, value in os.environ.items() if key.upper() in names}
            env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            def run(*args):
                return subprocess.run(
                    [sys.executable, "-B", "-m", *args], cwd=root, env=env,
                    stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

            def evidence(filename, *args):
                result = run("pytest", "-q", "-s", "-p", "no:cacheprovider",
                             "-p", "tools.anti_trick_gate_spec.pytest_probe", "-m", "not live",
                             "--s6-evidence", filename, "--s6-revision", sha, *args)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

            (root / "alternate.ini").write_text("[pytest]\npython_functions = test_one\n"
                                                 "markers = live: fixture provider mark\n", encoding="utf-8")
            evidence("all.json", "--collect-only", "-o", "console_output_style=count", "tests")
            evidence("subset-collect.json", "--collect-only", "tests/test_coverage.py::test_one")
            evidence("subset-run.json", "tests/test_coverage.py::test_one")
            evidence("keyword.json", "--collect-only", "-k", "test_one", "tests")
            evidence("override-collect.json", "--collect-only", "-o", "python_functions=test_one", "tests")
            evidence("override-run.json", "-o", "python_functions=test_one", "tests")
            evidence("alternate-collect.json", "--collect-only", "-c", "alternate.ini", "tests")
            evidence("full-run.json", "tests")

            check = ("tools.anti_trick_gate_spec", "coverage", "--revision", sha,
                     "--platform", platform, "--policy", "policy.json")
            reported = run(*check, "all.json", "full-run.json")
            self.assertEqual(reported.returncode, 1, reported.stderr)
            issues = json.loads(reported.stdout)["issues"]
            self.assertEqual(len(issues), 1, issues)
            self.assertTrue(issues[0]["signal"].endswith("::test_skip"))

            for inventory, executed in (
                ("override-collect.json", "override-run.json"),
                ("alternate-collect.json", "override-run.json"),
                ("subset-collect.json", "subset-run.json"),
                ("keyword.json", "subset-run.json"),
            ):
                with self.subTest(inventory=inventory):
                    response = run(*check, inventory, executed)
                    self.assertEqual(response.returncode, 2, response.stdout or response.stderr)
                    self.assertIn("inventory selection", response.stderr)
