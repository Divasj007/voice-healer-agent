#!/usr/bin/env python3
"""Voice/CLI orchestrator for the voice-healer Agent Skill."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
REPO_ROOT = SKILL_DIR.parent.parent
HEAL_SCRIPT = SCRIPT_DIR / "heal_code.py"

PREFIX = "[Voice Agent]"
GIT_PREFIX = "[Git]"
DOCTOR_SCRIPT = SCRIPT_DIR / "doctor.py"


def safe_target(user_path: str) -> Path:
    raw = user_path.strip().strip("\"").strip("'")
    if not raw:
        raise ValueError("A Python file path is required.")
    candidate = (REPO_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        candidate.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise ValueError("Target path must be inside the repository.") from exc
    if candidate.suffix.lower() != ".py":
        raise ValueError("Only Python files are supported by the self-healing route.")
    return candidate


def run_process(command: Sequence[str], cwd: Path = REPO_ROOT) -> int:
    print(f"{PREFIX} Running: {' '.join(command)}")
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            env=os.environ.copy(),
            check=False,
            text=True,
        )
    except OSError as exc:
        print(f"{PREFIX} Command failed to start: {exc}", file=sys.stderr)
        return 126
    return completed.returncode


def route_heal(target_text: str) -> int:
    try:
        target = safe_target(target_text)
    except ValueError as exc:
        print(f"{PREFIX} {exc}", file=sys.stderr)
        return 2
    return run_process([sys.executable, str(HEAL_SCRIPT), str(target)])


def route_doctor() -> int:
    return run_process([sys.executable, str(DOCTOR_SCRIPT)])


def route_run(target_text: str) -> int:
    try:
        target = safe_target(target_text)
    except ValueError as exc:
        print(f"{PREFIX} {exc}", file=sys.stderr)
        return 2
    return run_process([sys.executable, "-u", str(target)])


def route_git(argument_text: str) -> int:
    try:
        args = shlex.split(argument_text)
    except ValueError as exc:
        print(f"{GIT_PREFIX} Could not parse Git command: {exc}", file=sys.stderr)
        return 2

    if not args:
        args = ["status", "--short", "--branch"]

    action = args[0].lower()
    allowed = {"status", "diff", "log", "add", "commit"}
    if action not in allowed:
        print(
            f"{GIT_PREFIX} Allowed actions are: status, diff, log, add, commit.",
            file=sys.stderr,
        )
        return 2

    if action == "add":
        paths = args[1:]
        if not paths:
            print(f"{GIT_PREFIX} Tell me which files to stage.", file=sys.stderr)
            return 2
        normalized = []
        for path_text in paths:
            path = Path(path_text)
            absolute = (REPO_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
            try:
                absolute.relative_to(REPO_ROOT)
            except ValueError as exc:
                print(f"{GIT_PREFIX} Refusing path outside repository: {path_text}", file=sys.stderr)
                return 2
            normalized.append(str(absolute.relative_to(REPO_ROOT)))
        return run_process(["git", "add", "--", *normalized])

    if action == "commit":
        message_parts = args[1:]
        if not message_parts:
            print(f"{GIT_PREFIX} Tell me the commit message.", file=sys.stderr)
            return 2
        return run_process(["git", "commit", "-m", " ".join(message_parts)])

    if action == "status":
        allowed_options = {"--short", "--branch"}
        if any(option not in allowed_options for option in args[1:]):
            print(f"{GIT_PREFIX} Only 'git status', 'git status --short', and 'git status --short --branch' are supported.", file=sys.stderr)
            return 2
        return run_process(["git", *args])

    if action == "diff":
        allowed_options = {"--cached"}
        if any(option not in allowed_options for option in args[1:]):
            print(f"{GIT_PREFIX} Only 'git diff' and 'git diff --cached' are supported.", file=sys.stderr)
            return 2
        return run_process(["git", *args])

    if action == "log":
        if len(args) != 1:
            print(f"{GIT_PREFIX} Only 'git log' is supported by the safe Git route.", file=sys.stderr)
            return 2
        return run_process(["git", "log"])

    return 2


FILLER_PREFIX_RE = re.compile(
    r"^(?:(?:please|kindly)\s+|(?:can|could|would)\s+you\s+(?:please\s+)?|(?:i|i\'d|i\s+would)\s+(?:like|want|need)\s+you\s+to\s+|(?:go|go\s+ahead\s+and)\s+|(?:and|okay|ok)\s+)+",
    re.IGNORECASE,
)


def normalize_command(command: str) -> str:
    text = command.strip()
    text = text.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_voice_command(command: str) -> str:
    text = normalize_command(command)
    text = re.sub(r"\b(?:dot|period)\s+(?:py|python)\b", ".py", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:underscore)\b", "_", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:hyphen|dash)\b", "-", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:slash)\b", "/", text, flags=re.IGNORECASE)
    text = re.sub(r"[!?.,:;]+(?=\s*$)", "", text)
    previous = None
    while text and text != previous:
        previous = text
        text = FILLER_PREFIX_RE.sub("", text).strip()
    text = re.sub(r"^i\s+(?=(?:help|run|heal|fix|doctor|git|quit|exit|stop)\b)", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _extract_first_command(text: str) -> str:
    """Extract a supported command from a verbose utterance without executing extras."""
    normalized = text.strip()
    match = re.search(r"\b(?:help|doctor|diagnose|quit|exit|stop)\b", normalized, re.IGNORECASE)
    if match:
        return normalized[match.start() :].split(",")[0].strip()
    match = re.search(r"\b(?:run|heal|fix)\b", normalized, re.IGNORECASE)
    if match:
        candidate = normalized[match.start() :].strip()
        candidate = re.split(r"\s+(?:and then|then|and)\s+", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
        return candidate.rstrip(".!?,;:")
    return normalized


def normalize_voice_target(target: str) -> str:
    text = normalize_voice_command(target)
    text = re.sub(r"\s+", " ", text).strip()
    letters_only = re.sub(r"[^a-z0-9]+", "", text.lower())
    if letters_only in {"samplebug", "samplebugpy", "samplebutton", "samplebuttonpy"}:
        return "sample_bug.py"
    if text.lower() in {
        "sample bug",
        "sample bug.py",
        "sample bug py",
        "sample button",
        "sample button.py",
        "sample button py",
    }:
        return "sample_bug.py"
    return text

def handle_command(command: str) -> int:
    normalized = normalize_voice_command(_extract_first_command(command))
    if not normalized:
        return 0

    lower = normalized.lower()
    if lower in {"help", "commands", "what can you do"}:
        print(
            f"{PREFIX} Commands: heal <file>, run <file>, doctor, git status, git diff, git log, "
            "git add <file>, git commit <message>, quit."
        )
        return 0
    if lower in {"doctor", "diagnose", "check setup", "system check"}:
        return route_doctor()

    if lower in {"quit", "exit", "stop"}:
        return 99

    match = re.match(r"^(?:heal|fix)(?:\s+(?:the\s+)?(?:file\s+)?)?(.+)$", normalized, re.IGNORECASE)
    if match:
        return route_heal(normalize_voice_target(match.group(1)))

    match = re.match(r"^run\s+(.+)$", normalized, re.IGNORECASE)
    if match:
        return route_run(normalize_voice_target(match.group(1)))

    if lower.startswith("git "):
        return route_git(normalized[4:])

    if lower in {"status", "show status"}:
        return route_git("status --short --branch")

    if lower == "diff":
        return route_git("diff")

    print(
        f"{PREFIX} I did not recognize that command. Say 'help' for supported commands.",
        file=sys.stderr,
    )
    return 2


def run_cli_loop() -> int:
    print(f"{PREFIX} CLI mode enabled. Type 'help' for commands.")
    while True:
        try:
            command = input(f"{PREFIX} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        code = handle_command(command)
        if code == 99:
            return 0
        if code != 0:
            print(f"{PREFIX} Command finished with status {code}.", file=sys.stderr)


def run_voice_loop() -> int:
    try:
        from listen_command import build_voice_io
    except ImportError as exc:
        print(f"{PREFIX} Voice module import failed: {exc}; using CLI mode.", file=sys.stderr)
        return run_cli_loop()

    voice = build_voice_io(enable_tts=True)
    voice.speak("Voice healer ready. Say help for commands.")
    while True:
        command = voice.listen(allow_cli_fallback=True)
        if not command:
            continue
        code = handle_command(command)
        if code == 99:
            voice.speak("Goodbye.")
            return 0
        if code == 0:
            voice.speak("Command completed.")
        else:
            voice.speak(f"Command finished with status {code}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice-controlled local self-healing developer agent.")
    parser.add_argument("--cli", action="store_true", help="Use manual text input instead of microphone input.")
    parser.add_argument("--command", help="Run one command and exit, useful for scripts and demos.")
    args = parser.parse_args()

    if args.command is not None:
        code = handle_command(args.command)
        return 0 if code == 99 else code
    if args.cli:
        return run_cli_loop()
    return run_voice_loop()


if __name__ == "__main__":
    raise SystemExit(main())
