#!/usr/bin/env python3
"""Local preflight diagnostics for Voice Healer.

The doctor never changes source files. It reports whether the local developer
agent has the Python runtime, Ollama model, optional voice stack, UI files,
Agent Skill metadata, and Git hook needed for the supported workflows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from local_endpoint import validate_local_http_url
REPO_ROOT = Path(os.environ.get("VOICE_HEALER_REPO_ROOT", str(SCRIPT_DIR.parents[2]))).resolve()
MODEL_NAME = "qwen2.5-coder:1.5b"
OLLAMA_TAGS_URL = os.environ.get("OLLAMA_TAGS_URL", "http://localhost:11434/api/tags")


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    summary: str
    detail: str


def _check(name: str, status: str, summary: str, detail: str) -> Check:
    return Check(name=name, status=status, summary=summary, detail=detail)


def check_python() -> Check:
    major, minor = sys.version_info[:2]
    version = f"{major}.{minor}.{sys.version_info[2]}"
    if (major, minor) >= (3, 11):
        return _check("python", "ok", version, "Python 3.11 or newer is supported.")
    return _check("python", "fail", version, "Python 3.11 or newer is required.")


def check_files() -> Check:
    required = [
        "skills/voice-healer/SKILL.md",
        "skills/voice-healer/scripts/heal_code.py",
        "skills/voice-healer/scripts/main.py",
        "skills/voice-healer/scripts/listen_command.py",
        "skills/voice-healer/scripts/ui_server.py",
        "skills/voice-healer/scripts/approval.py",
        "skills/voice-healer/scripts/safety_guard.py",
        "skills/voice-healer/scripts/local_endpoint.py",
        "skills/voice-healer/scripts/audit.py",
        "skills/voice-healer/scripts/lifecycle.py",
        "ui/index.html",
        "ui/app.js",
        "ui/styles.css",
        "sample_bug.py",
        "fixtures/sample_bug.py",
        "requirements.txt",
    ]
    missing = [item for item in required if not (REPO_ROOT / item).is_file()]
    if missing:
        return _check("project files", "fail", f"{len(missing)} missing", ", ".join(missing))
    return _check("project files", "ok", f"{len(required)} checked", "Core repository files are present.")


def check_skill() -> Check:
    path = REPO_ROOT / "skills" / "voice-healer" / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _check("agent skill", "fail", "unreadable", str(exc))
    if not text.startswith("---\n"):
        return _check("agent skill", "fail", "frontmatter missing", "SKILL.md must start with YAML frontmatter.")
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return _check("agent skill", "fail", "frontmatter incomplete", "Closing frontmatter delimiter is missing.")
    fields: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"').strip("'")
    missing = [key for key in ("name", "description", "compatibility") if not fields.get(key)]
    if missing:
        return _check("agent skill", "fail", "required metadata missing", ", ".join(missing))
    if fields.get("name") != "voice-healer":
        return _check("agent skill", "fail", "name mismatch", fields.get("name", ""))
    return _check("agent skill", "ok", "valid metadata", "name, description, and compatibility are present.")


def check_ollama() -> Check:
    try:
        validate_local_http_url(OLLAMA_TAGS_URL, label="OLLAMA_TAGS_URL")
    except ValueError as exc:
        return _check("ollama", "fail", "non-local endpoint refused", str(exc))
    request = urllib.request.Request(OLLAMA_TAGS_URL, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return _check("ollama", "fail", "offline", f"{OLLAMA_TAGS_URL}: {exc}")
    models = payload.get("models", []) if isinstance(payload, dict) else []
    names = {str(item.get("name")) for item in models if isinstance(item, dict)}
    if MODEL_NAME in names:
        return _check("ollama", "ok", "connected + model ready", f"{MODEL_NAME} is available.")
    return _check("ollama", "fail", "connected, model missing", f"Pull {MODEL_NAME} before healing.")


def check_voice_stack() -> Check:
    modules = {
        "faster-whisper": "faster_whisper",
        "pyttsx3": "pyttsx3",
        "SpeechRecognition": "speech_recognition",
        "PyAudio": "pyaudio",
        "av": "av",
    }
    missing = []
    for label, module in modules.items():
        try:
            __import__(module)
        except Exception:
            missing.append(label)
    if missing:
        return _check("voice stack", "warn", f"{len(missing)} optional package(s) missing", ", ".join(missing))
    return _check("voice stack", "ok", "installed", "Voice dependencies are importable; microphone hardware is not tested by the doctor.")


def check_git() -> Check:
    try:
        result = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5, check=False)
    except OSError as exc:
        return _check("git", "warn", "git unavailable", str(exc))
    if result.returncode != 0 or result.stdout.strip() != "true":
        return _check("git", "warn", "not a repository", "Initialize Git when you are ready to use the pre-commit workflow.")
    installed = (REPO_ROOT / ".git" / "hooks" / "pre-commit").is_file()
    tracked = (REPO_ROOT / "hooks" / "pre-commit").is_file()
    if installed and tracked:
        return _check("git", "ok", "repository + hook ready", "Both the tracked hook and local Git hook are present.")
    if tracked:
        return _check("git", "warn", "repository ready; hook not installed", "Run hooks/install.ps1 on Windows or hooks/install.sh on Unix-like systems.")
    return _check("git", "warn", "repository ready; tracked hook missing", "The local Git integration files are incomplete.")


def check_demo() -> Check:
    fixture = REPO_ROOT / "fixtures" / "sample_bug.py"
    if not fixture.is_file():
        return _check("demo fixture", "fail", "missing", "fixtures/sample_bug.py is required for Reset Demo.")
    try:
        source = fixture.read_text(encoding="utf-8")
    except OSError as exc:
        return _check("demo fixture", "fail", "unreadable", str(exc))
    if "+ version" not in source:
        return _check("demo fixture", "fail", "unexpected source", "The tracked demo fixture should retain its deliberate TypeError.")
    return _check("demo fixture", "ok", "ready", "The reproducible broken sample is present.")


def run_doctor() -> dict[str, object]:
    checks = [
        check_python(),
        check_files(),
        check_skill(),
        check_ollama(),
        check_voice_stack(),
        check_git(),
        check_demo(),
    ]
    overall = "fail" if any(item.status == "fail" for item in checks) else "warn" if any(item.status == "warn" for item in checks) else "ok"
    return {
        "project": "voice-healer-agent",
        "model": MODEL_NAME,
        "repository": str(REPO_ROOT),
        "overall": overall,
        "checks": [asdict(item) for item in checks],
    }


def print_report(report: dict[str, object]) -> int:
    print("[Doctor] Voice Healer local preflight")
    print(f"[Doctor] Overall: {str(report['overall']).upper()}")
    for item in report["checks"]:
        status = str(item["status"]).upper()
        print(f"[Doctor] {status:<4} {item['name']}: {item['summary']} — {item['detail']}")
    print("[Doctor] No source files were modified.")
    return 0 if report["overall"] != "fail" else 1


def main() -> int:
    report = run_doctor()
    return print_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
