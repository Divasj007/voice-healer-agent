from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProjectQualityTests(unittest.TestCase):
    def test_required_files_exist(self) -> None:
        required = [
            "LICENSE",
            "README.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
            "requirements.txt",
            "sample_bug.py",
            "hooks/pre-commit",
            "hooks/install.sh",
            "hooks/install.ps1",
            "skills/voice-healer/SKILL.md",
            "skills/voice-healer/scripts/heal_code.py",
            "skills/voice-healer/scripts/listen_command.py",
            "skills/voice-healer/scripts/main.py",
            "skills/voice-healer/scripts/ui_server.py",
            "skills/voice-healer/scripts/audit.py",
            "skills/voice-healer/scripts/lifecycle.py",
            "skills/voice-healer/scripts/approval.py",
            "skills/voice-healer/scripts/doctor.py",
            "skills/voice-healer/scripts/safety_guard.py",
            "skills/voice-healer/scripts/local_endpoint.py",
            "tests/test_audit.py",
            "tests/test_approval.py",
            "tests/test_heal_code.py",
            "tests/test_main.py",
            "tests/test_ui_server.py",
            ".github/workflows/ci.yml",
            "ui/index.html",
            "ui/app.js",
            "ui/styles.css",
        ]
        for relative in required:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_skill_frontmatter_contains_required_fields(self) -> None:
        source = (ROOT / "skills/voice-healer/SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(source.startswith("---\n"))
        frontmatter = source.split("---\n", 2)[1]
        fields = {}
        for line in frontmatter.splitlines():
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
            if match:
                fields[match.group(1)] = match.group(2).strip()
        self.assertEqual(fields["name"], "voice-healer")
        self.assertIn("heal", fields["description"].lower())
        self.assertTrue(fields["compatibility"])
        self.assertTrue(1 <= len(fields["name"]) <= 64)
        self.assertLessEqual(len(fields["description"]), 1024)
        self.assertLessEqual(len(fields["compatibility"]), 500)

    def test_required_dependencies_are_pinned(self) -> None:
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        expected = {
            "faster-whisper==1.2.1",
            "pyttsx3==2.99",
            "SpeechRecognition==3.17.0",
            "PyAudio==0.2.14",
            "av==18.1.0",
        }
        self.assertTrue(expected.issubset(set(requirements)))

    def test_sample_bug_is_intentionally_broken(self) -> None:
        source = (ROOT / "fixtures/sample_bug.py").read_text(encoding="utf-8")
        self.assertIn(' + version', source)

    def test_ui_exposes_history_view(self) -> None:
        html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
        app = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        self.assertIn('data-view="history"', html)
        self.assertIn('/api/history', app)

    def test_history_groups_events_into_repair_items(self) -> None:
        app = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        audit = (ROOT / "skills/voice-healer/scripts/audit.py").read_text(encoding="utf-8")
        self.assertIn("function historyRepairCard", app)
        self.assertIn("Recorded repairs", app)
        self.assertIn("Audit events", app)
        self.assertIn("View audit trail", app)
        self.assertIn("build_repair_history", audit)

    def test_ui_labels_overview_trace_as_illustrative(self) -> None:
        html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("illustrative / self-healing-flow", html)
        self.assertIn("● DEMO", html)

    def test_ui_exposes_safe_patch_review(self) -> None:
        html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
        app = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        self.assertIn("Approve &amp; apply", html)
        self.assertIn("Reject", html)
        self.assertIn("/api/propose-heal", app)
        self.assertIn("/api/approve-heal", app)
        self.assertIn("/api/reject-heal", app)
        self.assertIn("proposal_diff", app)


    def test_ui_fails_closed_when_safety_report_is_missing(self) -> None:
        app = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        self.assertIn("Regenerate this repair proposal before approving it.", app)
        self.assertIn("Boolean(state.safetyReport?.blocked)", app)

    def test_ui_exposes_doctor_view(self) -> None:
        html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
        app = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        self.assertIn('data-view="doctor"', html)
        self.assertIn('/api/doctor', app)

    def test_ui_exposes_repair_lifecycle(self) -> None:
        html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
        app = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        self.assertIn("Repair lifecycle", html)
        self.assertIn("/api/lifecycle", app)

    def test_ui_exposes_safety_guard(self) -> None:
        html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
        app = (ROOT / "ui/app.js").read_text(encoding="utf-8")
        guard = (ROOT / "skills/voice-healer/scripts/safety_guard.py").read_text(encoding="utf-8")
        self.assertIn("Safety Guard", html)
        self.assertIn("safetyGuard", app)
        self.assertIn("safety_report", app)
        self.assertIn("shell-execution", guard)

    def test_ci_declares_no_secret_permissions(self) -> None:
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", ci)


if __name__ == "__main__":
    unittest.main()