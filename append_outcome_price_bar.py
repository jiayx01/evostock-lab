#!/usr/bin/env python3
"""Append one validated, idempotent market-price observation for outcome scoring."""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import exchange_calendars as xcals
import pandas as pd

from evostock_paths import data_path


BAR_COLUMNS = [
    "bar_id",
    "ticker",
    "bar_at",
    "session_date",
    "bar_type",
    "close",
    "source",
    "collected_at",
]
BAR_TYPES = {"INTRADAY", "DAILY_CLOSE"}
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{6,200}$")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class PriceBarError(ValueError):
    pass


def parse_aware_time(value: Any, field: str) -> datetime:
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise PriceBarError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise PriceBarError(f"{field} must include a timezone")
    return parsed


def aware_time(value: Any, field: str) -> str:
    return parse_aware_time(value, field).isoformat()


def normalize(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise PriceBarError("input must be one JSON object")
    bar_id = str(value.get("bar_id") or "").strip()
    ticker = str(value.get("ticker") or "").strip().upper()
    bar_type = str(value.get("bar_type") or "").strip().upper()
    source = str(value.get("source") or "").strip()
    if not ID_RE.fullmatch(bar_id):
        raise PriceBarError("invalid bar_id")
    if not TICKER_RE.fullmatch(ticker):
        raise PriceBarError("invalid ticker")
    if bar_type not in BAR_TYPES:
        raise PriceBarError("invalid bar_type")
    if not source:
        raise PriceBarError("source is required")
    try:
        session_date = date.fromisoformat(str(value.get("session_date") or ""))
    except ValueError as exc:
        raise PriceBarError("session_date must be an ISO date") from exc
    try:
        close = Decimal(str(value.get("close") or "").strip())
    except InvalidOperation as exc:
        raise PriceBarError("close must be numeric") from exc
    if not close.is_finite() or close <= 0:
        raise PriceBarError("close must be positive")
    bar_at = parse_aware_time(value.get("bar_at"), "bar_at")
    collected_at = parse_aware_time(value.get("collected_at"), "collected_at")
    if collected_at < bar_at:
        raise PriceBarError("collected_at cannot be earlier than bar_at")

    calendar = xcals.get_calendar("XNYS")
    session = pd.Timestamp(session_date.isoformat())
    if not calendar.is_session(session):
        raise PriceBarError("session_date is not an XNYS trading session")
    market_open = calendar.session_open(session).to_pydatetime()
    market_close = calendar.session_close(session).to_pydatetime()
    bar_at_utc = bar_at.astimezone(timezone.utc)
    if bar_type == "DAILY_CLOSE":
        if bar_at_utc != market_close:
            raise PriceBarError("DAILY_CLOSE bar_at must equal the XNYS session close")
    elif not market_open <= bar_at_utc <= market_close:
        raise PriceBarError("INTRADAY bar_at must fall within the XNYS regular session")
    return {
        "bar_id": bar_id,
        "ticker": ticker,
        "bar_at": bar_at.isoformat(),
        "session_date": session_date.isoformat(),
        "bar_type": bar_type,
        "close": format(close.normalize(), "f"),
        "source": source,
        "collected_at": collected_at.isoformat(),
    }


def canonical(value: dict[str, str]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def append(path: Path, bar: dict[str, str]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with path.open("r+", newline="", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        reader = csv.DictReader(handle)
        if reader.fieldnames:
            if reader.fieldnames != BAR_COLUMNS:
                raise PriceBarError("price bar ledger has unexpected columns")
            for row in reader:
                if row.get("bar_id") != bar["bar_id"]:
                    continue
                if canonical(row) != canonical(bar):
                    raise PriceBarError("bar_id already exists with different content")
                return "DUPLICATE_NOOP"
        else:
            handle.seek(0)
            writer = csv.DictWriter(handle, fieldnames=BAR_COLUMNS)
            writer.writeheader()
        handle.seek(0, os.SEEK_END)
        writer = csv.DictWriter(handle, fieldnames=BAR_COLUMNS)
        writer.writerow(bar)
        handle.flush()
        os.fsync(handle.fileno())
    return "APPENDED"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--ledger", default=data_path("outcome_price_bars.csv"))
    args = parser.parse_args()
    try:
        bar = normalize(json.loads(Path(args.input).read_text(encoding="utf-8")))
        status = append(Path(args.ledger), bar)
    except (OSError, json.JSONDecodeError, PriceBarError) as exc:
        print(f"price bar append failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": status, "bar_id": bar["bar_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
