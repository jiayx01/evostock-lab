#!/usr/bin/env python3
"""Append one validated, idempotent event to the portfolio decision JSONL log."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from evostock_paths import data_path


ALLOWED_EVENT_TYPES = {
    "DECISION_CREATED",
    "EMAIL_SEND_INTENT",
    "EMAIL_SENT",
    "EMAIL_FAILED",
    "OUTCOME_UPDATED",
    "USER_CAUSALITY_CONFIRMED",
}
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{6,160}$")


class DecisionEventError(ValueError):
    pass


def validate_time(value: Any) -> str:
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise DecisionEventError("occurred_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise DecisionEventError("occurred_at must include a timezone")
    return raw


def normalize_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionEventError("input must be one JSON object")
    event_id = str(value.get("event_id") or "").strip()
    decision_id = str(value.get("decision_id") or "").strip()
    event_type = str(value.get("event_type") or "").strip().upper()
    if not ID_RE.fullmatch(event_id):
        raise DecisionEventError("invalid event_id")
    if not ID_RE.fullmatch(decision_id):
        raise DecisionEventError("invalid decision_id")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise DecisionEventError("unsupported event_type")
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise DecisionEventError("payload must be an object")
    return {
        "event_id": event_id,
        "event_type": event_type,
        "decision_id": decision_id,
        "occurred_at": validate_time(value.get("occurred_at")),
        "payload": payload,
    }


def canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_time(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))


def validate_sequence(existing: list[dict[str, Any]], event: dict[str, Any]) -> None:
    related = [item for item in existing if item["decision_id"] == event["decision_id"]]
    created = [item for item in related if item["event_type"] == "DECISION_CREATED"]
    event_type = event["event_type"]

    if event_type == "DECISION_CREATED":
        if created:
            raise DecisionEventError("decision already has DECISION_CREATED")
        return
    if len(created) != 1:
        raise DecisionEventError("decision must have exactly one DECISION_CREATED first")
    if related and event_time(event) < max(event_time(item) for item in related):
        raise DecisionEventError("event occurred_at is earlier than the latest decision event")

    if event_type not in {"EMAIL_SEND_INTENT", "EMAIL_SENT", "EMAIL_FAILED"}:
        return
    marker = str(event["payload"].get("idempotency_marker") or "").strip()
    if not marker:
        raise DecisionEventError("email event requires idempotency_marker")

    intents = [item for item in related if item["event_type"] == "EMAIL_SEND_INTENT"]
    sent = [item for item in related if item["event_type"] == "EMAIL_SENT"]
    if event_type == "EMAIL_SEND_INTENT":
        if intents or sent:
            raise DecisionEventError("decision already has an email send intent or success")
        if not str(event["payload"].get("recipient") or "").strip():
            raise DecisionEventError("EMAIL_SEND_INTENT requires recipient")
        if not str(event["payload"].get("subject") or "").strip():
            raise DecisionEventError("EMAIL_SEND_INTENT requires subject")
        return

    if len(intents) != 1:
        raise DecisionEventError("email result requires exactly one EMAIL_SEND_INTENT")
    intent_marker = str(intents[0]["payload"].get("idempotency_marker") or "").strip()
    if marker != intent_marker:
        raise DecisionEventError("email idempotency marker differs from send intent")
    if event_type == "EMAIL_SENT":
        if sent:
            raise DecisionEventError("decision already has EMAIL_SENT")
        if not str(event["payload"].get("message_id") or "").strip():
            raise DecisionEventError("EMAIL_SENT requires message_id")
    elif sent:
        raise DecisionEventError("EMAIL_FAILED cannot follow EMAIL_SENT")
    elif not str(event["payload"].get("error") or "").strip():
        raise DecisionEventError("EMAIL_FAILED requires error")


def append_event(path: Path, event: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        existing_events: list[dict[str, Any]] = []
        existing_match = None
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                existing = normalize_event(json.loads(line))
            except (json.JSONDecodeError, DecisionEventError) as exc:
                raise DecisionEventError(f"invalid JSONL at line {line_number}") from exc
            existing_events.append(existing)
            if existing.get("event_id") == event["event_id"]:
                existing_match = existing
        validated: list[dict[str, Any]] = []
        for existing in existing_events:
            validate_sequence(validated, existing)
            validated.append(existing)
        if existing_match is not None:
            if canonical(existing_match) != canonical(event):
                raise DecisionEventError("event_id already exists with different content")
            return "DUPLICATE_NOOP"
        validate_sequence(existing_events, event)
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return "APPENDED"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to one JSON event object")
    parser.add_argument("--log", default=data_path("decision_log.jsonl"))
    args = parser.parse_args()
    try:
        with Path(args.input).open(encoding="utf-8") as handle:
            event = normalize_event(json.load(handle))
        result = append_event(Path(args.log), event)
    except (OSError, json.JSONDecodeError, DecisionEventError) as exc:
        print(f"decision event append failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result, "event_id": event["event_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
