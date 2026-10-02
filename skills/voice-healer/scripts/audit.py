#!/usr/bin/env python3
"""Small local audit log for Voice Healer repair history."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(os.environ.get("VOICE_HEALER_REPO_ROOT", str(SCRIPT_DIR.parents[2]))).resolve()
AUDIT_DIR = REPO_ROOT / ".voice-healer"
HISTORY_FILE = AUDIT_DIR / "history.jsonl"
MAX_EVENTS = 250
MAX_FIELD_CHARS = 4000


def _clip(value: Any, limit: int = MAX_FIELD_CHARS) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {str(key): _clip(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip(item) for item in value[:100]]
    return value


def _atomic_write_lines(lines: list[str]) -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="history.", suffix=".tmp", dir=str(AUDIT_DIR), text=True)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.writelines(lines[-MAX_EVENTS:])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, HISTORY_FILE)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def append_event(event: Mapping[str, Any]) -> None:
    """Append a best-effort local event; audit failure never blocks healing."""
    try:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            **{str(key): _clip(value) for key, value in event.items()},
        }
        session_id = os.environ.get("VOICE_HEALER_SESSION_ID", "").strip()
        if session_id and "session_id" not in payload:
            payload["session_id"] = session_id
        existing: list[str] = []
        if HISTORY_FILE.exists():
            existing = HISTORY_FILE.read_text(encoding="utf-8").splitlines(True)[-MAX_EVENTS + 1 :]
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        _atomic_write_lines(existing + [line])
    except (OSError, TypeError, ValueError):
        return


def read_events(limit: int = 50) -> list[dict[str, Any]]:
    """Read recent valid events, newest first."""
    limit = max(1, min(int(limit), MAX_EVENTS))
    if not HISTORY_FILE.exists():
        return []
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    events: list[dict[str, Any]] = []
    for line in reversed(lines[-MAX_EVENTS:]):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
            if len(events) >= limit:
                break
    return events


def build_repair_history(events: list[dict[str, Any]], limit: int = 50) -> list[dict[str, Any]]:
    """Group raw audit events into user-facing repair sessions.

    Interactive proposal/approval/rejection events are grouped by proposal_id.
    Automatic repair events are individual repair sessions. Non-repair events
    such as demo resets and session probes are excluded from the repair count.
    """
    limit = max(1, min(int(limit), 100))
    grouped: dict[str, list[dict[str, Any]]] = {}
    anonymous: list[dict[str, Any]] = []

    for event in events:
        event_name = str(event.get("event", ""))
        if event_name in {"demo_reset", "session_probe"} or not event_name:
            continue
        if event_name == "repair":
            anonymous.append(event)
            continue
        if event_name not in {"repair_proposed", "repair_approved", "repair_approved_failed", "repair_rejected"}:
            continue
        proposal_id = str(event.get("proposal_id", "")).strip()
        if proposal_id:
            grouped.setdefault(proposal_id, []).append(event)
        else:
            anonymous.append(event)

    repairs: list[dict[str, Any]] = []
    for proposal_id, proposal_events in grouped.items():
        chronological = sorted(proposal_events, key=lambda event: str(event.get("timestamp", "")))
        final = chronological[-1]
        durations = [
            int(event["duration_ms"])
            for event in chronological
            if isinstance(event.get("duration_ms"), (int, float))
        ]
        attempts = [
            int(event.get("attempts", 0) or 0)
            for event in chronological
            if isinstance(event.get("attempts"), (int, float))
        ]
        repairs.append(
            {
                "repair_id": proposal_id,
                "target": str(final.get("target", "")),
                "status": str(final.get("status", "unknown")),
                "event": str(final.get("event", "")),
                "model": str(final.get("model", "")),
                "attempts": max(attempts or [0]),
                "duration_ms": sum(durations),
                "timestamp": str(final.get("timestamp", "")),
                "original_sha256": next(
                    (str(event.get("original_sha256", "")) for event in chronological if event.get("original_sha256")),
                    "",
                ),
                "final_sha256": next(
                    (str(event.get("final_sha256", "")) for event in reversed(chronological) if event.get("final_sha256")),
                    "",
                ),
                "backup": next(
                    (str(event.get("backup", "")) for event in reversed(chronological) if event.get("backup")),
                    "",
                ),
                "error": next(
                    (str(event.get("error", "")) for event in reversed(chronological) if event.get("error")),
                    "",
                ),
                "events": chronological,
            }
        )

    for event in anonymous:
        duration = event.get("duration_ms")
        repairs.append(
            {
                "repair_id": str(event.get("repair_id") or event.get("timestamp") or len(repairs)),
                "target": str(event.get("target", "")),
                "status": str(event.get("status", "unknown")),
                "event": str(event.get("event", "")),
                "model": str(event.get("model", "")),
                "attempts": int(event.get("attempts", 0) or 0),
                "duration_ms": int(duration) if isinstance(duration, (int, float)) else 0,
                "timestamp": str(event.get("timestamp", "")),
                "original_sha256": str(event.get("original_sha256", "")),
                "final_sha256": str(event.get("final_sha256", "")),
                "backup": str(event.get("backup", "")),
                "error": str(event.get("error", "")),
                "events": [event],
            }
        )

    repairs.sort(key=lambda repair: repair.get("timestamp", ""), reverse=True)
    return repairs[:limit]
