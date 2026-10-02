#!/usr/bin/env python3
"""Human-approved repair proposals for the local Voice Healer UI."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from audit import append_event
from safety_guard import inspect_candidate
from heal_code import (
    MODEL_NAME,
    atomic_write,
    backup_file,
    build_prompt,
    call_ollama,
    restore_backup,
    run_target,
    strip_markdown_fences,
    validate_python_source,
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(os.environ.get("VOICE_HEALER_REPO_ROOT", str(SCRIPT_DIR.parents[2]))).resolve()
AUDIT_DIR = REPO_ROOT / ".voice-healer"
PENDING_FILE = AUDIT_DIR / "pending_repair.json"
PENDING_TTL_SECONDS = 30 * 60
MAX_PENDING_SOURCE_CHARS = 120_000
_pending_lock = Lock()


class SafetyGuardError(RuntimeError):
    """Raised when a proposed repair introduces blocked risky behavior."""

    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        super().__init__(report.get("summary", "Safety Guard blocked this repair."))


def build_proposal_diff(relative: str, source: str, candidate: str) -> str:
    return "".join(
        difflib.unified_diff(
            source.splitlines(keepends=True),
            candidate.splitlines(keepends=True),
            fromfile=f"{relative} (current)",
            tofile=f"{relative} (proposed)",
        )
    )


def sha256_text(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT).as_posix()


def configure_repo_root(repo_root: Path) -> None:
    global REPO_ROOT, AUDIT_DIR, PENDING_FILE
    REPO_ROOT = repo_root.resolve()
    AUDIT_DIR = REPO_ROOT / ".voice-healer"
    PENDING_FILE = AUDIT_DIR / "pending_repair.json"


def _atomic_json_write(payload: dict[str, Any]) -> None:
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="pending.", suffix=".tmp", dir=str(PENDING_FILE.parent), text=True)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, PENDING_FILE)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _read_pending_unlocked() -> dict[str, Any] | None:
    if not PENDING_FILE.exists():
        return None
    try:
        payload = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    current_session = os.environ.get("VOICE_HEALER_SESSION_ID", "").strip()
    pending_session = str(payload.get("session_id", "")).strip()
    if current_session and pending_session and current_session != pending_session:
        try:
            PENDING_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    created = payload.get("created_at")
    if isinstance(created, str):
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(created)).total_seconds()
            if age > PENDING_TTL_SECONDS:
                PENDING_FILE.unlink(missing_ok=True)
                return None
        except (ValueError, TypeError):
            return None
    return payload


def get_pending() -> dict[str, Any] | None:
    with _pending_lock:
        payload = _read_pending_unlocked()
        return dict(payload) if payload else None


def clear_pending() -> None:
    with _pending_lock:
        try:
            PENDING_FILE.unlink(missing_ok=True)
        except OSError:
            return


def propose_repair(path: Path, *, ollama_timeout: int = 120) -> dict[str, Any]:
    path = path.resolve()
    relative = repo_relative(path)
    started = time.perf_counter()
    source = path.read_text(encoding="utf-8")
    first_run = run_target(path, timeout=30)
    if first_run.returncode == 0:
        with _pending_lock:
            existing = _read_pending_unlocked()
            if existing and existing.get("target") == relative:
                PENDING_FILE.unlink(missing_ok=True)
        return {
            "status": "already_healthy",
            "target": relative,
            "returncode": 0,
            "stdout": first_run.stdout,
            "stderr": first_run.stderr,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }

    error = first_run.stderr or f"Process exited with code {first_run.returncode}."
    with _pending_lock:
        existing = _read_pending_unlocked()
        if existing:
            if existing.get("target") == relative and existing.get("base_sha256") == sha256_text(source):
                return {
                    "status": "pending",
                    "proposal_id": existing["proposal_id"],
                    "target": relative,
                    "base_sha256": existing["base_sha256"],
                    "candidate_sha256": existing["candidate_sha256"],
                    "candidate_source": existing["candidate_source"],
                    "diagnostics": existing.get("diagnostics", error),
                    "created_at": existing.get("created_at"),
                    "model": MODEL_NAME,
                    "proposal_diff": existing.get("proposal_diff", ""),
                    "safety_report": existing.get("safety_report"),
                    "duration_ms": existing.get("duration_ms", round((time.perf_counter() - started) * 1000)),
                }
            PENDING_FILE.unlink(missing_ok=True)

    candidate = strip_markdown_fences(call_ollama(build_prompt(source, error, 1), ollama_timeout))
    validate_python_source(candidate)
    if candidate == source:
        raise RuntimeError("The model returned unchanged source; no repair proposal was created.")
    safety_report = inspect_candidate(source, candidate, REPO_ROOT)
    proposal_id = uuid.uuid4().hex
    payload = {
        "proposal_id": proposal_id,
        "target": relative,
        "session_id": os.environ.get("VOICE_HEALER_SESSION_ID", "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": MODEL_NAME,
        "base_sha256": sha256_text(source),
        "candidate_sha256": sha256_text(candidate),
        "candidate_source": candidate[:MAX_PENDING_SOURCE_CHARS],
        "proposal_diff": build_proposal_diff(relative, source, candidate),
        "diagnostics": error[:24000],
        "safety_report": safety_report,
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }
    if len(candidate) > MAX_PENDING_SOURCE_CHARS:
        raise RuntimeError("Repair candidate is too large to hold safely in a pending proposal.")
    with _pending_lock:
        _atomic_json_write(payload)
    append_event(
        {
            "event": "repair_proposed",
            "status": "repair_proposed",
            "stage": "awaiting_approval",
            "mode": "interactive",
            "target": relative,
            "model": MODEL_NAME,
            "attempts": 1,
            "returncode": first_run.returncode,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "error": error[:2000],
            "original_sha256": sha256_text(source),
            "final_sha256": "",
            "backup": "",
            "proposal_id": proposal_id,
        }
    )
    return {
        "status": "proposed",
        "proposal_id": proposal_id,
        "target": relative,
        "base_sha256": payload["base_sha256"],
        "candidate_sha256": payload["candidate_sha256"],
        "candidate_source": candidate,
        "diagnostics": error,
        "created_at": payload["created_at"],
        "model": MODEL_NAME,
        "proposal_diff": payload["proposal_diff"],
        "safety_report": safety_report,
        "duration_ms": payload["duration_ms"],
    }


def apply_pending(path: Path, proposal_id: str, *, timeout: int = 30) -> dict[str, Any]:
    path = path.resolve()
    relative = repo_relative(path)
    started = time.perf_counter()
    started_source = path.read_text(encoding="utf-8")
    with _pending_lock:
        pending = _read_pending_unlocked()
    if not pending:
        raise ValueError("No pending repair proposal exists.")
    if pending.get("proposal_id") != proposal_id:
        raise ValueError("This repair proposal is no longer current.")
    if pending.get("target") != relative:
        raise ValueError("Repair proposal target mismatch.")
    if pending.get("base_sha256") != sha256_text(started_source):
        raise RuntimeError("The target changed after the proposal was generated. Generate a new proposal first.")
    candidate = pending.get("candidate_source")
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError("Pending repair candidate is missing.")
    validate_python_source(candidate)
    safety_report = inspect_candidate(started_source, candidate, REPO_ROOT)
    if safety_report["blocked"]:
        print(f"[Self-Healing] Safety Guard blocked approval: {safety_report['summary']}", file=sys.stderr)
        raise SafetyGuardError(safety_report)

    backup = backup_file(path)
    try:
        atomic_write(path, candidate)
        verification = run_target(path, timeout=timeout)
        if verification.returncode == 0:
            clear_pending()
            append_event(
                {
                    "event": "repair_approved",
                    "status": "approved_repair",
                    "stage": "verified",
                    "mode": "interactive",
                    "target": relative,
                    "model": MODEL_NAME,
                    "attempts": 1,
                    "returncode": 0,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "error": "",
                    "original_sha256": sha256_text(started_source),
                    "final_sha256": sha256_text(candidate),
                    "backup": backup.name,
                    "proposal_id": proposal_id,
                }
            )
            return {
                "status": "approved_repair",
                "target": relative,
                "returncode": 0,
                "stdout": verification.stdout,
                "stderr": verification.stderr,
                "backup_exists": backup.exists(),
                "source": candidate,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            }

        restore_backup(path, backup)
        clear_pending()
        append_event(
            {
                "event": "repair_approved_failed",
                "status": "approved_repair_failed",
                "stage": "restored",
                "mode": "interactive",
                "target": relative,
                "model": MODEL_NAME,
                "attempts": 1,
                "returncode": verification.returncode,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "error": verification.stderr[:2000],
                "original_sha256": sha256_text(started_source),
                "final_sha256": sha256_text(started_source),
                "backup": backup.name,
                "proposal_id": proposal_id,
            }
        )
        return {
            "status": "approved_repair_failed",
            "target": relative,
            "returncode": verification.returncode,
            "stdout": verification.stdout,
            "stderr": verification.stderr,
            "backup_exists": backup.exists(),
            "source": started_source,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception:
        try:
            if path.read_text(encoding="utf-8") != started_source:
                restore_backup(path, backup)
        except (OSError, UnicodeDecodeError):
            pass
        raise


def reject_pending(path: Path, proposal_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    path = path.resolve()
    relative = repo_relative(path)
    with _pending_lock:
        pending = _read_pending_unlocked()
        if not pending:
            raise ValueError("No pending repair proposal exists.")
        if pending.get("proposal_id") != proposal_id or pending.get("target") != relative:
            raise ValueError("This repair proposal is no longer current.")
        current = path.read_text(encoding="utf-8")
        if pending.get("base_sha256") != sha256_text(current):
            raise RuntimeError("The target changed after the proposal was generated; rejection is no longer necessary.")
        PENDING_FILE.unlink(missing_ok=True)
    append_event(
        {
            "event": "repair_rejected",
            "status": "rejected_by_user",
            "stage": "rejected",
            "mode": "interactive",
            "target": relative,
            "model": pending.get("model", MODEL_NAME),
            "attempts": 1,
            "returncode": 1,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "error": "User rejected repair proposal.",
            "original_sha256": pending.get("base_sha256", ""),
            "final_sha256": pending.get("base_sha256", ""),
            "backup": "",
            "proposal_id": proposal_id,
        }
    )
    return {
        "status": "rejected_by_user",
        "target": relative,
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }
