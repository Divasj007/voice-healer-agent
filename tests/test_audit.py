from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
SCRIPTS = ROOT / "skills" / "voice-healer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import audit


class AuditTests(unittest.TestCase):
    def test_append_and_read_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = Path(tmp)
            history = audit_dir / "history.jsonl"
            with patch.object(audit, "AUDIT_DIR", audit_dir), patch.object(audit, "HISTORY_FILE", history):
                audit.append_event({"event": "repair", "status": "repaired", "target": "sample_bug.py", "attempts": 1})
                events = audit.read_events(10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["status"], "repaired")
            self.assertEqual(events[0]["target"], "sample_bug.py")
            self.assertIn("timestamp", events[0])

    def test_read_events_ignores_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            history = Path(tmp) / "history.jsonl"
            history.write_text('{"status":"repaired"}\nnot-json\n', encoding="utf-8")
            with patch.object(audit, "HISTORY_FILE", history):
                events = audit.read_events(10)
            self.assertEqual(events, [{"status": "repaired"}])



    def test_append_event_includes_session_id_when_configured(self) -> None:
        original = audit.os.environ.get("VOICE_HEALER_SESSION_ID")
        try:
            audit.os.environ["VOICE_HEALER_SESSION_ID"] = "session-test"
            audit.append_event({"event": "session_probe"})
            events = audit.read_events(1)
            self.assertEqual(events[0]["session_id"], "session-test")
        finally:
            if original is None:
                audit.os.environ.pop("VOICE_HEALER_SESSION_ID", None)
            else:
                audit.os.environ["VOICE_HEALER_SESSION_ID"] = original

if __name__ == "__main__":
    unittest.main()

class RepairHistoryTests(unittest.TestCase):
    def test_interactive_repair_events_are_grouped_into_one_repair(self) -> None:
        events = [
            {"event": "repair_approved", "status": "approved_repair", "target": "sample_bug.py", "proposal_id": "p1", "timestamp": "2026-10-02T00:00:02+00:00", "duration_ms": 20, "attempts": 1, "model": "qwen2.5-coder:1.5b"},
            {"event": "repair_proposed", "status": "repair_proposed", "target": "sample_bug.py", "proposal_id": "p1", "timestamp": "2026-10-02T00:00:01+00:00", "duration_ms": 100, "attempts": 1, "model": "qwen2.5-coder:1.5b"},
        ]
        repairs = audit.build_repair_history(events, 10)
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["status"], "approved_repair")
        self.assertEqual(repairs[0]["target"], "sample_bug.py")
        self.assertEqual(repairs[0]["duration_ms"], 120)
        self.assertEqual(len(repairs[0]["events"]), 2)

    def test_rejected_repair_is_one_history_item(self) -> None:
        events = [
            {"event": "repair_rejected", "status": "rejected_by_user", "target": "sample_bug.py", "proposal_id": "p2", "timestamp": "2026-10-02T00:00:03+00:00", "duration_ms": 1, "attempts": 1},
            {"event": "repair_proposed", "status": "repair_proposed", "target": "sample_bug.py", "proposal_id": "p2", "timestamp": "2026-10-02T00:00:02+00:00", "duration_ms": 80, "attempts": 1},
        ]
        repairs = audit.build_repair_history(events, 10)
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["status"], "rejected_by_user")
        self.assertEqual(len(repairs[0]["events"]), 2)

    def test_non_repair_events_are_excluded(self) -> None:
        events = [
            {"event": "demo_reset", "status": "demo_reset", "target": "sample_bug.py"},
            {"event": "run", "status": "healthy", "target": "sample_bug.py"},
            {"event": "session_probe", "status": "ok", "target": "sample_bug.py"},
        ]
        self.assertEqual(audit.build_repair_history(events, 10), [])

    def test_automatic_repairs_are_counted_once(self) -> None:
        events = [
            {"event": "repair", "status": "repaired", "target": "sample_bug.py", "timestamp": "2026-10-02T00:00:04+00:00", "duration_ms": 250, "attempts": 1},
            {"event": "repair", "status": "failed_and_restored", "target": "other.py", "timestamp": "2026-10-02T00:00:03+00:00", "duration_ms": 300, "attempts": 2},
        ]
        repairs = audit.build_repair_history(events, 10)
        self.assertEqual(len(repairs), 2)
        self.assertEqual(repairs[0]["target"], "sample_bug.py")
        self.assertEqual(repairs[0]["duration_ms"], 250)

    def test_pending_proposal_is_visible_as_one_repair(self) -> None:
        events = [
            {"event": "repair_proposed", "status": "repair_proposed", "target": "sample_bug.py", "proposal_id": "p3", "timestamp": "2026-10-02T00:00:05+00:00", "duration_ms": 120, "attempts": 1},
        ]
        repairs = audit.build_repair_history(events, 10)
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["status"], "repair_proposed")
