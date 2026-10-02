from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "voice-healer" / "scripts"
for name in ("audit", "safety_guard", "heal_code", "approval"):
    module_path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)

safety_guard = sys.modules["safety_guard"]
heal_code = sys.modules["heal_code"]
approval = sys.modules["approval"]


class SafetyGuardIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.target = self.root / "broken.py"
        self.source = "value = 1\nprint('x' + value)\n"
        self.target.write_text(self.source, encoding="utf-8")
        self.original_heal_root = heal_code.os.environ.get("VOICE_HEALER_REPO_ROOT")
        self.original_approval_root = approval.REPO_ROOT
        self.original_pending = approval.PENDING_FILE
        heal_code.os.environ["VOICE_HEALER_REPO_ROOT"] = str(self.root)
        approval.REPO_ROOT = self.root
        approval.PENDING_FILE = self.root / ".voice-healer" / "pending_repair.json"
        approval.clear_pending()

    def tearDown(self) -> None:
        approval.clear_pending()
        approval.REPO_ROOT = self.original_approval_root
        approval.PENDING_FILE = self.original_pending
        if self.original_heal_root is None:
            heal_code.os.environ.pop("VOICE_HEALER_REPO_ROOT", None)
        else:
            heal_code.os.environ["VOICE_HEALER_REPO_ROOT"] = self.original_heal_root
        self.tmp.cleanup()

    def test_automatic_heal_blocks_high_risk_candidate_and_restores_original(self) -> None:
        unsafe = "import subprocess\nsubprocess.run(['echo', 'x'])\n"
        with patch.object(heal_code, "call_ollama", return_value=unsafe):
            result = heal_code.heal(self.target, max_attempts=1, timeout=5, ollama_timeout=1)
        self.assertEqual(result, 1)
        self.assertEqual(self.target.read_text(encoding="utf-8"), self.source)

    def test_ui_proposal_payload_contains_safety_report(self) -> None:
        safe = "value = 1\nprint('x' + str(value))\n"
        with patch.object(approval, "call_ollama", return_value=safe):
            result = approval.propose_repair(self.target, ollama_timeout=1)
        self.assertEqual(result["status"], "proposed")
        self.assertIn("safety_report", result)
        self.assertFalse(result["safety_report"]["blocked"])

    def test_blocked_candidate_is_reported_with_findings(self) -> None:
        unsafe = "import subprocess\nsubprocess.run(['echo', 'x'])\n"
        report = safety_guard.inspect_candidate(self.source, unsafe, self.root)
        self.assertTrue(report["blocked"])
        self.assertGreaterEqual(report["high_count"], 1)
        self.assertTrue(any(item["rule"] == "shell-execution" for item in report["findings"]))


if __name__ == "__main__":
    unittest.main()
