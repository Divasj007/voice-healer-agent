from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "voice-healer" / "scripts" / "safety_guard.py"
spec = importlib.util.spec_from_file_location("voice_healer_safety_guard", MODULE_PATH)
safety_guard = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(safety_guard)


class SafetyGuardTests(unittest.TestCase):
    def test_safe_string_conversion_is_safe(self) -> None:
        source = "value = 42\nprint('x' + value)\n"
        candidate = "value = 42\nprint('x' + str(value))\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertFalse(report["blocked"])
        self.assertEqual(report["overall"], "safe")
        self.assertEqual(report["finding_count"], 0)

    def test_new_subprocess_import_is_blocked(self) -> None:
        source = "print('x')\n"
        candidate = "import subprocess\nsubprocess.run(['echo', 'x'])\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertTrue(report["blocked"])
        self.assertEqual(report["overall"], "blocked")
        rules = {item["rule"] for item in report["findings"]}
        self.assertIn("shell-execution", rules)

    def test_new_network_import_is_blocked(self) -> None:
        source = "print('x')\n"
        candidate = "import requests\nrequests.get('https://example.com')\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertTrue(report["blocked"])
        self.assertIn("network-access", {item["rule"] for item in report["findings"]})

    def test_dynamic_code_is_blocked(self) -> None:
        source = "value = 1\nprint(value)\n"
        candidate = "value = 1\nprint(eval('value'))\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertTrue(report["blocked"])
        self.assertIn("dynamic-code", {item["rule"] for item in report["findings"]})

    def test_destructive_delete_is_blocked(self) -> None:
        source = "path = 'demo.txt'\nprint(path)\n"
        candidate = "import os\npath = 'demo.txt'\nos.remove(path)\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertTrue(report["blocked"])
        self.assertIn("destructive-delete", {item["rule"] for item in report["findings"]})

    def test_secret_environment_access_is_blocked(self) -> None:
        source = "print('ready')\n"
        candidate = "import os\nprint(os.getenv('API_KEY'))\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertTrue(report["blocked"])
        self.assertIn("credential-access", {item["rule"] for item in report["findings"]})

    def test_traversal_write_is_blocked(self) -> None:
        source = "print('ready')\n"
        candidate = "open('../outside.txt', 'w').write('x')\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertTrue(report["blocked"])
        self.assertIn("path-escape-write", {item["rule"] for item in report["findings"]})

    def test_regular_filesystem_write_is_warning(self) -> None:
        source = "print('ready')\n"
        candidate = "open('output.txt', 'w').write('x')\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertFalse(report["blocked"])
        self.assertEqual(report["overall"], "warning")
        self.assertIn("filesystem-write", {item["rule"] for item in report["findings"]})

    def test_legacy_risk_is_not_counted_as_introduced_again(self) -> None:
        source = "import subprocess\nsubprocess.run(['echo', 'x'])\n"
        candidate = "import subprocess\nsubprocess.run(['echo', 'x'])\nprint('same risk, new output')\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertFalse(report["blocked"])
        self.assertEqual(report["finding_count"], 0)

    def test_new_native_code_import_is_blocked(self) -> None:
        source = "print('x')\n"
        candidate = "import ctypes\nprint(ctypes.CDLL('libx.so'))\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertTrue(report["blocked"])
        self.assertIn("native-code", {item["rule"] for item in report["findings"]})

    def test_cli_returns_zero_for_safe_candidate(self) -> None:
        source = "value = 42\nprint(value)\n"
        candidate = "value = 42\nprint(str(value))\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.py"
            candidate_path = root / "candidate.py"
            source_path.write_text(source, encoding="utf-8")
            candidate_path.write_text(candidate, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(source_path), str(candidate_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0)

    def test_cli_returns_two_for_blocked_candidate(self) -> None:
        source = "print('x')\n"
        candidate = "import subprocess\nsubprocess.run(['echo', 'x'])\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.py"
            candidate_path = root / "candidate.py"
            source_path.write_text(source, encoding="utf-8")
            candidate_path.write_text(candidate, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(source_path), str(candidate_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn('"overall": "blocked"', result.stdout)

    def test_pickle_deserialization_is_blocked(self) -> None:
        source = "data = b''\nprint(data)\n"
        candidate = "import pickle\nprint(pickle.loads(data))\n"
        report = safety_guard.inspect_candidate(source, candidate)
        self.assertTrue(report["blocked"])
        self.assertIn("pickle-execution", {item["rule"] for item in report["findings"]})


if __name__ == "__main__":
    unittest.main()
