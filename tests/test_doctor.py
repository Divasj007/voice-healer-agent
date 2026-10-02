from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "voice-healer" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import doctor  # noqa: E402


class DoctorTests(unittest.TestCase):
    def test_python_check_passes_on_supported_runtime(self) -> None:
        result = doctor.check_python()
        self.assertEqual(result.status, "ok")

    def test_unsupported_python_minor_fails(self) -> None:
        with patch.object(doctor.sys, "version_info", (3, 10, 0)):
            result = doctor.check_python()
        self.assertEqual(result.status, "fail")

    def test_required_files_check_passes(self) -> None:
        result = doctor.check_files()
        self.assertEqual(result.status, "ok")

    def test_skill_check_requires_matching_name(self) -> None:
        result = doctor.check_skill()
        self.assertEqual(result.status, "ok")

    def test_demo_fixture_check_passes(self) -> None:
        result = doctor.check_demo()
        self.assertEqual(result.status, "ok")

    def test_ollama_check_detects_required_model(self) -> None:
        payload = {"models": [{"name": doctor.MODEL_NAME}]}
        response = io.BytesIO(json.dumps(payload).encode("utf-8"))
        response.__enter__ = lambda: response
        response.__exit__ = lambda *args: None
        with patch.object(doctor.urllib.request, "urlopen", return_value=response):
            result = doctor.check_ollama()
        self.assertEqual(result.status, "ok")

    def test_ollama_check_rejects_non_local_endpoint(self) -> None:
        original = doctor.OLLAMA_TAGS_URL
        try:
            doctor.OLLAMA_TAGS_URL = "http://example.com/api/tags"
            result = doctor.check_ollama()
        finally:
            doctor.OLLAMA_TAGS_URL = original
        self.assertEqual(result.status, "fail")

    def test_ollama_check_reports_failure_without_service(self) -> None:
        with patch.object(doctor.urllib.request, "urlopen", side_effect=OSError("connection refused")):
            result = doctor.check_ollama()
        self.assertEqual(result.status, "fail")

    def test_run_doctor_is_read_only_shape(self) -> None:
        report = doctor.run_doctor()
        self.assertIn(report["overall"], {"ok", "warn", "fail"})
        self.assertEqual(report["model"], doctor.MODEL_NAME)
        self.assertTrue(report["checks"])
        self.assertTrue(all(set(item) == {"name", "status", "summary", "detail"} for item in report["checks"]))


if __name__ == "__main__":
    unittest.main()
