#!/usr/bin/env python3
"""Rebuild current long-only US stock holdings from verified broker executions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from evostock_paths import data_path


HOLDING_COLUMNS = [
    "date",
    "ticker",
    "company_name",
    "position_type",
    "shares",
    "avg_cost",
    "last_price",
    "market_value",
    "portfolio_weight_pct",
    "entry_date",
    "entry_reason",
    "core_thesis",
    "expected_holding_period",
    "target_allocation_pct",
    "max_allocation_pct",
    "stop_loss_pct",
    "trim_profit_pct",
    "add_rule",
    "trim_rule",
    "thesis_break_rule",
    "next_earnings_date",
    "key_risks",
    "latest_action",
    "notes",
]

BROKER_EVENT_COLUMNS = [
    "source_account",
    "source_message_id",
    "source_thread_id",
    "message_received_at",
    "sender",
    "subject",
    "content_hash",
    "parser_version",
    "broker",
    "account_ref",
    "order_id",
    "execution_id",
    "event_type",
    "status",
    "side",
    "ticker",
    "company_name",
    "exchange",
    "security_id",
    "quantity",
    "price",
    "fees",
    "currency",
    "trade_time",
    "trade_time_source",
    "settlement_date",
    "affects_position",
    "parse_confidence",
    "supersedes_execution_id",
    "parsed_at",
    "notes",
]

BROKER_QUARANTINE_COLUMNS = [
    "source_account",
    "source_message_id",
    "source_thread_id",
    "message_received_at",
    "sender",
    "subject",
    "reason",
    "resolution_status",
    "parsed_at",
    "notes",
]

BROKER_MESSAGE_INDEX_COLUMNS = [
    "index_event_id",
    "batch_id",
    "source_account",
    "source_message_id",
    "source_thread_id",
    "message_received_at",
    "sender",
    "subject",
    "content_hash",
    "parser_version",
    "lifecycle_type",
    "normalized_event_count",
    "processing_status",
    "processed_at",
    "notes",
]

HOLDINGS_ANCHOR_COLUMNS = [
    "anchor_at",
    "source",
    "account_ref",
    "ticker",
    "company_name",
    "shares",
    "avg_cost",
    "entry_date",
    "notes",
]

REQUIRED_EVENT_COLUMNS = {
    "source_account",
    "source_message_id",
    "message_received_at",
    "sender",
    "subject",
    "execution_id",
    "event_type",
    "status",
    "side",
    "ticker",
    "company_name",
    "account_ref",
    "quantity",
    "price",
    "fees",
    "currency",
    "trade_time",
    "trade_time_source",
    "affects_position",
    "parse_confidence",
    "parsed_at",
}

REQUIRED_QUARANTINE_COLUMNS = {"source_message_id", "reason", "resolution_status"}
REQUIRED_MESSAGE_INDEX_COLUMNS = {
    "source_message_id",
    "message_received_at",
    "processing_status",
}
REQUIRED_ANCHOR_COLUMNS = {
    "anchor_at",
    "source",
    "account_ref",
    "ticker",
    "company_name",
    "shares",
    "avg_cost",
    "entry_date",
}

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
APPLIED_STATUSES = {"FILLED", "PARTIALLY_FILLED"}


class ReconciliationError(ValueError):
    pass


@dataclass
class Position:
    quantity: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    company_name: str = ""
    entry_date: str = ""
    cost_known: bool = True
    fees_complete: bool = True

    @property
    def avg_cost(self) -> Decimal | None:
        if self.quantity == 0 or not self.cost_known:
            return None
        return self.total_cost / self.quantity


def parse_decimal(value: str, field: str, row_number: int, *, allow_zero: bool) -> Decimal:
    try:
        result = Decimal((value or "").strip())
    except InvalidOperation as exc:
        raise ReconciliationError(f"row {row_number}: invalid {field}") from exc
    if result < 0 or (result == 0 and not allow_zero):
        rule = "non-negative" if allow_zero else "positive"
        raise ReconciliationError(f"row {row_number}: {field} must be {rule}")
    return result


def parse_bool(value: str, field: str, row_number: int) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ReconciliationError(f"row {row_number}: invalid {field}")


def parse_time(value: str, field: str, row_number: int) -> datetime:
    raw = (value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ReconciliationError(f"row {row_number}: invalid {field}") from exc
    if parsed.tzinfo is None:
        raise ReconciliationError(f"row {row_number}: {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def normalized_sender(value: str) -> str:
    raw = (value or "").strip().lower()
    match = re.search(r"<([^<>]+)>", raw)
    return (match.group(1) if match else raw).strip()


def decimal_text(value: Decimal, places: int = 8) -> str:
    quantized = value.quantize(Decimal(1).scaleb(-places))
    return format(quantized.normalize(), "f")


def load_profile(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        profile = json.load(handle)
    if profile.get("profile_status") != "CONFIRMED":
        raise ReconciliationError("broker email profile is not CONFIRMED")
    target = str(profile.get("target_account", "")).strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", target):
        raise ReconciliationError("target email account is invalid")
    if not profile.get("bootstrap_completed_at"):
        raise ReconciliationError("broker email history bootstrap is incomplete")
    senders = profile.get("confirmed_senders") or []
    if not senders:
        raise ReconciliationError("confirmed sender whitelist is empty")
    subject_patterns = profile.get("confirmed_subject_patterns") or []
    if not subject_patterns:
        raise ReconciliationError("confirmed subject patterns are empty")
    try:
        for pattern in subject_patterns:
            re.compile(str(pattern), re.IGNORECASE)
    except re.error as exc:
        raise ReconciliationError("confirmed subject pattern is invalid") from exc
    if not profile.get("confirmed_execution_terms"):
        raise ReconciliationError("confirmed execution terms are empty")
    if profile.get("confirmed_timezone") in {None, "", "PENDING"}:
        raise ReconciliationError("broker email timezone is not confirmed")
    mode = profile.get("bootstrap_mode")
    if mode == "FULL_HISTORY":
        if not profile.get("bootstrap_oldest_message_at"):
            raise ReconciliationError("full-history bootstrap has no oldest message timestamp")
        event_count = profile.get("bootstrap_event_count")
        if not isinstance(event_count, int) or event_count < 1:
            raise ReconciliationError("full-history bootstrap event count is invalid")
    elif mode in {"EXACT_USER_ANCHOR", "VERIFIED_POSITION_ANCHOR"}:
        if not profile.get("anchor_at"):
            raise ReconciliationError("exact-anchor bootstrap has no anchor timestamp")
    else:
        raise ReconciliationError("unsupported bootstrap mode")
    return profile


def load_events(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_EVENT_COLUMNS - columns)
        if missing:
            raise ReconciliationError(f"event ledger missing columns: {', '.join(missing)}")
        return list(reader), list(reader.fieldnames or [])


def ensure_no_unresolved_quarantine(path: Path) -> None:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_QUARANTINE_COLUMNS - columns)
        if missing:
            raise ReconciliationError(f"quarantine ledger missing columns: {', '.join(missing)}")
        latest: dict[str, tuple[int, dict[str, str]]] = {}
        for row_number, row in enumerate(reader, start=2):
            message_id = (row.get("source_message_id") or "").strip()
            latest[message_id or f"__row_{row_number}"] = (row_number, row)
        unresolved = []
        for row_number, row in latest.values():
            status = (row.get("resolution_status") or "").strip().upper()
            if status not in {"RESOLVED", "FALSE_POSITIVE"}:
                unresolved.append((row_number, (row.get("source_message_id") or "").strip()))
    if unresolved:
        sample = ", ".join(f"row {row_number}:{message_id or 'unknown'}" for row_number, message_id in unresolved[:3])
        raise ReconciliationError(f"unresolved quarantined broker emails: {sample}")


def ensure_anchor_evidence(path: Path, profile: dict[str, Any]) -> None:
    if profile.get("bootstrap_mode") != "VERIFIED_POSITION_ANCHOR":
        return
    expected_ids = {str(item).strip() for item in profile.get("anchor_evidence_message_ids") or []}
    if not expected_ids or "" in expected_ids:
        raise ReconciliationError("verified position anchor has no evidence message IDs")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_MESSAGE_INDEX_COLUMNS - columns)
        if missing:
            raise ReconciliationError(f"message index missing columns: {', '.join(missing)}")
        latest: dict[str, tuple[int, dict[str, str]]] = {}
        bootstrap_completed_at = parse_time(
            str(profile.get("bootstrap_completed_at") or ""), "bootstrap_completed_at", 0
        )
        for row_number, row in enumerate(reader, start=2):
            message_id = (row.get("source_message_id") or "").strip()
            if message_id not in expected_ids:
                continue
            latest[message_id] = (row_number, row)
        found = set()
        for message_id, (row_number, row) in latest.items():
            if (row.get("processing_status") or "").strip().upper() != "ANCHOR_EVIDENCE":
                raise ReconciliationError(f"row {row_number}: anchor evidence has wrong status")
            message_time = parse_time(
                row.get("message_received_at") or "", "message_received_at", row_number
            )
            if message_time > bootstrap_completed_at:
                raise ReconciliationError(f"row {row_number}: anchor evidence is after bootstrap")
            found.add(message_id)
    missing_ids = sorted(expected_ids - found)
    if missing_ids:
        raise ReconciliationError(
            f"message index is missing anchor evidence: {', '.join(missing_ids[:3])}"
        )


def load_anchor(path: Path, profile: dict[str, Any]) -> tuple[dict[tuple[str, str], Position], datetime | None]:
    if profile.get("bootstrap_mode") == "FULL_HISTORY":
        return {}, None

    expected_at = parse_time(str(profile.get("anchor_at") or ""), "anchor_at", 0)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_ANCHOR_COLUMNS - columns)
        if missing:
            raise ReconciliationError(f"anchor file missing columns: {', '.join(missing)}")
        rows = list(reader)
    if not rows:
        raise ReconciliationError("exact-anchor mode requires at least one anchor position")

    positions: dict[tuple[str, str], Position] = {}
    for row_number, row in enumerate(rows, start=2):
        row_at = parse_time(row.get("anchor_at") or "", "anchor_at", row_number)
        if row_at != expected_at:
            raise ReconciliationError(f"row {row_number}: anchor timestamp conflicts with profile")
        ticker = (row.get("ticker") or "").strip().upper()
        if not TICKER_RE.fullmatch(ticker):
            raise ReconciliationError(f"row {row_number}: invalid anchor ticker")
        quantity = parse_decimal(row.get("shares") or "", "shares", row_number, allow_zero=False)
        source = (row.get("source") or "").strip()
        mode = profile.get("bootstrap_mode")
        if mode == "EXACT_USER_ANCHOR" and source != "user_chat":
            raise ReconciliationError(f"row {row_number}: exact user anchor must use source=user_chat")
        if mode == "VERIFIED_POSITION_ANCHOR" and source != "gmail_full_mailbox_verification":
            raise ReconciliationError(
                f"row {row_number}: verified position anchor has an unapproved source"
            )
        raw_avg_cost = (row.get("avg_cost") or "").strip()
        if mode == "EXACT_USER_ANCHOR" and not raw_avg_cost:
            raise ReconciliationError(f"row {row_number}: exact user anchor requires avg_cost")
        avg_cost = (
            parse_decimal(raw_avg_cost, "avg_cost", row_number, allow_zero=False)
            if raw_avg_cost
            else None
        )
        account_ref = (row.get("account_ref") or "DEFAULT").strip() or "DEFAULT"
        key = (account_ref, ticker)
        if key in positions:
            raise ReconciliationError(f"row {row_number}: duplicate anchor position")
        entry_date = (row.get("entry_date") or "").strip()
        try:
            datetime.fromisoformat(entry_date)
        except ValueError as exc:
            raise ReconciliationError(f"row {row_number}: invalid anchor entry_date") from exc
        positions[key] = Position(
            quantity=quantity,
            total_cost=quantity * avg_cost if avg_cost is not None else Decimal("0"),
            company_name=(row.get("company_name") or "").strip(),
            entry_date=entry_date,
            cost_known=avg_cost is not None,
            fees_complete=False,
        )
    return positions, expected_at


def stable_event_signature(row: dict[str, str]) -> tuple[str, ...]:
    fields = (
        "source_account",
        "event_type",
        "status",
        "side",
        "ticker",
        "account_ref",
        "quantity",
        "price",
        "fees",
        "currency",
        "trade_time",
        "trade_time_source",
        "affects_position",
        "parse_confidence",
    )
    return tuple((row.get(field) or "").strip() for field in fields)


def reconcile(
    profile: dict[str, Any],
    events: list[dict[str, str]],
    as_of: datetime | None,
    initial_positions: dict[tuple[str, str], Position] | None = None,
    anchor_at: datetime | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    cutoff = as_of or datetime.now(timezone.utc)
    live_mode = as_of is None
    clock_skew = timedelta(minutes=5)
    target = str(profile["target_account"]).strip().lower()
    sender_whitelist = {normalized_sender(item) for item in profile["confirmed_senders"]}
    subject_patterns = [re.compile(str(item), re.IGNORECASE) for item in profile["confirmed_subject_patterns"]]
    ticker_aliases = {
        str(source).strip().upper(): str(target).strip().upper()
        for source, target in (profile.get("ticker_aliases") or {}).items()
    }
    if profile.get("bootstrap_mode") == "FULL_HISTORY" and len(events) < profile["bootstrap_event_count"]:
        raise ReconciliationError("event ledger has fewer rows than the completed full-history bootstrap")
    if as_of and anchor_at and as_of < anchor_at:
        raise ReconciliationError("as_of is earlier than the exact user anchor")
    seen: dict[tuple[str, str], tuple[str, ...]] = {}
    prepared: list[tuple[datetime, int, dict[str, str]]] = []
    ignored = 0

    for row_number, row in enumerate(events, start=2):
        message_id = (row.get("source_message_id") or "").strip()
        execution_id = (row.get("execution_id") or "").strip()
        if not message_id or not execution_id:
            raise ReconciliationError(f"row {row_number}: missing deduplication identity")

        if (row.get("source_account") or "").strip().lower() != target:
            raise ReconciliationError(f"row {row_number}: event came from a different Gmail account")
        if normalized_sender(row.get("sender") or "") not in sender_whitelist:
            raise ReconciliationError(f"row {row_number}: sender is not whitelisted")
        subject = (row.get("subject") or "").strip()
        if not any(pattern.search(subject) for pattern in subject_patterns):
            raise ReconciliationError(f"row {row_number}: subject does not match a confirmed pattern")

        affects_position = parse_bool(row.get("affects_position") or "", "affects_position", row_number)
        if not affects_position:
            ignored += 1
            continue
        if (row.get("parse_confidence") or "").strip().upper() != "CONFIRMED":
            raise ReconciliationError(f"row {row_number}: position event is not CONFIRMED")
        event_type = (row.get("event_type") or "").strip().upper()
        status = (row.get("status") or "").strip().upper()
        if event_type == "TRADE" and status not in APPLIED_STATUSES:
            raise ReconciliationError(f"row {row_number}: unsupported position event status")
        if event_type == "STOCK_REWARD" and status != "CREDITED":
            raise ReconciliationError(f"row {row_number}: unsupported stock reward status")
        if event_type not in {"TRADE", "STOCK_REWARD"}:
            raise ReconciliationError(f"row {row_number}: unsupported position event type")

        trade_time = parse_time(row.get("trade_time") or "", "trade_time", row_number)
        trade_time_source = (row.get("trade_time_source") or "").strip().upper()
        if trade_time_source not in {"BROKER_EXECUTION_TIME", "NOTIFICATION_TIME_PROXY"}:
            raise ReconciliationError(f"row {row_number}: unsupported trade_time_source")
        message_time = parse_time(
            row.get("message_received_at") or "", "message_received_at", row_number
        )
        if trade_time_source == "NOTIFICATION_TIME_PROXY":
            if abs((trade_time - message_time).total_seconds()) > 1:
                raise ReconciliationError(
                    f"row {row_number}: notification time proxy must equal message_received_at"
                )
        elif trade_time > message_time + clock_skew:
            raise ReconciliationError(
                f"row {row_number}: broker execution time is after the notification time"
            )

        if live_mode and (trade_time > cutoff + clock_skew or message_time > cutoff + clock_skew):
            raise ReconciliationError(f"row {row_number}: future-dated broker event")

        account_ref = (row.get("account_ref") or "DEFAULT").strip() or "DEFAULT"
        key = (account_ref, execution_id)
        signature = stable_event_signature(row)
        if key in seen:
            if seen[key] != signature:
                raise ReconciliationError(f"row {row_number}: conflicting duplicate event {key}")
            ignored += 1
            continue
        seen[key] = signature

        if anchor_at and trade_time <= anchor_at:
            ignored += 1
            continue
        if trade_time > cutoff or message_time > cutoff:
            ignored += 1
            continue
        prepared.append((trade_time, row_number, row))

    prepared.sort(key=lambda item: (item[0], item[1]))
    positions = {
        key: Position(
            quantity=value.quantity,
            total_cost=value.total_cost,
            company_name=value.company_name,
            entry_date=value.entry_date,
            cost_known=value.cost_known,
            fees_complete=value.fees_complete,
        )
        for key, value in (initial_positions or {}).items()
    }
    realized_pnl = Decimal("0")
    realized_pnl_complete = True
    applied = 0

    for trade_time, row_number, row in prepared:
        side = (row.get("side") or "").strip().upper()
        event_type = (row.get("event_type") or "").strip().upper()
        allowed_sides = {"CREDIT"} if event_type == "STOCK_REWARD" else {"BUY", "SELL"}
        if side not in allowed_sides:
            raise ReconciliationError(f"row {row_number}: unsupported side")
        raw_ticker = (row.get("ticker") or "").strip().upper()
        ticker = ticker_aliases.get(raw_ticker, raw_ticker)
        if not TICKER_RE.fullmatch(ticker):
            raise ReconciliationError(f"row {row_number}: invalid ticker")
        currency = (row.get("currency") or "").strip().upper()
        if currency != "USD":
            raise ReconciliationError(f"row {row_number}: non-USD trade requires explicit FX handling")

        quantity = parse_decimal(row.get("quantity") or "", "quantity", row_number, allow_zero=False)
        account_ref = (row.get("account_ref") or "DEFAULT").strip() or "DEFAULT"
        key = (account_ref, ticker)
        position = positions.setdefault(key, Position())

        if event_type == "STOCK_REWARD":
            if position.quantity == 0:
                position.entry_date = trade_time.date().isoformat()
            position.quantity += quantity
            position.cost_known = False
            position.fees_complete = False
            company_name = (row.get("company_name") or "").strip()
            if company_name:
                position.company_name = company_name
            applied += 1
            continue

        price = parse_decimal(row.get("price") or "", "price", row_number, allow_zero=False)
        raw_fees = (row.get("fees") or "").strip()
        fees = parse_decimal(raw_fees or "0", "fees", row_number, allow_zero=True)
        position.fees_complete = position.fees_complete and bool(raw_fees)

        if side == "BUY":
            if position.quantity == 0:
                position.entry_date = trade_time.date().isoformat()
            if position.cost_known:
                position.total_cost += quantity * price + fees
            position.quantity += quantity
        else:
            if quantity > position.quantity:
                raise ReconciliationError(
                    f"row {row_number}: sell quantity exceeds known position for {account_ref}/{ticker}"
                )
            if position.avg_cost is None:
                realized_pnl_complete = False
                position.total_cost = Decimal("0")
            else:
                realized_pnl += quantity * (price - position.avg_cost) - fees
                position.total_cost -= quantity * position.avg_cost
            if not raw_fees:
                realized_pnl_complete = False
            position.quantity -= quantity
            if position.quantity == 0:
                position.total_cost = Decimal("0")
                position.entry_date = ""
                position.cost_known = True
                position.fees_complete = True

        company_name = (row.get("company_name") or "").strip()
        if company_name:
            position.company_name = company_name
        applied += 1

    consolidated: dict[str, Position] = {}
    for (_, ticker), position in positions.items():
        if position.quantity == 0:
            continue
        target_position = consolidated.setdefault(ticker, Position())
        target_position.quantity += position.quantity
        target_position.total_cost += position.total_cost
        target_position.cost_known = target_position.cost_known and position.cost_known
        target_position.fees_complete = target_position.fees_complete and position.fees_complete
        target_position.company_name = position.company_name or target_position.company_name
        if position.entry_date and (
            not target_position.entry_date or position.entry_date < target_position.entry_date
        ):
            target_position.entry_date = position.entry_date

    output_date = cutoff.date().isoformat()
    holding_rows: list[dict[str, str]] = []
    for ticker in sorted(consolidated):
        position = consolidated[ticker]
        note_parts = ["由已核验ZA Bank成交邮件账本或持仓锚点重建", "未从旧持仓继承未提供字段"]
        if not position.cost_known:
            note_parts.append("平均成本待确认")
        elif not position.fees_complete:
            note_parts.append("平均成本未计邮件中缺失的费用")
        note_parts.append(f"截至{cutoff.isoformat()}")
        row = {column: "" for column in HOLDING_COLUMNS}
        row.update(
            {
                "date": output_date,
                "ticker": ticker,
                "company_name": position.company_name or "待确认",
                "position_type": "stock",
                "shares": decimal_text(position.quantity),
                "avg_cost": decimal_text(position.avg_cost) if position.avg_cost is not None else "",
                "entry_date": position.entry_date or "待确认",
                "entry_reason": "待确认",
                "core_thesis": "待确认",
                "next_earnings_date": "待确认",
                "key_risks": "待确认",
                "latest_action": "待确认",
                "notes": "；".join(note_parts),
            }
        )
        holding_rows.append(row)

    audit = {
        "status": "SUCCESS",
        "target_account": target,
        "as_of": cutoff.isoformat(),
        "input_rows": len(events),
        "applied_execution_rows": applied,
        "ignored_or_duplicate_rows": ignored,
        "active_tickers": [row["ticker"] for row in holding_rows],
        "realized_pnl_usd_from_ledger": (
            decimal_text(realized_pnl) if realized_pnl_complete else None
        ),
        "unknown_cost_tickers": [
            ticker for ticker, position in consolidated.items() if not position.cost_known
        ],
        "fee_incomplete_tickers": [
            ticker for ticker, position in consolidated.items() if not position.fees_complete
        ],
        "profile_parser_version": profile.get("parser_version"),
        "bootstrap_mode": profile.get("bootstrap_mode"),
        "anchor_at": anchor_at.isoformat() if anchor_at else None,
    }
    return holding_rows, audit


def stage_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=HOLDING_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(handle.name)
    return temp_path


def stage_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    return temp_path


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore_file(path: Path, existed: bool, content: bytes | None) -> None:
    if not existed:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(content or b"")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def commit_outputs(
    output_path: Path,
    holding_rows: list[dict[str, str]],
    audit_path: Path,
    audit: dict[str, Any],
    manifest_path: Path,
    source_inputs: dict[str, Path] | None = None,
) -> None:
    transaction_id = str(audit.get("transaction_id") or "").strip()
    if not transaction_id:
        raise ReconciliationError("audit has no transaction_id")
    output_existed = output_path.exists()
    audit_existed = audit_path.exists()
    manifest_existed = manifest_path.exists()
    output_before = output_path.read_bytes() if output_existed else None
    audit_before = audit_path.read_bytes() if audit_existed else None
    manifest_before = manifest_path.read_bytes() if manifest_existed else None
    staged_output = stage_csv(output_path, holding_rows)
    staged_audit = stage_json(audit_path, audit)
    manifest = {
        "manifest_version": 2,
        "transaction_id": transaction_id,
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "holdings_path": str(output_path.resolve()),
        "holdings_sha256": sha256_path(staged_output),
        "audit_path": str(audit_path.resolve()),
        "audit_sha256": sha256_path(staged_audit),
        "source_inputs": {
            name: {"path": str(path.resolve()), "sha256": sha256_path(path)}
            for name, path in sorted((source_inputs or {}).items())
        },
    }
    staged_manifest = stage_json(manifest_path, manifest)
    try:
        os.replace(staged_output, output_path)
        os.replace(staged_audit, audit_path)
        os.replace(staged_manifest, manifest_path)
    except OSError as exc:
        staged_output.unlink(missing_ok=True)
        staged_audit.unlink(missing_ok=True)
        staged_manifest.unlink(missing_ok=True)
        try:
            restore_file(output_path, output_existed, output_before)
            restore_file(audit_path, audit_existed, audit_before)
            restore_file(manifest_path, manifest_existed, manifest_before)
        except OSError as rollback_exc:
            raise OSError(f"output commit failed and rollback also failed: {rollback_exc}") from exc
        raise


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _paths_equivalent(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return _absolute_without_resolving(left) == _absolute_without_resolving(right)


def _verify_hashed_file(item: Any, label: str, generation_path: Path | None = None) -> Path:
    if not isinstance(item, dict):
        raise ReconciliationError(f"commit manifest entry is invalid: {label}")
    path = Path(str(item.get("path") or ""))
    if not path.is_file() or sha256_path(path) != item.get("sha256"):
        raise ReconciliationError(f"committed file hash does not match: {label}")
    if generation_path is not None:
        try:
            path.resolve().relative_to(generation_path.resolve())
        except ValueError as exc:
            raise ReconciliationError(
                f"committed file is outside the immutable generation: {label}"
            ) from exc
    return path


def resolve_committed_path(
    manifest: dict[str, Any], name: str, logical_fallback: Path
) -> Path:
    version = manifest.get("manifest_version")
    aliases = {
        "broker_events": "events",
        "broker_event_quarantine": "quarantine",
        "broker_message_index": "message_index",
        "broker_sync_state": "sync_state",
    }
    if version == 3:
        logical_paths = manifest.get("logical_paths") or {}
        expected = logical_paths.get(name)
        if expected and not _paths_equivalent(Path(str(expected)), logical_fallback):
            raise ReconciliationError(f"logical path differs from commit manifest: {name}")
        if name == "holdings":
            return Path(str(manifest.get("holdings_path") or ""))
        if name == "audit":
            return Path(str(manifest.get("audit_path") or ""))
        transaction_files = manifest.get("transaction_files") or {}
        if name in transaction_files:
            return Path(str(transaction_files[name].get("path") or ""))
        source_inputs = manifest.get("source_inputs") or {}
        if name in source_inputs:
            return Path(str(source_inputs[name].get("path") or ""))
        raise ReconciliationError(f"commit manifest has no file: {name}")

    source_inputs = manifest.get("source_inputs") or {}
    legacy_name = aliases.get(name, name)
    if legacy_name in source_inputs:
        return Path(str(source_inputs[legacy_name].get("path") or ""))
    if name == "holdings":
        return Path(str(manifest.get("holdings_path") or logical_fallback))
    if name == "audit":
        return Path(str(manifest.get("audit_path") or logical_fallback))
    return logical_fallback


def verify_commit_manifest(
    manifest_path: Path, output_path: Path, *, allow_blocked: bool = False
) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconciliationError("holdings commit manifest is missing or invalid") from exc
    version = manifest.get("manifest_version")
    if version == 3:
        generation_path = Path(str(manifest.get("generation_path") or ""))
        if not generation_path.is_dir():
            raise ReconciliationError("immutable broker generation is missing")
        logical_paths = manifest.get("logical_paths")
        if not isinstance(logical_paths, dict):
            raise ReconciliationError("v3 manifest has no logical paths")
        logical_holdings = Path(str(logical_paths.get("holdings") or ""))
        if not _paths_equivalent(logical_holdings, output_path):
            raise ReconciliationError("holdings logical path does not match commit manifest")

        holdings_path = _verify_hashed_file(
            {
                "path": manifest.get("holdings_path"),
                "sha256": manifest.get("holdings_sha256"),
            },
            "holdings",
            generation_path,
        )
        audit_path = _verify_hashed_file(
            {"path": manifest.get("audit_path"), "sha256": manifest.get("audit_sha256")},
            "audit",
            generation_path,
        )
        transaction_files = manifest.get("transaction_files")
        required_transaction_files = {
            "broker_events",
            "broker_event_quarantine",
            "broker_message_index",
            "broker_sync_state",
        }
        if not isinstance(transaction_files, dict) or not required_transaction_files.issubset(
            transaction_files
        ):
            raise ReconciliationError("v3 manifest does not bind the complete broker transaction")
        verified_transaction_paths = {
            name: _verify_hashed_file(transaction_files[name], name, generation_path)
            for name in required_transaction_files
        }
        source_inputs = manifest.get("source_inputs")
        if not isinstance(source_inputs, dict) or not {"profile", "anchor"}.issubset(
            source_inputs
        ):
            raise ReconciliationError("v3 manifest does not bind profile and anchor inputs")
        for name in ("profile", "anchor"):
            _verify_hashed_file(source_inputs[name], name)
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            sync_state = json.loads(
                verified_transaction_paths["broker_sync_state"].read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise ReconciliationError("committed v3 JSON artifact is invalid") from exc
        transaction_id = str(manifest.get("transaction_id") or "")
        if not transaction_id or audit.get("transaction_id") != transaction_id:
            raise ReconciliationError("transaction_id differs between manifest and audit")
        if sync_state.get("last_transaction_id") != transaction_id:
            raise ReconciliationError("sync state transaction_id differs from manifest")
        commit_status = str(manifest.get("commit_status") or "").upper()
        if commit_status not in {"SUCCESS", "BLOCKED"}:
            raise ReconciliationError("v3 manifest has invalid commit status")
        if str(audit.get("status") or "").upper() != commit_status:
            raise ReconciliationError("audit status differs from manifest commit status")
        target_account = str(manifest.get("target_account") or "").strip().lower()
        if not target_account or str(sync_state.get("target_account") or "").strip().lower() != target_account:
            raise ReconciliationError("sync state target account differs from manifest")
        if str(sync_state.get("last_commit_status") or "").upper() != commit_status:
            raise ReconciliationError("sync state commit status differs from manifest")
        audit_waterline = audit.get("waterline_after")
        if audit_waterline is not None and str(sync_state.get("last_verified_history_id") or "") != str(
            audit_waterline
        ):
            raise ReconciliationError("sync state waterline differs from audit")
        scan = manifest.get("scan")
        if commit_status == "SUCCESS" and isinstance(scan, dict):
            if str(sync_state.get("last_verified_history_id") or "") != str(
                scan.get("history_id_after") or ""
            ):
                raise ReconciliationError("successful sync waterline differs from scan evidence")
            state_scan_at = parse_time(
                str(sync_state.get("last_successful_scan_at") or ""),
                "last_successful_scan_at",
                0,
            )
            manifest_scan_at = parse_time(
                str(scan.get("completed_at") or ""), "scan.completed_at", 0
            )
            if state_scan_at != manifest_scan_at:
                raise ReconciliationError("successful scan timestamp differs from sync state")
        if commit_status == "BLOCKED" and not allow_blocked:
            raise ReconciliationError("latest broker sync transaction is BLOCKED")
        if not holdings_path.is_file():
            raise ReconciliationError("committed holdings file is missing")
        return manifest

    if version != 2:
        raise ReconciliationError("unsupported holdings commit manifest version")
    if Path(str(manifest.get("holdings_path") or "")).resolve() != output_path.resolve():
        raise ReconciliationError("holdings path does not match commit manifest")
    if sha256_path(output_path) != manifest.get("holdings_sha256"):
        raise ReconciliationError("holdings hash does not match commit manifest")
    audit_path = Path(str(manifest.get("audit_path") or ""))
    if not audit_path.is_file() or sha256_path(audit_path) != manifest.get("audit_sha256"):
        raise ReconciliationError("audit hash does not match commit manifest")
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReconciliationError("committed audit is invalid JSON") from exc
    if not manifest.get("transaction_id") or audit.get("transaction_id") != manifest.get(
        "transaction_id"
    ):
        raise ReconciliationError("transaction_id differs between manifest and audit")
    if str(audit.get("status") or "").upper() != "SUCCESS":
        raise ReconciliationError("committed reconciliation audit is not SUCCESS")
    source_inputs = manifest.get("source_inputs")
    if not isinstance(source_inputs, dict) or not source_inputs:
        raise ReconciliationError("commit manifest has no source input hashes")
    for name, item in source_inputs.items():
        if not isinstance(item, dict):
            raise ReconciliationError(f"source input manifest is invalid: {name}")
        source_path = Path(str(item.get("path") or ""))
        if not source_path.is_file() or sha256_path(source_path) != item.get("sha256"):
            raise ReconciliationError(f"source input hash does not match: {name}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=data_path("broker_email_profile.json"))
    parser.add_argument("--events", default=data_path("broker_events.csv"))
    parser.add_argument("--quarantine", default=data_path("broker_event_quarantine.csv"))
    parser.add_argument("--message-index", default=data_path("broker_message_index.csv"))
    parser.add_argument("--anchor", default=data_path("holdings_anchor.csv"))
    parser.add_argument("--output", default=data_path("holdings_current.csv"))
    parser.add_argument(
        "--audit", default=data_path("reports", "latest_holdings_reconciliation.json")
    )
    parser.add_argument("--manifest", default=data_path("holdings_commit_manifest.json"))
    parser.add_argument("--as-of", help="ISO-8601 timestamp with timezone; events after it are ignored")
    args = parser.parse_args()

    try:
        profile_path = Path(args.profile)
        events_path = Path(args.events)
        quarantine_path = Path(args.quarantine)
        anchor_path = Path(args.anchor)
        if Path(args.output).is_symlink() or Path(args.manifest).is_symlink():
            raise ReconciliationError(
                "atomic v3 layout requires commit_broker_sync_batch.py; standalone rebuild is disabled"
            )
        profile = load_profile(profile_path)
        events, _ = load_events(events_path)
        ensure_no_unresolved_quarantine(quarantine_path)
        ensure_anchor_evidence(Path(args.message_index), profile)
        initial_positions, anchor_at = load_anchor(anchor_path, profile)
        as_of = parse_time(args.as_of, "as_of", 0) if args.as_of else None
        holdings, audit = reconcile(profile, events, as_of, initial_positions, anchor_at)
        audit["transaction_id"] = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}"
        commit_outputs(
            Path(args.output),
            holdings,
            Path(args.audit),
            audit,
            Path(args.manifest),
            {
                "profile": profile_path,
                "events": events_path,
                "quarantine": quarantine_path,
                "anchor": anchor_path,
            },
        )
        verify_commit_manifest(Path(args.manifest), Path(args.output))
    except (OSError, json.JSONDecodeError, ReconciliationError) as exc:
        print(f"reconciliation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
