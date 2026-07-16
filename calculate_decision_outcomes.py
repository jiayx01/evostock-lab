#!/usr/bin/env python3
"""Deterministically calculate 1h/close/1/5/20-session decision outcomes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from append_decision_event import DecisionEventError, event_time, normalize_event, validate_sequence
from append_outcome_price_bar import BAR_COLUMNS, PriceBarError, normalize as normalize_bar
from evostock_paths import data_path
from rebuild_holdings_from_broker_events import (
    ReconciliationError,
    TICKER_RE,
    resolve_committed_path,
    verify_commit_manifest,
)


ET = ZoneInfo("America/New_York")
XNYS = xcals.get_calendar("XNYS")
CALCULATOR_VERSION = "2.0.0"
HORIZONS = (("1h", 0), ("close", 0), ("1d", 1), ("5d", 5), ("20d", 20))
DEFAULT_EXPOSURE = {
    "继续持有": 1.0,
    "观望但提高警戒": 1.0,
}
OUTPUT_COLUMNS = [
    "decision_id",
    "ticker",
    "decision_at",
    "market_phase",
    "advice_action",
    "user_event_relation",
    "outcome_horizon",
    "outcome_status",
    "actual_return_pct",
    "hold_return_pct",
    "advice_counterfactual_return_pct",
    "actual_max_drawdown_pct",
    "hold_max_drawdown_pct",
    "transaction_cost_assumption",
    "reference_price",
    "reference_price_at",
    "end_price",
    "end_price_at",
    "price_source",
    "input_sha256",
    "calculated_at",
    "notes",
]


class OutcomeError(ValueError):
    pass


def parse_time(value: Any, field: str) -> datetime:
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise OutcomeError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise OutcomeError(f"{field} must include a timezone")
    return parsed


def number(value: Any, field: str, *, allow_zero: bool = False) -> float:
    try:
        result = float(Decimal(str(value).strip()))
    except (InvalidOperation, ValueError) as exc:
        raise OutcomeError(f"{field} must be numeric") from exc
    if result < 0 or (result == 0 and not allow_zero) or not math.isfinite(result):
        raise OutcomeError(f"{field} has an invalid value")
    return result


def optional_number(value: Any, field: str) -> float | None:
    if value is None or str(value).strip() in {"", "待确认"}:
        return None
    return number(value, field, allow_zero=True)


def sha256_inputs(
    paths: list[Path], as_of: datetime, transaction_cost_bps: float
) -> str:
    digest = hashlib.sha256()
    config = {
        "calculator_version": CALCULATOR_VERSION,
        "as_of": as_of.isoformat(),
        "transaction_cost_bps": transaction_cost_bps,
    }
    digest.update(json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for path in paths:
        digest.update(str(path.resolve()).encode("utf-8"))
        if path.exists():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing>")
    return digest.hexdigest()


def load_decisions(path: Path, as_of: datetime) -> tuple[list[dict[str, Any]], dict[str, datetime]]:
    if not path.exists():
        return [], {}
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = normalize_event(json.loads(line))
                validate_sequence(events, event)
            except (json.JSONDecodeError, DecisionEventError) as exc:
                raise OutcomeError(f"invalid decision log at line {line_number}") from exc
            events.append(event)
    visible = [event for event in events if event_time(event) <= as_of]
    created = [event for event in visible if event["event_type"] == "DECISION_CREATED"]
    delivered = {
        event["decision_id"]: event_time(event)
        for event in visible
        if event["event_type"] == "EMAIL_SENT"
    }
    return created, delivered


def load_bars(path: Path, as_of: datetime) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != BAR_COLUMNS:
            raise OutcomeError("outcome price bar ledger has unexpected columns")
        raw_rows = list(reader)
    rows: list[dict[str, Any]] = []
    seen_ids: dict[str, dict[str, str]] = {}
    seen_keys: dict[tuple[str, str, str], dict[str, str]] = {}
    for row_number, raw in enumerate(raw_rows, start=2):
        try:
            normalized = normalize_bar(raw)
        except PriceBarError as exc:
            raise OutcomeError(f"invalid price bar at row {row_number}") from exc
        if normalized["bar_id"] in seen_ids:
            if seen_ids[normalized["bar_id"]] != normalized:
                raise OutcomeError(f"conflicting bar_id at row {row_number}")
            continue
        key = (normalized["ticker"], normalized["bar_at"], normalized["bar_type"])
        if key in seen_keys and seen_keys[key] != normalized:
            raise OutcomeError(f"conflicting economic price bar at row {row_number}")
        seen_ids[normalized["bar_id"]] = normalized
        seen_keys[key] = normalized
        bar_at = parse_time(normalized["bar_at"], "bar_at")
        collected_at = parse_time(normalized["collected_at"], "collected_at")
        if bar_at > as_of or collected_at > as_of:
            continue
        rows.append(
            {
                **normalized,
                "bar_at_dt": bar_at,
                "collected_at_dt": collected_at,
                "close_value": number(normalized["close"], "close"),
            }
        )
    return rows


def load_broker_events(
    path: Path, as_of: datetime, ticker_aliases: dict[str, str] | None = None
) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "ticker",
            "event_type",
            "status",
            "side",
            "quantity",
            "price",
            "fees",
            "trade_time",
            "trade_time_source",
            "message_received_at",
            "parsed_at",
            "affects_position",
            "parse_confidence",
        }
        if not required.issubset(set(reader.fieldnames or [])):
            raise OutcomeError("broker event ledger is missing outcome fields")
        raw_rows = list(reader)
    aliases = {key.upper(): value.upper() for key, value in (ticker_aliases or {}).items()}
    rows: list[dict[str, str]] = []
    for row_number, row in enumerate(raw_rows, start=2):
        if str(row.get("affects_position") or "").strip().lower() not in {
            "true",
            "1",
            "yes",
        }:
            continue
        if str(row.get("parse_confidence") or "").strip().upper() != "CONFIRMED":
            continue
        if str(row.get("event_type") or "").strip().upper() != "TRADE":
            continue
        if str(row.get("status") or "").strip().upper() not in {
            "FILLED",
            "PARTIALLY_FILLED",
        }:
            continue
        trade_at = parse_time(row.get("trade_time"), f"trade_time row {row_number}")
        received_at = parse_time(
            row.get("message_received_at"), f"message_received_at row {row_number}"
        )
        parsed_at = parse_time(row.get("parsed_at"), f"parsed_at row {row_number}")
        if parsed_at < received_at:
            raise OutcomeError(f"broker event row {row_number} was parsed before receipt")
        if trade_at > as_of or received_at > as_of or parsed_at > as_of:
            continue
        normalized = dict(row)
        ticker = str(row.get("ticker") or "").strip().upper()
        normalized["ticker"] = aliases.get(ticker, ticker)
        rows.append(normalized)
    return rows


def target_bar(
    bars: list[dict[str, Any]],
    ticker: str,
    decision_at: datetime,
    horizon: str,
    session_offset: int,
    as_of: datetime,
) -> tuple[dict[str, Any] | None, str]:
    ticker_bars = [bar for bar in bars if bar["ticker"] == ticker]
    decision_session = pd.Timestamp(decision_at.astimezone(ET).date().isoformat())
    if not XNYS.is_session(decision_session):
        raise OutcomeError("decision_at is not within an XNYS trading session date")
    if horizon == "1h":
        target = decision_at + timedelta(hours=1)
        session_close = XNYS.session_close(decision_session).to_pydatetime()
        if target.astimezone(session_close.tzinfo) > session_close:
            return None, "NOT_APPLICABLE"
        if as_of < target:
            return None, "PENDING"
        candidates = sorted(
            (
                bar
                for bar in ticker_bars
                if bar["bar_type"] == "INTRADAY"
                and target - timedelta(minutes=15)
                <= bar["bar_at_dt"]
                <= target + timedelta(minutes=15)
            ),
            key=lambda bar: (
                abs((bar["bar_at_dt"] - target).total_seconds()),
                bar["bar_at_dt"] < target,
                bar["bar_at_dt"],
            ),
        )
        return (candidates[0], "MATURED") if candidates else (None, "PENDING_DATA")

    target_session_value = XNYS.session_offset(decision_session, session_offset)
    target_session = target_session_value.date().isoformat()
    target_close = XNYS.session_close(target_session_value).to_pydatetime()
    if as_of.astimezone(target_close.tzinfo) < target_close:
        return None, "PENDING"
    spy_close_present = any(
        bar["ticker"] == "SPY"
        and bar["bar_type"] == "DAILY_CLOSE"
        and bar["session_date"] == target_session
        for bar in bars
    )
    if not spy_close_present:
        return None, "PENDING_DATA"
    candidates = sorted(
        (
            bar
            for bar in ticker_bars
            if bar["bar_type"] == "DAILY_CLOSE" and bar["session_date"] == target_session
        ),
        key=lambda bar: bar["bar_at_dt"],
    )
    return (candidates[-1], "MATURED") if candidates else (None, "PENDING_DATA")


def max_drawdown_pct(prices: list[float]) -> float | None:
    if not prices:
        return None
    peak = prices[0]
    worst = 0.0
    for price in prices:
        peak = max(peak, price)
        if peak:
            worst = min(worst, price / peak - 1.0)
    return worst * 100.0


def exact_actual_result(
    broker_events: list[dict[str, str]],
    ticker: str,
    decision_at: datetime,
    end_at: datetime,
    reference_price: float,
    end_price: float,
    start_shares: float | None,
    path_bars: list[dict[str, Any]],
) -> tuple[float | None, float | None, str, str]:
    events: list[tuple[datetime, dict[str, str]]] = []
    for row in broker_events:
        if str(row.get("ticker") or "").strip().upper() != ticker:
            continue
        if str(row.get("affects_position") or "").strip().lower() not in {"true", "1", "yes"}:
            continue
        if str(row.get("parse_confidence") or "").strip().upper() != "CONFIRMED":
            continue
        if str(row.get("event_type") or "").strip().upper() != "TRADE":
            continue
        if str(row.get("status") or "").strip().upper() not in {"FILLED", "PARTIALLY_FILLED"}:
            continue
        trade_at = parse_time(row.get("trade_time"), "trade_time")
        if decision_at < trade_at <= end_at:
            events.append((trade_at, row))
    events.sort(key=lambda item: item[0])
    if not events:
        hold_return = (end_price / reference_price - 1.0) * 100.0
        path = [reference_price] + [bar["close_value"] for bar in path_bars] + [end_price]
        return hold_return, max_drawdown_pct(path), "无成交", ""
    if start_shares is None or start_shares <= 0:
        return None, None, "建议后成交", "起始股数待确认"
    for _, row in events:
        if str(row.get("trade_time_source") or "").strip().upper() != "BROKER_EXECUTION_TIME":
            return None, None, "建议后成交", "成交时间仅为通知代理，无法精确回测"
        if not str(row.get("fees") or "").strip():
            return None, None, "建议后成交", "成交费用缺失，无法精确回测"

    initial_value = start_shares * reference_price
    shares = start_shares
    cash = 0.0
    event_index = 0
    values = [initial_value]
    timeline = sorted(path_bars, key=lambda bar: bar["bar_at_dt"])
    for bar in timeline + [{"bar_at_dt": end_at, "close_value": end_price}]:
        while event_index < len(events) and events[event_index][0] <= bar["bar_at_dt"]:
            _, row = events[event_index]
            quantity = number(row.get("quantity"), "quantity")
            price = number(row.get("price"), "price")
            fees = number(row.get("fees"), "fees", allow_zero=True)
            side = str(row.get("side") or "").strip().upper()
            if side == "BUY":
                shares += quantity
                cash -= quantity * price + fees
            elif side == "SELL":
                if quantity > shares:
                    return None, None, "建议后成交", "回测期间卖出超过起始及新增股数"
                shares -= quantity
                cash += quantity * price - fees
            else:
                return None, None, "建议后成交", "回测期间出现不支持的成交方向"
            event_index += 1
        values.append(shares * bar["close_value"] + cash)
    actual_return = (values[-1] / initial_value - 1.0) * 100.0 if initial_value else None
    return actual_return, max_drawdown_pct(values), "建议后成交", ""


def fmt(value: float | None) -> str:
    return "" if value is None or not math.isfinite(value) else f"{value:.8f}"


def build_rows(
    decisions: list[dict[str, Any]],
    delivered: dict[str, datetime],
    bars: list[dict[str, Any]],
    broker_events: list[dict[str, str]],
    as_of: datetime,
    transaction_cost_bps: float,
    input_hash: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for decision in decisions:
        decision_id = decision["decision_id"]
        created_at = parse_time(decision["occurred_at"], "decision occurred_at")
        decision_at = delivered.get(decision_id, created_at)
        payload = decision["payload"]
        holdings = payload.get("holdings")
        if not isinstance(holdings, list) or not holdings:
            if str(payload.get("decision_kind") or "").upper() == "CANDIDATE_TRIGGER":
                continue
            raise OutcomeError(f"decision {decision_id} has no holdings outcome contract")
        for item in holdings:
            if not isinstance(item, dict):
                raise OutcomeError(f"decision {decision_id} has invalid holding payload")
            ticker = str(item.get("ticker") or "").strip().upper()
            if not TICKER_RE.fullmatch(ticker):
                raise OutcomeError(f"decision {decision_id} has invalid ticker")
            action = str(item.get("action") or "").strip()
            if not action:
                raise OutcomeError(f"decision {decision_id}/{ticker} has no action")
            reference_price = number(item.get("reference_price"), "reference_price")
            reference_at = parse_time(item.get("reference_price_at"), "reference_price_at")
            if reference_at > created_at:
                raise OutcomeError(f"decision {decision_id}/{ticker} uses a future reference price")
            start_shares = optional_number(item.get("shares"), "shares")
            exposure = optional_number(item.get("recommended_exposure"), "recommended_exposure")
            if exposure is None:
                exposure = DEFAULT_EXPOSURE.get(action)
            if exposure is not None and not 0 <= exposure <= 2:
                raise OutcomeError(f"decision {decision_id}/{ticker} exposure must be 0-2")

            for horizon, offset in HORIZONS:
                end_bar, maturity = target_bar(
                    bars,
                    ticker,
                    decision_at,
                    horizon,
                    offset,
                    as_of,
                )
                base = {
                    "decision_id": decision_id,
                    "ticker": ticker,
                    "decision_at": decision_at.isoformat(),
                    "market_phase": str(payload.get("market_phase") or "待确认"),
                    "advice_action": action,
                    "user_event_relation": "待成熟",
                    "outcome_horizon": horizon,
                    "outcome_status": maturity,
                    "actual_return_pct": "",
                    "hold_return_pct": "",
                    "advice_counterfactual_return_pct": "",
                    "actual_max_drawdown_pct": "",
                    "hold_max_drawdown_pct": "",
                    "transaction_cost_assumption": f"{transaction_cost_bps:.2f} bps on changed exposure",
                    "reference_price": fmt(reference_price),
                    "reference_price_at": reference_at.isoformat(),
                    "end_price": "",
                    "end_price_at": "",
                    "price_source": "",
                    "input_sha256": input_hash,
                    "calculated_at": as_of.isoformat(),
                    "notes": "邮件已送达" if decision_id in delivered else "邮件未送达，不纳入建议评分",
                }
                if decision_id not in delivered:
                    base["outcome_status"] = "NOT_DELIVERED"
                    rows.append(base)
                    continue
                if end_bar is None:
                    rows.append(base)
                    continue

                end_price = end_bar["close_value"]
                end_at = end_bar["bar_at_dt"]
                hold_return = (end_price / reference_price - 1.0) * 100.0
                path_bars = [
                    bar
                    for bar in bars
                    if bar["ticker"] == ticker
                    and reference_at < bar["bar_at_dt"] <= end_at
                ]
                hold_path = [reference_price] + [
                    bar["close_value"] for bar in sorted(path_bars, key=lambda x: x["bar_at_dt"])
                ]
                advice_return = None
                if exposure is not None:
                    cost_pct = abs(1.0 - exposure) * transaction_cost_bps / 100.0
                    advice_return = exposure * hold_return - cost_pct
                actual_return, actual_drawdown, relation, actual_note = exact_actual_result(
                    broker_events,
                    ticker,
                    decision_at,
                    end_at,
                    reference_price,
                    end_price,
                    start_shares,
                    path_bars,
                )
                status = "MATURED"
                notes = [base["notes"]]
                if exposure is None:
                    status = "MATURED_PARTIAL"
                    notes.append("动作缺少recommended_exposure，建议反事实不可计算")
                if actual_return is None:
                    status = "MATURED_PARTIAL"
                if actual_note:
                    notes.append(actual_note)
                base.update(
                    {
                        "user_event_relation": relation,
                        "outcome_status": status,
                        "actual_return_pct": fmt(actual_return),
                        "hold_return_pct": fmt(hold_return),
                        "advice_counterfactual_return_pct": fmt(advice_return),
                        "actual_max_drawdown_pct": fmt(actual_drawdown),
                        "hold_max_drawdown_pct": fmt(max_drawdown_pct(hold_path)),
                        "end_price": fmt(end_price),
                        "end_price_at": end_at.isoformat(),
                        "price_source": end_bar["source"],
                        "notes": "；".join(notes),
                    }
                )
                rows.append(base)
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        staged = Path(handle.name)
    os.replace(staged, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-log", default=data_path("decision_log.jsonl"))
    parser.add_argument("--price-bars", default=data_path("outcome_price_bars.csv"))
    parser.add_argument("--broker-events", default=data_path("broker_events.csv"))
    parser.add_argument("--profile", default=data_path("broker_email_profile.json"))
    parser.add_argument("--holdings", default=data_path("holdings_current.csv"))
    parser.add_argument(
        "--holdings-manifest", default=data_path("holdings_commit_manifest.json")
    )
    parser.add_argument("--output", default=data_path("decision_outcomes.csv"))
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--transaction-cost-bps", type=float, default=10.0)
    args = parser.parse_args()
    try:
        as_of = parse_time(args.as_of, "as_of")
        if args.transaction_cost_bps < 0:
            raise OutcomeError("transaction cost cannot be negative")
        manifest = verify_commit_manifest(Path(args.holdings_manifest), Path(args.holdings))
        committed_events_path = resolve_committed_path(
            manifest, "broker_events", Path(args.broker_events)
        )
        committed_profile_path = resolve_committed_path(
            manifest, "profile", Path(args.profile)
        )
        with committed_profile_path.open(encoding="utf-8") as handle:
            profile = json.load(handle)
        decisions, delivered = load_decisions(Path(args.decision_log), as_of)
        bars = load_bars(Path(args.price_bars), as_of)
        broker_events = load_broker_events(
            committed_events_path, as_of, profile.get("ticker_aliases") or {}
        )
        source_paths = [
            Path(args.decision_log),
            Path(args.price_bars),
            committed_events_path,
            committed_profile_path,
            Path(args.holdings_manifest),
        ]
        input_hash = sha256_inputs(source_paths, as_of, args.transaction_cost_bps)
        rows = build_rows(
            decisions,
            delivered,
            bars,
            broker_events,
            as_of,
            args.transaction_cost_bps,
            input_hash,
        )
        write_rows(Path(args.output), rows)
    except (OSError, json.JSONDecodeError, DecisionEventError, ReconciliationError, OutcomeError) as exc:
        print(f"decision outcome calculation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "SUCCESS", "rows": len(rows), "input_sha256": input_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
