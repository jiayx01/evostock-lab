#!/usr/bin/env python3
"""Evaluate the midnight automation schedule before loading investment context."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


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


def evaluate_gate(now: datetime, calendar: Any | None = None) -> dict[str, Any]:
    if now.tzinfo is None:
        raise GateError("now must include a timezone")

    now_shanghai = now.astimezone(SHANGHAI)
    now_new_york = now.astimezone(NEW_YORK)
    scheduled_window_ok = now_shanghai.hour == 0 and now_shanghai.minute < 15

    calendar = calendar if calendar is not None else xcals.get_calendar("XNYS")
    session = pd.Timestamp(now_new_york.date().isoformat())
    xnys_is_session = bool(calendar.is_session(session))
    market_open = None
    market_close = None
    in_regular_session = False
    if xnys_is_session:
        market_open_dt = calendar.session_open(session).to_pydatetime()
        market_close_dt = calendar.session_close(session).to_pydatetime()
        now_utc = now.astimezone(timezone.utc)
        in_regular_session = market_open_dt <= now_utc < market_close_dt
        market_open = market_open_dt.isoformat()
        market_close = market_close_dt.isoformat()

    execute_gate = scheduled_window_ok and xnys_is_session and in_regular_session
    if not scheduled_window_ok:
        skip_reason = "SKIP_OUTSIDE_SCHEDULED_WINDOW"
    elif not xnys_is_session:
        skip_reason = "SKIP_NON_XNYS_SESSION"
    elif not in_regular_session:
        skip_reason = "SKIP_OUTSIDE_REGULAR_SESSION"
    else:
        skip_reason = None

    return {
        "as_of_shanghai": now_shanghai.isoformat(),
        "as_of_new_york": now_new_york.isoformat(),
        "scheduled_slot": (
            f"{now_shanghai.date().isoformat()}-0000-CST"
            if scheduled_window_ok
            else None
        ),
        "scheduled_window_ok": scheduled_window_ok,
        "xnys_is_session": xnys_is_session,
        "in_regular_session": in_regular_session,
        "execute_gate": execute_gate,
        "skip_reason": skip_reason,
        "xnys_session_date": now_new_york.date().isoformat(),
        "market_open": market_open,
        "market_close": market_close,
    }


def error_result(now: datetime, exc: Exception) -> dict[str, Any]:
    now_shanghai = now.astimezone(SHANGHAI)
    now_new_york = now.astimezone(NEW_YORK)
    return {
        "as_of_shanghai": now_shanghai.isoformat(),
        "as_of_new_york": now_new_york.isoformat(),
        "scheduled_slot": (
            f"{now_shanghai.date().isoformat()}-0000-CST"
            if now_shanghai.hour == 0 and now_shanghai.minute < 15
            else None
        ),
        "scheduled_window_ok": now_shanghai.hour == 0 and now_shanghai.minute < 15,
        "xnys_is_session": None,
        "in_regular_session": None,
        "execute_gate": False,
        "skip_reason": "SKIP_STAGE0_ERROR",
        "xnys_session_date": now_new_york.date().isoformat(),
        "market_open": None,
        "market_close": None,
        "gate_error": f"{type(exc).__name__}: {exc}",
    }


def append_skip_memory(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = (
        f"\n## Stage 0 {result['as_of_shanghai']} / {result['as_of_new_york']}\n\n"
        f"- `scheduled_window_ok={str(result['scheduled_window_ok']).lower()}`; "
        f"`xnys_is_session={json.dumps(result['xnys_is_session'])}`; "
        f"`in_regular_session={json.dumps(result['in_regular_session'])}`; "
        f"`execute_gate=false`; `skip_reason={result['skip_reason']}`.\n"
        "- Stage 0 stopped before loading investment prompts, skills, Gmail, "
        "holdings, ledgers, or analysis subagents.\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(record)
        handle.flush()
        os.fsync(handle.fileno())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the Stage 0 gate for the midnight automation."
    )
    parser.add_argument(
        "--now",
        help="Aware ISO-8601 time for deterministic checks; defaults to the current time.",
    )
    parser.add_argument(
        "--append-skip-memory",
        type=Path,
        help="Append a minimal record here only when execute_gate is false.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        now = parse_now(args.now)
    except GateError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        result = evaluate_gate(now)
    except Exception as exc:  # Fail closed if the local calendar cannot be evaluated.
        result = error_result(now, exc)

    if not result["execute_gate"] and args.append_skip_memory:
        try:
            append_skip_memory(args.append_skip_memory, result)
            result["skip_memory_append_status"] = "APPENDED"
        except OSError as exc:
            result["skip_memory_append_status"] = "FAILED"
            result["skip_memory_append_error"] = f"{type(exc).__name__}: {exc}"
    else:
        result["skip_memory_append_status"] = "NOT_REQUIRED"

    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
