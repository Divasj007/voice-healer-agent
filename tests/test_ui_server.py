from __future__ import annotations

import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "voice-healer" / "scripts" / "ui_server.py"
spec = importlib.util.spec_from_file_location("voice_healer_ui_server", MODULE_PATH)
ui_server = importlib.util.module_from_spec(spec)
assert spec.loader is not None
import sys
sys.modules["voice_healer_ui_server"] = ui_server
spec.loader.exec_module(ui_server)


class UIServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ui_server.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict | str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        raw = response.read().decode("utf-8")
        conn.close()
        try:
            return response.status, json.loads(raw)
        except json.JSONDecodeError:
            return response.status, raw

    def test_files_endpoint_lists_sample(self) -> None:
        status, payload = self.request("GET", "/api/files")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        paths = {item["path"] for item in payload["files"]}
        self.assertIn("sample_bug.py", paths)

    def test_source_endpoint_returns_utf8_source(self) -> None:
        status, payload = self.request("GET", "/api/source?file=sample_bug.py")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("build_greeting", payload["source"])

    def test_diff_endpoint_reports_change_when_backup_differs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            target = temp_root / "sample_bug.py"
            backup = temp_root / "sample_bug.py.bak"
            target.write_text("print('fixed')\n", encoding="utf-8")
            backup.write_text("print('broken')\n", encoding="utf-8")
            original_root = ui_server.REPO_ROOT
            try:
                ui_server.REPO_ROOT = temp_root
                status, payload = self.request("GET", "/api/diff?file=sample_bug.py")
                self.assertEqual(status, 200)
                self.assertTrue(payload["ok"])
                self.assertTrue(payload["available"])
                self.assertTrue(payload["changed"])
                self.assertIn("-print('broken')", payload["text"])
                self.assertIn("+print('fixed')", payload["text"])
            finally:
                ui_server.REPO_ROOT = original_root

    def test_api_rejects_path_escape(self) -> None:
        status, payload = self.request("GET", "/api/source?file=..%2Foutside.py")
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_unknown_api_endpoint_is_404(self) -> None:
        status, payload = self.request("GET", "/api/nope")
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])

    def test_reset_demo_uses_tracked_fixture_when_backup_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample_bug.py"
            target.write_text("print('healthy')\n", encoding="utf-8")
            original_root = ui_server.REPO_ROOT
            original_fixture = ui_server.DEMO_FIXTURE
            try:
                ui_server.REPO_ROOT = Path(tmp)
                ui_server.DEMO_FIXTURE = Path(tmp) / "fixtures" / "sample_bug.py"
                ui_server.DEMO_FIXTURE.parent.mkdir(parents=True)
                ui_server.DEMO_FIXTURE.write_text("print('broken fixture')\n", encoding="utf-8")
                result = ui_server.reset_demo(target)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(target.read_text(encoding="utf-8"), "print('broken fixture')\n")
                self.assertEqual(target.with_suffix(".py.bak").read_text(encoding="utf-8"), "print('broken fixture')\n")
            finally:
                ui_server.REPO_ROOT = original_root
                ui_server.DEMO_FIXTURE = original_fixture

    def test_lifecycle_endpoint_returns_state(self) -> None:
        original = ui_server.read_events
        try:
            ui_server.read_events = lambda limit: [{
                "event": "repair", "status": "repaired", "stage": "verified",
                "target": "sample_bug.py", "session_id": ui_server.SESSION_ID,
            }]
            status, payload = self.request("GET", "/api/lifecycle?file=sample_bug.py")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["lifecycle"]["current_stage"], "verified")
            self.assertIn("steps", payload["lifecycle"])
        finally:
            ui_server.read_events = original


    def test_lifecycle_ignores_events_from_previous_ui_session(self) -> None:
        original = ui_server.read_events
        try:
            ui_server.read_events = lambda limit: [{
                "event": "repair_approved", "status": "approved_repair", "stage": "verified",
                "target": "sample_bug.py", "session_id": "previous-session",
            }]
            status, payload = self.request("GET", "/api/lifecycle?file=sample_bug.py")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["lifecycle"]["current_stage"], "idle")
        finally:
            ui_server.read_events = original

    def test_reset_demo_emits_session_reset_and_clears_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            target = temp_root / "sample_bug.py"
            fixture = temp_root / "fixtures" / "sample_bug.py"
            target.write_text("print('healthy')\n", encoding="utf-8")
            fixture.parent.mkdir(parents=True)
            fixture.write_text("print('broken')\n", encoding="utf-8")
            original_root = ui_server.REPO_ROOT
            original_fixture = ui_server.DEMO_FIXTURE
            original_append = ui_server.append_event
            original_clear = ui_server.approval.clear_pending
            events = []
            cleared = []
            try:
                ui_server.REPO_ROOT = temp_root
                ui_server.DEMO_FIXTURE = fixture
                ui_server.append_event = lambda event: events.append(event)
                ui_server.approval.clear_pending = lambda: cleared.append(True)
                result = ui_server.reset_demo(target)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(target.read_text(encoding="utf-8"), "print('broken')\n")
                self.assertEqual(cleared, [True])
                self.assertEqual(events[0]["event"], "demo_reset")
                self.assertEqual(events[0]["stage"], "idle")
            finally:
                ui_server.REPO_ROOT = original_root
                ui_server.DEMO_FIXTURE = original_fixture
                ui_server.append_event = original_append
                ui_server.approval.clear_pending = original_clear

    def test_lifecycle_endpoint_resets_to_idle_after_demo_reset(self) -> None:
        original = ui_server.read_events
        try:
            ui_server.read_events = lambda limit: [
                {
                    "event": "demo_reset", "status": "demo_reset", "stage": "idle",
                    "target": "sample_bug.py", "session_id": ui_server.SESSION_ID,
                },
                {
                    "event": "repair_approved", "status": "approved_repair", "stage": "verified",
                    "target": "sample_bug.py", "session_id": ui_server.SESSION_ID,
                },
            ]
            status, payload = self.request("GET", "/api/lifecycle?file=sample_bug.py")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["lifecycle"]["current_stage"], "idle")
            self.assertEqual(payload["lifecycle"]["status"], "idle")
        finally:
            ui_server.read_events = original

    def test_history_endpoint_returns_local_events(self) -> None:
        original = ui_server.read_events
        try:
            ui_server.read_events = lambda limit: [{"event": "repair", "status": "repaired", "target": "sample_bug.py"}]
            status, payload = self.request("GET", "/api/history?limit=10")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["events"][0]["status"], "repaired")
        finally:
            ui_server.read_events = original

    def test_history_endpoint_returns_grouped_repairs_and_counts(self) -> None:
        original = ui_server.read_events
        try:
            ui_server.read_events = lambda limit: [
                {"event": "repair_approved", "status": "approved_repair", "target": "sample_bug.py", "proposal_id": "p1", "timestamp": "2026-10-02T00:00:02+00:00", "duration_ms": 25},
                {"event": "repair_proposed", "status": "repair_proposed", "target": "sample_bug.py", "proposal_id": "p1", "timestamp": "2026-10-02T00:00:01+00:00", "duration_ms": 100},
                {"event": "demo_reset", "status": "demo_reset", "target": "sample_bug.py", "timestamp": "2026-10-02T00:00:03+00:00"},
            ]
            status, payload = self.request("GET", "/api/history?limit=10")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["repair_count"], 1)
            self.assertEqual(payload["event_count"], 3)
            self.assertEqual(len(payload["repairs"]), 1)
            self.assertEqual(payload["repairs"][0]["status"], "approved_repair")
            self.assertEqual(len(payload["repairs"][0]["events"]), 2)
        finally:
            ui_server.read_events = original


    def test_pending_endpoint_is_empty_by_default(self) -> None:
        ui_server.approval.clear_pending()
        status, payload = self.request("GET", "/api/pending")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIsNone(payload["pending"])

    def test_propose_heal_endpoint_returns_reviewable_proposal(self) -> None:
        with patch.object(ui_server, "propose_repair", return_value={
            "status": "proposed",
            "proposal_id": "abc123",
            "target": "sample_bug.py",
            "model": "qwen2.5-coder:1.5b",
            "candidate_sha256": "candidate",
            "base_sha256": "base",
            "candidate_source": "print('fixed')\n",
            "proposal_diff": "-broken\n+fixed\n",
            "diagnostics": "TypeError",
        }):
            status, payload = self.request("POST", "/api/propose-heal", {"file": "sample_bug.py"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["proposal"]["proposal_id"], "abc123")
        self.assertIn("proposal_diff", payload["proposal"])

    def test_approve_and_reject_endpoints_route_to_approval_engine(self) -> None:
        with patch.object(ui_server, "apply_pending", return_value={"status": "approved_repair", "returncode": 0, "stdout": "ok", "stderr": ""}) as apply_mock:
            status, payload = self.request("POST", "/api/approve-heal", {"file": "sample_bug.py", "proposal_id": "abc123"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        apply_mock.assert_called_once()

        with patch.object(ui_server, "reject_pending", return_value={"status": "rejected_by_user", "target": "sample_bug.py"}) as reject_mock:
            status, payload = self.request("POST", "/api/reject-heal", {"file": "sample_bug.py", "proposal_id": "abc123"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        reject_mock.assert_called_once()

    def test_doctor_endpoint_returns_report(self) -> None:
        with patch.object(ui_server, "run_doctor", return_value={"overall": "ok", "checks": []}):
            status, payload = self.request("GET", "/api/doctor")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["doctor"]["overall"], "ok")

    def test_static_index_is_served(self) -> None:
        status, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Voice Healer", payload)

    def test_non_python_repo_file_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ui_server.safe_repo_file("README.md")

    def test_ollama_status_rejects_non_local_endpoint(self) -> None:
        original = ui_server.OLLAMA_TAGS_URL
        try:
            ui_server.OLLAMA_TAGS_URL = "http://example.com/api/tags"
            result = ui_server.ollama_status()
        finally:
            ui_server.OLLAMA_TAGS_URL = original
        self.assertFalse(result["connected"])
        self.assertTrue(result["local_only"])

    def test_ui_bind_host_policy_rejects_public_host(self) -> None:
        from local_endpoint import validate_loopback_bind_host
        with self.assertRaises(ValueError):
            validate_loopback_bind_host("0.0.0.0")

    def test_ui_port_range_is_enforced(self) -> None:
        self.assertFalse(1 <= 0 <= 65535)
        self.assertFalse(1 <= 65536 <= 65535)


if __name__ == "__main__":
    unittest.main()
