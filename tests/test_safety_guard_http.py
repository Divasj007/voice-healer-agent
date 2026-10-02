from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "voice-healer" / "scripts"
for name in ("audit", "safety_guard", "heal_code", "approval", "doctor", "lifecycle", "ui_server"):
    module_path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)

ui_server = sys.modules["ui_server"]
approval = sys.modules["approval"]


class SafetyGuardHTTPTests(unittest.TestCase):
    def test_blocked_proposal_is_visible_and_approval_endpoint_refuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample_bug.py"
            target.write_text("value = 1\nprint('x' + value)\n", encoding="utf-8")
            original_root = ui_server.REPO_ROOT
            original_approval_root = approval.REPO_ROOT
            original_audit_dir = approval.AUDIT_DIR
            original_pending = approval.PENDING_FILE
            ui_server.REPO_ROOT = root
            approval.REPO_ROOT = root
            approval.AUDIT_DIR = root / ".voice-healer"
            approval.PENDING_FILE = approval.AUDIT_DIR / "pending_repair.json"
            approval.clear_pending()
            server = ThreadingHTTPServer(("127.0.0.1", 0), ui_server.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                unsafe = "import subprocess\nsubprocess.run(['echo', 'unsafe'])\n"
                with patch.object(approval, "call_ollama", return_value=unsafe):
                    status, proposal = self.request(server.server_port, "POST", "/api/propose-heal", {"file": "sample_bug.py"})
                self.assertEqual(status, 200)
                self.assertTrue(proposal["ok"])
                self.assertTrue(proposal["proposal"]["safety_report"]["blocked"])
                proposal_id = proposal["proposal"]["proposal_id"]

                status, blocked = self.request(server.server_port, "POST", "/api/approve-heal", {"file": "sample_bug.py", "proposal_id": proposal_id})
                self.assertEqual(status, 403)
                self.assertFalse(blocked["ok"])
                self.assertTrue(blocked["safety_report"]["blocked"])
                self.assertEqual(target.read_text(encoding="utf-8"), "value = 1\nprint('x' + value)\n")
            finally:
                approval.clear_pending()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                ui_server.REPO_ROOT = original_root
                approval.REPO_ROOT = original_approval_root
                approval.AUDIT_DIR = original_audit_dir
                approval.PENDING_FILE = original_pending

    @staticmethod
    def request(port: int, method: str, path: str, payload: dict[str, str]) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = json.dumps(payload)
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        conn.close()
        return response.status, data


if __name__ == "__main__":
    unittest.main()
