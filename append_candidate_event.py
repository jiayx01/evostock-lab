#!/usr/bin/env python3
"""Append one validated, idempotent candidate-state event to JSONL."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from evostock_paths import data_path


EVENT_TYPES = {
    "CANDIDATE_ADDED",
    "CANDIDATE_REVIEWED",
    "STATE_CHANGED",
    "CANDIDATE_EMAIL_SENT",
    "CANDIDATE_EMAIL_FAILED",
    "CANDIDATE_OUTCOME_UPDATED",
}
STATES = {"研究队列", "持续观察", "接近触发", "开仓候选/待确认", "暂停", "移出"}
ALLOWED_TRANSITIONS = {
    "研究队列": {"持续观察", "暂停", "移出"},
    "持续观察": {"研究队列", "接近触发", "暂停", "移出"},
    "接近触发": {"持续观察", "开仓候选/待确认", "暂停", "移出"},
    "开仓候选/待确认": {"接近触发", "持续观察", "暂停", "移出"},
    "暂停": {"研究队列", "持续观察", "接近触发", "移出"},
    "移出": {"研究队列"},
}
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{6,160}$")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class CandidateEventError(ValueError):
    pass


def normalize(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CandidateEventError("input must be one JSON object")
    event_id = str(value.get("event_id") or "").strip()
    event_type = str(value.get("event_type") or "").strip().upper()
    ticker = str(value.get("ticker") or "").strip().upper()
    if not ID_RE.fullmatch(event_id):
        raise CandidateEventError("invalid event_id")
    if event_type not in EVENT_TYPES:
        raise CandidateEventError("unsupported event_type")
    if not TICKER_RE.fullmatch(ticker):
        raise CandidateEventError("invalid ticker")
    raw_time = str(value.get("occurred_at") or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw_time)
    except ValueError as exc:
        raise CandidateEventError("occurred_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CandidateEventError("occurred_at must include a timezone")
    previous = value.get("previous_state")
    current = value.get("new_state")
    if previous is not None and previous not in STATES:
        raise CandidateEventError("invalid previous_state")
    if current is not None and current not in STATES:
        raise CandidateEventError("invalid new_state")
    if event_type in {"CANDIDATE_ADDED", "STATE_CHANGED"} and current is None:
        raise CandidateEventError("state event requires new_state")
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise CandidateEventError("payload must be an object")
    return {
        "event_id": event_id,
        "event_type": event_type,
        "ticker": ticker,
        "occurred_at": raw_time,
        "previous_state": previous,
        "new_state": current,
        "payload": payload,
    }


def canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_time(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))


def apply_event(
    states: dict[str, str], last_times: dict[str, datetime], event: dict[str, Any]
) -> None:
    ticker = event["ticker"]
    event_type = event["event_type"]
    previous = event["previous_state"]
    new_state = event["new_state"]
    occurred_at = event_time(event)
    if ticker in last_times and occurred_at < last_times[ticker]:
        raise CandidateEventError("candidate event occurred_at is out of order")

    current = states.get(ticker)
    if event_type == "CANDIDATE_ADDED":
        if current is not None:
            raise CandidateEventError("candidate already exists")
        if previous is not None:
            raise CandidateEventError("CANDIDATE_ADDED previous_state must be null")
        states[ticker] = str(new_state)
    elif event_type == "STATE_CHANGED":
        if current is None:
            raise CandidateEventError("STATE_CHANGED requires an existing candidate")
        if previous != current:
            raise CandidateEventError("previous_state does not match current candidate state")
        if new_state == current:
            raise CandidateEventError("STATE_CHANGED requires a different new_state")
        if new_state not in ALLOWED_TRANSITIONS[current]:
            raise CandidateEventError(f"illegal candidate transition: {current} -> {new_state}")
        states[ticker] = str(new_state)
    else:
        if current is None:
            raise CandidateEventError(f"{event_type} requires an existing candidate")
        if previous is not None and previous != current:
            raise CandidateEventError("event previous_state does not match current candidate state")
        if new_state is not None and new_state != current:
            raise CandidateEventError("non-transition event cannot change candidate state")
    last_times[ticker] = occurred_at


def replay_events(events: list[dict[str, Any]]) -> dict[str, str]:
    states: dict[str, str] = {}
    last_times: dict[str, datetime] = {}
    for event in events:
        apply_event(states, last_times, event)
    return states


def load_event_log(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = normalize(json.loads(line))
            except (json.JSONDecodeError, CandidateEventError) as exc:
                raise CandidateEventError(f"invalid JSONL at line {line_number}") from exc
            if event["event_id"] in seen_ids:
                raise CandidateEventError(f"duplicate event_id at line {line_number}")
            seen_ids.add(event["event_id"])
            events.append(event)
    return events


def validate_candidate_watchlist(log_path: Path, watchlist_path: Path) -> dict[str, str]:
    states = replay_events(load_event_log(log_path))
    with watchlist_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        if not {"ticker", "state"}.issubset(columns):
            raise CandidateEventError("candidate watchlist requires ticker and state columns")
        rows = list(reader)

    csv_states: dict[str, str] = {}
    for row_number, row in enumerate(rows, start=2):
        ticker = str(row.get("ticker") or "").strip().upper()
        state = str(row.get("state") or "").strip()
        if not TICKER_RE.fullmatch(ticker):
            raise CandidateEventError(f"candidate watchlist row {row_number}: invalid ticker")
        if state not in STATES:
            raise CandidateEventError(f"candidate watchlist row {row_number}: invalid state")
        if ticker in csv_states:
            raise CandidateEventError(f"candidate watchlist row {row_number}: duplicate ticker")
        csv_states[ticker] = state

    active_log_states = {ticker: state for ticker, state in states.items() if state != "移出"}
    if len(active_log_states) > 5:
        raise CandidateEventError("candidate watchlist has more than 5 active candidates")
    for ticker, state in csv_states.items():
        if states.get(ticker) != state:
            raise CandidateEventError(f"candidate state differs between CSV and event log: {ticker}")
    missing = sorted(set(active_log_states) - set(csv_states))
    if missing:
        raise CandidateEventError(
            f"active candidates missing from watchlist CSV: {', '.join(missing)}"
        )
    return states


def append(path: Path, event: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        events: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                existing = normalize(json.loads(line))
            except (json.JSONDecodeError, CandidateEventError) as exc:
                raise CandidateEventError(f"invalid JSONL at line {line_number}") from exc
            if existing["event_id"] in seen_ids:
                raise CandidateEventError(f"duplicate event_id at line {line_number}")
            seen_ids.add(existing["event_id"])
            events.append(existing)
        replay_events(events)
        existing_match = next(
            (item for item in events if item["event_id"] == event["event_id"]), None
        )
        if existing_match is not None:
            if canonical(existing_match) != canonical(event):
                raise CandidateEventError("event_id already exists with different content")
            return "DUPLICATE_NOOP"
        states: dict[str, str] = {}
        last_times: dict[str, datetime] = {}
        for existing in events:
            apply_event(states, last_times, existing)
        apply_event(states, last_times, event)
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return "APPENDED"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--log", default=data_path("candidate_state_log.jsonl"))
    args = parser.parse_args()
    try:
        event = normalize(json.loads(Path(args.input).read_text(encoding="utf-8")))
        status = append(Path(args.log), event)
    except (OSError, json.JSONDecodeError, CandidateEventError) as exc:
        print(f"candidate event append failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": status, "event_id": event["event_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
