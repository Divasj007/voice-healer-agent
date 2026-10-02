from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills" / "voice-healer" / "scripts" / "heal_code.py"
spec = importlib.util.spec_from_file_location("voice_healer_heal_code", MODULE_PATH)
heal_code = importlib.util.module_from_spec(spec)
assert spec.loader is not None
import sys
sys.modules["voice_healer_heal_code"] = heal_code
spec.loader.exec_module(heal_code)


class _OllamaHandler(BaseHTTPRequestHandler):
    response_text = "print('ok')"
    last_payload: dict = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.__class__.last_payload = json.loads(body.decode("utf-8"))
        payload = json.dumps({"response": self.response_text}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


class HealCodeTests(unittest.TestCase):
    def test_audit_event_is_best_effort(self) -> None:
        with patch.object(heal_code, "append_event") as mocked:
            heal_code.audit_repair(
                path=Path("sample.py"),
                status="already_healthy",
                started=time.perf_counter(),
                attempts=0,
                returncode=0,
                original_source="print(1)\n",
                final_source="print(1)\n",
            )
        mocked.assert_called_once()
        payload = mocked.call_args.args[0]
        self.assertEqual(payload["status"], "already_healthy")
        self.assertEqual(len(payload["original_sha256"]), 64)

    def test_strip_markdown_fences(self) -> None:
        source = "```python\nprint('hello')\n```"
        self.assertEqual(heal_code.strip_markdown_fences(source), "print('hello')")

    def test_validate_python_source_rejects_invalid_code(self) -> None:
        with self.assertRaises(SyntaxError):
            heal_code.validate_python_source("def broken(:\n")

    def test_call_ollama_uses_expected_local_payload(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/generate"
            with patch.dict("os.environ", {"OLLAMA_API_URL": url}):
                result = heal_code.call_ollama("repair this", timeout=5)
            self.assertEqual(result, "print('ok')")
            self.assertEqual(_OllamaHandler.last_payload["model"], heal_code.MODEL_NAME)
            self.assertEqual(_OllamaHandler.last_payload["stream"], False)
            self.assertEqual(_OllamaHandler.last_payload["prompt"], "repair this")
        finally:
            server.shutdown()
            server.server_close()

    def test_heal_repairs_failed_script_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "broken.py"
            original = "value = 1\nprint('x' + value)\n"
            repaired = "value = 1\nprint('x' + str(value))\n"
            target.write_text(original, encoding="utf-8")

            with patch.object(heal_code, "call_ollama", return_value=repaired):
                code = heal_code.heal(target, max_attempts=1, timeout=5, ollama_timeout=5)

            self.assertEqual(code, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), repaired.rstrip("\n"))
            self.assertEqual(target.with_suffix(".py.bak").read_text(encoding="utf-8"), original)

    def test_heal_restores_original_when_repair_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "broken.py"
            original = "value = 1\nprint('x' + value)\n"
            target.write_text(original, encoding="utf-8")

            with patch.object(heal_code, "call_ollama", return_value="def broken(:\n"):
                code = heal_code.heal(target, max_attempts=1, timeout=5, ollama_timeout=5)

            self.assertEqual(code, 1)
            self.assertEqual(target.read_text(encoding="utf-8"), original)
            self.assertEqual(target.with_suffix(".py.bak").read_text(encoding="utf-8"), original)

    def test_heal_rejects_non_python_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.txt"
            target.write_text("hello", encoding="utf-8")
            self.assertEqual(heal_code.heal(target, 1, 5, 5), 2)

    def test_call_ollama_rejects_non_local_endpoint(self) -> None:
        with patch.dict(heal_code.os.environ, {"OLLAMA_API_URL": "http://example.com/api/generate"}, clear=False):
            with self.assertRaises(ValueError):
                heal_code.call_ollama("print(1)", timeout=1)


if __name__ == "__main__":
    unittest.main()
