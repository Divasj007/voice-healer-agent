#!/usr/bin/env python3
"""Local-only web UI server for Voice Healer.

Uses only Python's standard library. The browser talks to this server for
repository status, source inspection, execution, healing, and demo reset.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import uuid
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SESSION_ID = uuid.uuid4().hex
os.environ["VOICE_HEALER_SESSION_ID"] = SESSION_ID

from audit import append_event, build_repair_history, read_events
import approval
import lifecycle
from doctor import run_doctor
from local_endpoint import validate_local_http_url, validate_loopback_bind_host
from approval import SafetyGuardError, apply_pending, get_pending, propose_repair, reject_pending

SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = Path(os.environ.get("VOICE_HEALER_REPO_ROOT", str(SKILL_DIR.parent.parent))).resolve()
UI_ROOT = REPO_ROOT / "ui"
HEAL_SCRIPT = REPO_ROOT / "skills" / "voice-healer" / "scripts" / "heal_code.py"
DEMO_FIXTURE = REPO_ROOT / "fixtures" / "sample_bug.py"
MODEL_NAME = "qwen2.5-coder:1.5b"
OLLAMA_TAGS_URL = os.environ.get("OLLAMA_TAGS_URL", "http://localhost:11434/api/tags")
MAX_BODY_BYTES = 64 * 1024
DEFAULT_TARGET_TIMEOUT = 30


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


def json_response(payload: dict[str, Any], status: int = HTTPStatus.OK) -> tuple[int, bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, body


def safe_repo_file(value: str, *, allow_suffixes: tuple[str, ...] = (".py",)) -> Path:
    raw = value.strip().strip('"').strip("'")
    if not raw:
        raise ValueError("A file path is required.")
    candidate = (REPO_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        candidate.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise ValueError("File must stay inside the project repository.") from exc
    if candidate.is_dir():
        raise ValueError("Directories are not valid targets.")
    if candidate.suffix.lower() not in allow_suffixes:
        raise ValueError("Only Python source files are supported here.")
    return candidate


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def build_unified_diff(target: Path) -> dict[str, Any]:
    current = read_text(target)
    backup = target.with_suffix(target.suffix + ".bak")
    if not backup.exists():
        return {"available": False, "text": "", "changed": False}
    original = read_text(backup)
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=f"{repo_relative(backup)} (backup)",
            tofile=f"{repo_relative(target)} (current)",
        )
    )
    return {"available": True, "text": diff, "changed": original != current}


def run_command(command: list[str], timeout: int = DEFAULT_TARGET_TIMEOUT) -> CommandResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            round((time.perf_counter() - started) * 1000),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(124, stdout, f"{stderr}\nExecution timed out after {timeout} seconds.".strip(), round((time.perf_counter() - started) * 1000))
    except OSError as exc:
        return CommandResult(126, "", str(exc), round((time.perf_counter() - started) * 1000))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Unable to read file: {exc}") from exc


def list_python_files() -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in {".venv", "__pycache__", ".git"} for part in path.parts):
            continue
        if path.is_file():
            files.append({"path": repo_relative(path), "name": path.name})
    files.sort(key=lambda item: (item["path"] != "sample_bug.py", item["path"]))
    return files[:100]


def ollama_status() -> dict[str, Any]:
    try:
        validate_local_http_url(OLLAMA_TAGS_URL, label="OLLAMA_TAGS_URL")
    except ValueError as exc:
        return {"connected": False, "model": MODEL_NAME, "error": str(exc), "local_only": True}
    request = urllib.request.Request(OLLAMA_TAGS_URL, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"connected": False, "model": MODEL_NAME, "error": str(exc)}
    models = payload.get("models", []) if isinstance(payload, dict) else []
    names = {str(item.get("name")) for item in models if isinstance(item, dict)}
    return {
        "connected": True,
        "model": MODEL_NAME,
        "model_available": MODEL_NAME in names,
        "models": sorted(name for name in names if name and name != "None"),
    }


def dependency_status() -> dict[str, bool]:
    modules = {
        "faster-whisper": "faster_whisper",
        "pyttsx3": "pyttsx3",
        "SpeechRecognition": "speech_recognition",
        "PyAudio": "pyaudio",
        "av": "av",
    }
    result: dict[str, bool] = {}
    for label, module in modules.items():
        try:
            __import__(module)
            result[label] = True
        except Exception:
            result[label] = False
    return result


def sample_health() -> dict[str, Any]:
    sample = REPO_ROOT / "sample_bug.py"
    backup = REPO_ROOT / "sample_bug.py.bak"
    if not sample.exists():
        return {"exists": False, "healthy": False, "backup_exists": backup.exists()}
    result = run_command([sys.executable, "-u", str(sample)], timeout=10)
    return {
        "exists": True,
        "healthy": result.returncode == 0,
        "backup_exists": backup.exists(),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def build_status() -> dict[str, Any]:
    sample = sample_health()
    return {
        "project": "voice-healer-agent",
        "python": sys.version.split()[0],
        "model": MODEL_NAME,
        "ollama": ollama_status(),
        "dependencies": dependency_status(),
        "voice": {
            "implemented": True,
            "hardware_optional": True,
            "fallback": "CLI text input",
        },
        "sample": sample,
        "skill": str((SKILL_DIR / "SKILL.md").relative_to(REPO_ROOT)).replace("\\", "/"),
        "repository": str(REPO_ROOT),
    }


def reset_demo(target: Path) -> CommandResult:
    backup = target.with_suffix(target.suffix + ".bak")
    source = backup
    if not source.exists() and repo_relative(target) == "sample_bug.py":
        source = DEMO_FIXTURE
    if not source.exists():
        return CommandResult(2, "", f"Demo source not found: {repo_relative(source)}", 0)
    started = time.perf_counter()
    try:
        target.write_bytes(source.read_bytes())
        if source == DEMO_FIXTURE:
            backup.write_bytes(source.read_bytes())
        approval.clear_pending()
        duration_ms = round((time.perf_counter() - started) * 1000)
        append_event(
            {
                "event": "demo_reset",
                "status": "demo_reset",
                "stage": "idle",
                "mode": "interactive",
                "target": repo_relative(target),
                "model": MODEL_NAME,
                "attempts": 0,
                "returncode": 0,
                "duration_ms": duration_ms,
                "error": "Demo lifecycle reset by user.",
                "original_sha256": "",
                "final_sha256": "",
                "backup": backup.name,
                "session_id": SESSION_ID,
            }
        )
    except OSError as exc:
        return CommandResult(1, "", f"Could not restore demo: {exc}", round((time.perf_counter() - started) * 1000))
    return CommandResult(0, f"[Demo] Restored {repo_relative(target)} from {repo_relative(source)}", "", duration_ms)


class Handler(BaseHTTPRequestHandler):
    server_version = "VoiceHealerUI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write(f"[UI Server] {fmt % args}\n")
        sys.stdout.flush()

    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        code, body = json_response(payload, status)
        self.send_bytes(code, "application/json; charset=utf-8", body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/doctor":
            self.send_json({"ok": True, "doctor": run_doctor()})
            return
        if parsed.path == "/api/status":
            self.send_json({"ok": True, "status": build_status()})
            return
        if parsed.path == "/api/files":
            self.send_json({"ok": True, "files": list_python_files()})
            return
        if parsed.path == "/api/history":
            params = parse_qs(parsed.query)
            raw_limit = params.get("limit", ["50"])[0]
            try:
                limit = max(1, min(int(raw_limit), 100))
            except ValueError:
                limit = 50
            raw_events = read_events(min(250, max(50, limit * 4)))
            repairs = build_repair_history(raw_events, limit)
            self.send_json({
                "ok": True,
                "events": raw_events,
                "repairs": repairs,
                "repair_count": len(repairs),
                "event_count": len(raw_events),
            })
            return
        if parsed.path == "/api/lifecycle":
            params = parse_qs(parsed.query)
            filename = params.get("file", ["sample_bug.py"])[0]
            try:
                target = safe_repo_file(filename)
                target_name = repo_relative(target)
                events = [
                    event for event in read_events(100)
                    if str(event.get("session_id", "")) == SESSION_ID
                    and str(event.get("target", "")) == target_name
                ]
                current_cycle: list[dict[str, Any]] = []
                for event in events:
                    if str(event.get("event", "")) == "demo_reset":
                        break
                    current_cycle.append(event)
                lifecycle_data = lifecycle.build_lifecycle(current_cycle, target_name)
                self.send_json({"ok": True, "lifecycle": lifecycle_data})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/skill":
            skill_path = REPO_ROOT / "skills" / "voice-healer" / "SKILL.md"
            try:
                self.send_json({"ok": True, "source": read_text(skill_path)})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if parsed.path == "/api/source":
            params = parse_qs(parsed.query)
            filename = params.get("file", ["sample_bug.py"])[0]
            try:
                target = safe_repo_file(filename)
                source = read_text(target)
                backup = target.with_suffix(target.suffix + ".bak")
                backup_source = read_text(backup) if backup.exists() else ""
                run_data = None
                healthy = None
                if target.name == "sample_bug.py":
                    result = run_command([sys.executable, "-u", str(target)], timeout=10)
                    run_data = asdict(result)
                    healthy = result.returncode == 0
                self.send_json({
                    "ok": True,
                    "file": repo_relative(target),
                    "source": source,
                    "backup_source": backup_source,
                    "backup_exists": backup.exists(),
                    "healthy": healthy,
                    "run": run_data,
                })
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/pending":
            pending = get_pending()
            self.send_json({"ok": True, "pending": pending})
            return
        if parsed.path == "/api/diff":
            params = parse_qs(parsed.query)
            filename = params.get("file", ["sample_bug.py"])[0]
            try:
                target = safe_repo_file(filename)
                result = build_unified_diff(target)
                self.send_json({"ok": True, "file": repo_relative(target), **result})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path.startswith("/api/"):
            self.send_json({"ok": False, "error": "Unknown API endpoint."}, HTTPStatus.NOT_FOUND)
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                self.send_json({"ok": False, "error": "Invalid Content-Length."}, HTTPStatus.BAD_REQUEST)
                return
            if length > MAX_BODY_BYTES:
                self.send_json({"ok": False, "error": "Request body too large."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object.")
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"ok": False, "error": f"Invalid JSON request: {exc}"}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path not in {"/api/run", "/api/heal", "/api/reset", "/api/propose-heal", "/api/approve-heal", "/api/reject-heal"}:
            self.send_json({"ok": False, "error": "Unknown API endpoint."}, HTTPStatus.NOT_FOUND)
            return

        filename = str(payload.get("file", "sample_bug.py"))
        try:
            target = safe_repo_file(filename)
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/run":
            result = run_command([sys.executable, "-u", str(target)])
            self.send_json({"ok": result.returncode == 0, "file": repo_relative(target), "result": asdict(result)})
            return

        if parsed.path == "/api/propose-heal":
            try:
                result = propose_repair(target)
            except (OSError, UnicodeDecodeError, RuntimeError, ValueError, SyntaxError) as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"ok": result["status"] in {"proposed", "pending", "already_healthy"}, "file": repo_relative(target), "proposal": result})
            return

        proposal_id = str(payload.get("proposal_id", ""))
        if parsed.path == "/api/approve-heal":
            try:
                result = apply_pending(target, proposal_id)
            except SafetyGuardError as exc:
                self.send_json(
                    {"ok": False, "error": str(exc), "safety_report": exc.report},
                    HTTPStatus.FORBIDDEN,
                )
                return
            except (OSError, UnicodeDecodeError, RuntimeError, ValueError, SyntaxError) as exc:
                status = HTTPStatus.CONFLICT if "changed after" in str(exc).lower() else HTTPStatus.BAD_REQUEST
                self.send_json({"ok": False, "error": str(exc)}, status)
                return
            self.send_json({"ok": result["status"] == "approved_repair", "file": repo_relative(target), "result": result})
            return

        if parsed.path == "/api/reject-heal":
            try:
                result = reject_pending(target, proposal_id)
            except (OSError, UnicodeDecodeError, RuntimeError, ValueError) as exc:
                status = HTTPStatus.CONFLICT if "changed after" in str(exc).lower() else HTTPStatus.BAD_REQUEST
                self.send_json({"ok": False, "error": str(exc)}, status)
                return
            self.send_json({"ok": True, "file": repo_relative(target), "result": result})
            return

        if parsed.path == "/api/reset":
            if repo_relative(target) != "sample_bug.py":
                self.send_json({"ok": False, "error": "The demo reset is restricted to sample_bug.py."}, HTTPStatus.BAD_REQUEST)
                return
            result = reset_demo(target)
            self.send_json({"ok": result.returncode == 0, "file": repo_relative(target), "result": asdict(result)})
            return

        command = [sys.executable, str(HEAL_SCRIPT), str(target)]
        result = run_command(command, timeout=180)
        backup = target.with_suffix(target.suffix + ".bak")
        source = read_text(target) if target.exists() else ""
        self.send_json({
            "ok": result.returncode == 0,
            "file": repo_relative(target),
            "result": asdict(result),
            "source": source,
            "backup_exists": backup.exists(),
            "healthy": result.returncode == 0 if target.exists() else False,
        })

    def serve_static(self, request_path: str) -> None:
        clean = request_path or "/"
        if clean == "/":
            relative = Path("index.html")
        else:
            relative = Path(clean.lstrip("/"))
        candidate = (UI_ROOT / relative).resolve()
        try:
            candidate.relative_to(UI_ROOT)
        except ValueError:
            self.send_json({"ok": False, "error": "Invalid resource path."}, HTTPStatus.BAD_REQUEST)
            return
        if not candidate.exists() or not candidate.is_file():
            self.send_json({"ok": False, "error": "Resource not found."}, HTTPStatus.NOT_FOUND)
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
        }
        self.send_bytes(HTTPStatus.OK, content_types.get(candidate.suffix.lower(), "application/octet-stream"), candidate.read_bytes())


def main() -> int:
    global REPO_ROOT, UI_ROOT, HEAL_SCRIPT
    parser = argparse.ArgumentParser(description="Serve the local Voice Healer web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: 8000).")
    parser.add_argument("--open", action="store_true", help="Open the UI in the default browser.")
    parser.add_argument("--repo-root", type=Path, help="Override repository root; useful for local testing.")
    args = parser.parse_args()

    try:
        validate_loopback_bind_host(args.host)
    except ValueError as exc:
        parser.error(str(exc))
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    if not UI_ROOT.exists():
        print(f"[UI Server] UI directory is missing: {UI_ROOT}", file=sys.stderr)
        return 1

    if args.repo_root is not None:
        REPO_ROOT = args.repo_root.resolve()
        approval.configure_repo_root(REPO_ROOT)
        UI_ROOT = REPO_ROOT / "ui"
        HEAL_SCRIPT = REPO_ROOT / "skills" / "voice-healer" / "scripts" / "heal_code.py"
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[UI Server] Voice Healer UI: http://{args.host}:{args.port}")
    print(f"[UI Server] Repository: {REPO_ROOT}")
    print("[UI Server] Local-only server. Press Ctrl+C to stop.")
    if args.open:
        import webbrowser

        webbrowser.open(f"http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[UI Server] Stopping.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
