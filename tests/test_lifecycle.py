from __future__ import annotations

import unittest
import importlib.util
import sys

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "voice-healer" / "scripts" / "lifecycle.py"
spec = importlib.util.spec_from_file_location("lifecycle", MODULE_PATH)
lifecycle = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["lifecycle"] = lifecycle
spec.loader.exec_module(lifecycle)


class LifecycleTests(unittest.TestCase):
    def test_empty_lifecycle_is_idle(self) -> None:
        result = lifecycle.build_lifecycle([], "sample_bug.py")
        self.assertEqual(result["current_stage"], "idle")
        self.assertEqual(result["status"], "idle")

    def test_proposal_enters_approval_stage(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_proposed", "status": "repair_proposed", "stage": "awaiting_approval", "target": "sample_bug.py"}
        ], "sample_bug.py")
        self.assertEqual(result["current_stage"], "awaiting_approval")
        stages = {item["stage"]: item["status"] for item in result["steps"]}
        self.assertEqual(stages["analyzing"], "complete")
        self.assertEqual(stages["awaiting_approval"], "pending")

    def test_approved_repair_is_verified(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_proposed", "status": "repair_proposed", "stage": "awaiting_approval", "target": "sample_bug.py"},
            {"event": "repair_approved", "status": "approved_repair", "stage": "verified", "target": "sample_bug.py"},
        ], "sample_bug.py")
        self.assertEqual(result["current_stage"], "verified")
        self.assertEqual(result["status"], "approved_repair")
        stages = {item["stage"]: item["status"] for item in result["steps"]}
        self.assertEqual(stages["verified"], "complete")

    def test_rejected_repair_is_terminal(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_proposed", "status": "repair_proposed", "stage": "awaiting_approval", "target": "sample_bug.py"},
            {"event": "repair_rejected", "status": "rejected_by_user", "stage": "rejected", "target": "sample_bug.py"},
        ], "sample_bug.py")
        self.assertEqual(result["current_stage"], "rejected")
        self.assertEqual(result["status"], "rejected_by_user")

    def test_failed_repair_reports_restored(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_completed", "status": "failed_and_restored", "stage": "restored", "mode": "automatic", "target": "sample_bug.py"},
        ], "sample_bug.py")
        self.assertEqual(result["current_stage"], "restored")
        self.assertEqual(result["status"], "failed_and_restored")

    def test_already_healthy_is_verified(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_completed", "status": "already_healthy", "stage": "verified", "mode": "automatic", "target": "sample_bug.py"},
        ], "sample_bug.py")
        self.assertEqual(result["current_stage"], "verified")

    def test_target_filter_excludes_other_files(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_completed", "status": "repaired", "stage": "verified", "target": "other.py"},
        ], "sample_bug.py")
        self.assertEqual(result["current_stage"], "idle")


    def test_same_second_newest_event_wins_when_audit_is_newest_first(self) -> None:
        timestamp = "2026-10-01T18:00:00+00:00"
        result = lifecycle.build_lifecycle([
            {"event": "repair_approved", "status": "approved_repair", "stage": "verified", "target": "sample_bug.py", "timestamp": timestamp},
            {"event": "repair_proposed", "status": "repair_proposed", "stage": "awaiting_approval", "target": "sample_bug.py", "timestamp": timestamp},
        ], "sample_bug.py")
        self.assertEqual(result["current_stage"], "verified")

    def test_verified_marks_unselected_terminal_branches_skipped(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_proposed", "status": "repair_proposed", "stage": "awaiting_approval", "target": "sample_bug.py"},
            {"event": "repair_approved", "status": "approved_repair", "stage": "verified", "target": "sample_bug.py"},
        ], "sample_bug.py")
        stages = {item["stage"]: item["status"] for item in result["steps"]}
        self.assertEqual(stages["rejected"], "skipped")
        self.assertEqual(stages["restored"], "skipped")

    def test_rejected_marks_apply_verify_and_restore_skipped(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_proposed", "status": "repair_proposed", "stage": "awaiting_approval", "target": "sample_bug.py"},
            {"event": "repair_rejected", "status": "rejected_by_user", "stage": "rejected", "target": "sample_bug.py"},
        ], "sample_bug.py")
        stages = {item["stage"]: item["status"] for item in result["steps"]}
        self.assertEqual(result["current_stage"], "rejected")
        self.assertEqual(stages["applying"], "skipped")
        self.assertEqual(stages["verifying"], "skipped")
        self.assertEqual(stages["restored"], "skipped")

    def test_restored_marks_rejected_skipped(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_proposed", "status": "repair_proposed", "stage": "awaiting_approval", "target": "sample_bug.py"},
            {"event": "repair_approved_failed", "status": "approved_repair_failed", "stage": "restored", "target": "sample_bug.py"},
        ], "sample_bug.py")
        stages = {item["stage"]: item["status"] for item in result["steps"]}
        self.assertEqual(result["current_stage"], "restored")
        self.assertEqual(stages["rejected"], "skipped")
        self.assertEqual(stages["restored"], "complete")

    def test_automatic_terminal_state_skips_approval(self) -> None:
        result = lifecycle.build_lifecycle([
            {"event": "repair_completed", "status": "repaired", "stage": "verified", "mode": "automatic", "target": "sample_bug.py"},
        ], "sample_bug.py")
        stages = {item["stage"]: item["status"] for item in result["steps"]}
        self.assertEqual(stages["awaiting_approval"], "skipped")
        self.assertEqual(stages["applying"], "complete")


if __name__ == "__main__":
    unittest.main()
