#!/usr/bin/env python3
"""Fail-closed schedule gate for EvoStock automation modes."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


NEW_YORK = ZoneInfo("America/New_York")
DEFAULT_SLOTS = {
    "intraday": ("09:30", "10:30", "11:30", "12:30"),
    "daily-review": ("12:30",),
    "post-close": ("16:30",),
    "weekly-review": ("16:45",),
    "monthly-review": ("17:00",),
}
POST_CLOSE_MODES = {"post-close", "weekly-review", "monthly-review"}


class GateError(ValueError):
    pass


def parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise GateError("--now must include a timezone")
    return parsed


def parse_slots(values: Iterable[str]) -> tuple[time, ...]:
    slots: list[time] = []
    for value in values:
        try:
            parsed = time.fromisoformat(value.strip())
        except ValueError as exc:
            raise GateError(f"invalid slot {value!r}; expected HH:MM") from exc
        if parsed.second or parsed.microsecond:
            raise GateError(f"invalid slot {value!r}; seconds are not supported")
        slots.append(parsed)
    if not slots:
        raise GateError("at least one slot is required")
    return tuple(sorted(set(slots)))


def _next_session(calendar: Any, session: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(calendar.next_session(session))


def _is_last_session_of_week(calendar: Any, session: pd.Timestamp) -> bool:
    current = session.date().isocalendar()[:2]
    following = _next_session(calendar, session).date().isocalendar()[:2]
    return current != following


def _is_last_session_of_month(calendar: Any, session: pd.Timestamp) -> bool:
    current = session.date()
    following = _next_session(calendar, session).date()
    return (current.year, current.month) != (following.year, following.month)


def evaluate_gate(
    now: datetime,
    mode: str,
    *,
    slot_values: Iterable[str] | None = None,
    window_minutes: int = 15,
    calendar: Any | None = None,
) -> dict[str, Any]:
    if now.tzinfo is None:
        raise GateError("now must include a timezone")
    if mode not in DEFAULT_SLOTS:
        raise GateError(f"unsupported mode: {mode}")
    if not 1 <= window_minutes <= 60:
        raise GateError("window_minutes must be between 1 and 60")

    slot_source = slot_values if slot_values is not None else DEFAULT_SLOTS[mode]
    slots = parse_slots(slot_source)
    now_et = now.astimezone(NEW_YORK)
    now_utc = now.astimezone(timezone.utc)
    session = pd.Timestamp(now_et.date().isoformat())

    matched_slot: time | None = None
    for slot in slots:
        start = datetime.combine(now_et.date(), slot, tzinfo=NEW_YORK)
        if start <= now_et < start + timedelta(minutes=window_minutes):
            matched_slot = slot
            break

    calendar = calendar if calendar is not None else xcals.get_calendar("XNYS")
    xnys_is_session = bool(calendar.is_session(session))
    market_open = None
    market_close = None
    in_regular_session = False
    after_regular_session = False
    cadence_ok = True
    if xnys_is_session:
        market_open_dt = calendar.session_open(session).to_pydatetime()
        market_close_dt = calendar.session_close(session).to_pydatetime()
        market_open = market_open_dt.isoformat()
        market_close = market_close_dt.isoformat()
        in_regular_session = market_open_dt <= now_utc < market_close_dt
        after_regular_session = now_utc >= market_close_dt
        if mode == "weekly-review":
            cadence_ok = _is_last_session_of_week(calendar, session)
        elif mode == "monthly-review":
            cadence_ok = _is_last_session_of_month(calendar, session)

    phase_ok = after_regular_session if mode in POST_CLOSE_MODES else in_regular_session
    scheduled_window_ok = matched_slot is not None
    execute_gate = scheduled_window_ok and xnys_is_session and phase_ok and cadence_ok

    if not scheduled_window_ok:
        skip_reason = "SKIP_OUTSIDE_SCHEDULED_WINDOW"
    elif not xnys_is_session:
        skip_reason = "SKIP_NON_XNYS_SESSION"
    elif not phase_ok:
        skip_reason = "SKIP_WRONG_MARKET_PHASE"
    elif not cadence_ok:
        skip_reason = "SKIP_CADENCE_NOT_DUE"
    else:
        skip_reason = None

    slot_label = matched_slot.strftime("%H%M") if matched_slot else None
    return {
        "mode": mode,
        "as_of_utc": now_utc.isoformat(),
        "as_of_new_york": now_et.isoformat(),
        "scheduled_slot": (
            f"{now_et.date().isoformat()}-{slot_label}-ET-{mode}"
            if slot_label
            else None
        ),
        "configured_slots_et": [slot.strftime("%H:%M") for slot in slots],
        "window_minutes": window_minutes,
        "scheduled_window_ok": scheduled_window_ok,
        "xnys_is_session": xnys_is_session,
        "in_regular_session": in_regular_session,
        "after_regular_session": after_regular_session,
        "cadence_ok": cadence_ok,
        "execute_gate": execute_gate,
        "skip_reason": skip_reason,
        "xnys_session_date": now_et.date().isoformat(),
        "market_open": market_open,
        "market_close": market_close,
    }


def error_result(now: datetime, mode: str, exc: Exception) -> dict[str, Any]:
    now_et = now.astimezone(NEW_YORK)
    return {
        "mode": mode,
        "as_of_utc": now.astimezone(timezone.utc).isoformat(),
        "as_of_new_york": now_et.isoformat(),
        "scheduled_slot": None,
        "configured_slots_et": list(DEFAULT_SLOTS.get(mode, ())),
        "scheduled_window_ok": None,
        "xnys_is_session": None,
        "in_regular_session": None,
        "after_regular_session": None,
        "cadence_ok": None,
        "execute_gate": False,
        "skip_reason": "SKIP_STAGE0_ERROR",
        "xnys_session_date": now_et.date().isoformat(),
        "market_open": None,
        "market_close": None,
        "gate_error": f"{type(exc).__name__}: {exc}",
    }


def append_gate_event(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event": "AUTOMATION_GATE",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    payload = (json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=sorted(DEFAULT_SLOTS))
    parser.add_argument("--now", help="Aware ISO-8601 time; defaults to current time")
    parser.add_argument(
        "--slots",
        help="Comma-separated America/New_York slots; defaults to the mode policy",
    )
    parser.add_argument("--window-minutes", type=int, default=15)
    parser.add_argument(
        "--append-event-log",
        type=Path,
        help="Append the Stage 0 result as one JSONL event",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        now = parse_now(args.now)
    except GateError as exc:
        raise SystemExit(str(exc)) from exc

    slot_values = args.slots.split(",") if args.slots else None
    try:
        result = evaluate_gate(
            now,
            args.mode,
            slot_values=slot_values,
            window_minutes=args.window_minutes,
        )
    except Exception as exc:  # A calendar or policy failure must stop the run.
        result = error_result(now, args.mode, exc)

    if args.append_event_log:
        try:
            append_gate_event(args.append_event_log.expanduser(), result)
            result["event_append_status"] = "APPENDED"
        except OSError as exc:
            result["execute_gate"] = False
            result["skip_reason"] = "SKIP_STAGE0_ERROR"
            result["event_append_status"] = "FAILED"
            result["event_append_error"] = f"{type(exc).__name__}: {exc}"
    else:
        result["event_append_status"] = "NOT_REQUESTED"

    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
