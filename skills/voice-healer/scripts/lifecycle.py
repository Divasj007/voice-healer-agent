#!/usr/bin/env python3
"""Pure helpers for presenting Voice Healer repair lifecycle state."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

STAGES = (
    "analyzing",
    "diagnosing",
    "proposing",
    "awaiting_approval",
    "applying",
    "verifying",
    "verified",
    "rejected",
    "restored",
)

STATUS_TO_STAGE = {
    "already_healthy": "verified",
    "repair_proposed": "awaiting_approval",
    "approved_repair": "verified",
    "approved_repair_failed": "restored",
    "repair_rejected": "rejected",
    "rejected_by_user": "rejected",
    "repaired": "verified",
    "failed_and_restored": "restored",
    "rejected_target": "rejected",
}

STAGE_LABELS = {
    "analyzing": "Analyzing",
    "diagnosing": "Diagnosing",
    "proposing": "Proposing repair",
    "awaiting_approval": "Awaiting approval",
    "applying": "Applying repair",
    "verifying": "Verifying",
    "verified": "Verified",
    "rejected": "Rejected",
    "restored": "Restored",
}

TERMINAL_STAGES = {"verified", "rejected", "restored"}


def stage_for_event(event: dict[str, Any]) -> str:
    explicit = event.get("stage")
    if isinstance(explicit, str) and explicit in STAGES:
        return explicit
    status = str(event.get("status", ""))
    return STATUS_TO_STAGE.get(status, "diagnosing")


def _status_for_stage(stage: str) -> str:
    if stage == "verified":
        return "complete"
    if stage == "rejected":
        return "rejected"
    if stage == "restored":
        return "restored"
    return "pending"


def build_lifecycle(events: Iterable[dict[str, Any]], target: str | None = None) -> dict[str, Any]:
    relevant = []
    for event in events:
        if target is not None and str(event.get("target", "")) != target:
            continue
        relevant.append(event)

    if not relevant:
        return {
            "target": target or "",
            "current_stage": "idle",
            "current_label": "Idle",
            "status": "idle",
            "updated_at": None,
            "proposal_id": "",
            "model": "",
            "steps": [
                {"stage": stage, "label": STAGE_LABELS[stage], "status": "pending"}
                for stage in STAGES
            ],
        }

    def event_key(item: dict[str, Any], index: int) -> tuple[int, str, int]:
        value = item.get("timestamp")
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                # audit.read_events() returns newest-first. For events sharing
                # the same second, the lower index is therefore the newer event.
                return (1, parsed.isoformat(), -index)
            except ValueError:
                pass
        # Tests and callers without timestamps are already expected to provide
        # events in their logical order, so preserve their input order.
        return (0, "", index)

    indexed = list(enumerate(relevant))
    ordered = [item for _, item in sorted(indexed, key=lambda pair: event_key(pair[1], pair[0]))]
    latest = ordered[-1]
    current_stage = stage_for_event(latest)

    final = latest
    step_status: dict[str, str] = {stage: "pending" for stage in STAGES}
    latest_event_name = str(latest.get("event", ""))
    automatic = (
        latest_event_name in {"repair_completed", "repair_failed", "repair_rejected_target"}
        or latest.get("mode") == "automatic"
    )

    if current_stage == "verified":
        for stage in ("analyzing", "diagnosing", "proposing", "applying", "verifying", "verified"):
            step_status[stage] = "complete"
        step_status["awaiting_approval"] = "skipped" if automatic else "complete"
        step_status["rejected"] = "skipped"
        step_status["restored"] = "skipped"
    elif current_stage == "rejected":
        for stage in ("analyzing", "diagnosing", "proposing"):
            step_status[stage] = "complete"
        step_status["awaiting_approval"] = "skipped" if automatic else "complete"
        step_status["applying"] = "skipped"
        step_status["verifying"] = "skipped"
        step_status["rejected"] = "complete"
        step_status["restored"] = "skipped"
    elif current_stage == "restored":
        for stage in ("analyzing", "diagnosing", "proposing", "applying", "verifying"):
            step_status[stage] = "complete"
        step_status["awaiting_approval"] = "skipped" if automatic else "complete"
        step_status["rejected"] = "skipped"
        step_status["restored"] = "complete"
    else:
        current_index = STAGES.index(current_stage) if current_stage in STAGES else 0
        for index, stage in enumerate(STAGES):
            if stage in TERMINAL_STAGES:
                continue
            if index < current_index:
                step_status[stage] = "complete"
            elif index == current_index:
                step_status[stage] = _status_for_stage(current_stage)

    if automatic and current_stage in {"verified", "restored"}:
        step_status["awaiting_approval"] = "skipped"
        step_status["rejected"] = "skipped"

    steps = [
        {"stage": stage, "label": STAGE_LABELS[stage], "status": step_status[stage]}
        for stage in STAGES
    ]
    return {
        "target": str(final.get("target", target or "")),
        "current_stage": current_stage,
        "current_label": STAGE_LABELS.get(current_stage, current_stage),
        "status": final.get("status", "unknown"),
        "updated_at": final.get("timestamp"),
        "proposal_id": str(final.get("proposal_id", "")),
        "model": str(final.get("model", "")),
        "steps": steps,
        "event": str(final.get("event", "")),
    }


__all__ = ["STAGES", "STAGE_LABELS", "STATUS_TO_STAGE", "build_lifecycle", "stage_for_event"]
