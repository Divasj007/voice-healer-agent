from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "voice-healer" / "scripts"
for name in ("audit", "heal_code", "approval"):
    module_path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)

approval = sys.modules["approval"]
heal_code = sys.modules["heal_code"]


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original_root = approval.REPO_ROOT
        self.original_pending = approval.PENDING_FILE
        approval.REPO_ROOT = self.root
        approval.PENDING_FILE = self.root / ".voice-healer" / "pending_repair.json"
        approval.clear_pending()

    def tearDown(self) -> None:
        approval.clear_pending()
        approval.REPO_ROOT = self.original_root
        approval.PENDING_FILE = self.original_pending
        self.tmp.cleanup()

    def test_propose_does_not_modify_target(self) -> None:
        target = self.root / "broken.py"
        source = "value = 1\nprint('x' + value)\n"
        target.write_text(source, encoding="utf-8")
        with patch.object(approval, "call_ollama", return_value="value = 1\nprint('x' + str(value))\n"):
            result = approval.propose_repair(target, ollama_timeout=1)
        self.assertEqual(result["status"], "proposed")
        self.assertEqual(target.read_text(encoding="utf-8"), source)
        self.assertTrue(approval.get_pending())
        self.assertIn("-print(\'x\' + value)", result["proposal_diff"])
        self.assertIn("+print(\'x\' + str(value))", result["proposal_diff"])
        history = approval.AUDIT_DIR / "history.jsonl"
        if history.exists():
            logged = history.read_text(encoding="utf-8")
            self.assertNotIn("str(value)", logged)

    def test_approve_applies_only_unchanged_target(self) -> None:
        target = self.root / "broken.py"
        target.write_text("value = 1\nprint('x' + value)\n", encoding="utf-8")
        with patch.object(approval, "call_ollama", return_value="value = 1\nprint('x' + str(value))\n"):
            proposal = approval.propose_repair(target, ollama_timeout=1)
        result = approval.apply_pending(target, proposal["proposal_id"])
        self.assertEqual(result["status"], "approved_repair")
        self.assertIsInstance(result["duration_ms"], int)
        self.assertGreaterEqual(result["duration_ms"], 0)
        self.assertIn("str(value)", target.read_text(encoding="utf-8"))
        self.assertIsNone(approval.get_pending())

    def test_approve_rejects_stale_target(self) -> None:
        target = self.root / "broken.py"
        target.write_text("value = 1\nprint('x' + value)\n", encoding="utf-8")
        with patch.object(approval, "call_ollama", return_value="value = 1\nprint('x' + str(value))\n"):
            proposal = approval.propose_repair(target, ollama_timeout=1)
        target.write_text("print('changed')\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            approval.apply_pending(target, proposal["proposal_id"])

    def test_reject_keeps_target_unchanged(self) -> None:
        target = self.root / "broken.py"
        source = "value = 1\nprint('x' + value)\n"
        target.write_text(source, encoding="utf-8")
        with patch.object(approval, "call_ollama", return_value="value = 1\nprint('x' + str(value))\n"):
            proposal = approval.propose_repair(target, ollama_timeout=1)
        result = approval.reject_pending(target, proposal["proposal_id"])
        self.assertEqual(result["status"], "rejected_by_user")
        self.assertIsInstance(result["duration_ms"], int)
        self.assertGreaterEqual(result["duration_ms"], 0)
        self.assertEqual(target.read_text(encoding="utf-8"), source)
        self.assertIsNone(approval.get_pending())

    def test_failed_approved_candidate_restores_backup(self) -> None:
        target = self.root / "broken.py"
        source = "print('original')\n"
        target.write_text(source, encoding="utf-8")
        pending = {
            "proposal_id": "test",
            "target": "broken.py",
            "base_sha256": approval.sha256_text(source),
            "candidate_sha256": approval.sha256_text("print('still broken')\n"),
            "candidate_source": "raise RuntimeError('boom')\n",
            "model": heal_code.MODEL_NAME,
        }
        approval._atomic_json_write(pending)
        with patch.object(approval, "run_target", return_value=heal_code.RunResult(1, "", "boom")):
            result = approval.apply_pending(target, "test")
        self.assertEqual(result["status"], "approved_repair_failed")
        self.assertEqual(target.read_text(encoding="utf-8"), source)



    def test_pending_from_previous_session_is_ignored(self) -> None:
        original = approval.os.environ.get("VOICE_HEALER_SESSION_ID")
        try:
            approval.os.environ["VOICE_HEALER_SESSION_ID"] = "current-session"
            approval.PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
            approval.PENDING_FILE.write_text(json.dumps({
                "proposal_id": "old", "target": "sample_bug.py",
                "session_id": "old-session",
            }), encoding="utf-8")
            self.assertIsNone(approval.get_pending())
        finally:
            approval.clear_pending()
            if original is None:
                approval.os.environ.pop("VOICE_HEALER_SESSION_ID", None)
            else:
                approval.os.environ["VOICE_HEALER_SESSION_ID"] = original

if __name__ == "__main__":
    unittest.main()
