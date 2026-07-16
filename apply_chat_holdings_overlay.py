#!/usr/bin/env python3
"""Commit or clear a sparse, chat-sourced holdings view used only for analysis."""

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
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from evostock_paths import data_path
from rebuild_holdings_from_broker_events import HOLDING_COLUMNS, TICKER_RE, sha256_path


OVERLAY_COLUMNS = ["correction_id", "corrected_at", "source", *HOLDING_COLUMNS]
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{6,160}$")
NUMERIC_FIELDS = {
    "shares",
    "avg_cost",
    "last_price",
    "market_value",
    "portfolio_weight_pct",
    "target_allocation_pct",
    "max_allocation_pct",
    "stop_loss_pct",
    "trim_profit_pct",
}


class OverlayError(ValueError):
    pass


def parse_time(value: Any, field: str) -> datetime:
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise OverlayError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise OverlayError(f"{field} must include a timezone")
    return parsed


def validate_number(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "待确认":
        return ""
    try:
        number = Decimal(raw)
    except InvalidOperation as exc:
        raise OverlayError(f"{field} must be numeric or blank") from exc
    if not number.is_finite():
        raise OverlayError(f"{field} must be finite")
    if field in {"shares", "avg_cost", "last_price", "market_value"} and number <= 0:
        raise OverlayError(f"{field} must be positive when provided")
    return format(number.normalize(), "f")


def validate_date(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw or raw == "待确认":
        return ""
    try:
        return datetime.fromisoformat(raw).date().isoformat()
    except ValueError as exc:
        raise OverlayError(f"{field} must be an ISO date") from exc


def normalize_payload(value: Any) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if not isinstance(value, dict):
        raise OverlayError("input must be one JSON object")
    allowed_top_level = {
        "correction_id",
        "corrected_at",
        "source",
        "holdings",
        "expected_previous_correction_id",
    }
    unknown_top_level = sorted(set(value) - allowed_top_level)
    if unknown_top_level:
        raise OverlayError(f"input has unknown fields: {', '.join(unknown_top_level)}")
    correction_id = str(value.get("correction_id") or "").strip()
    if not ID_RE.fullmatch(correction_id):
        raise OverlayError("invalid correction_id")
    corrected_at = parse_time(value.get("corrected_at"), "corrected_at")
    if str(value.get("source") or "").strip() != "user_chat":
        raise OverlayError("source must be user_chat")
    holdings = value.get("holdings")
    if not isinstance(holdings, list):
        raise OverlayError("holdings must be a complete replacement list")
    expected_previous = str(value.get("expected_previous_correction_id") or "").strip()
    if expected_previous and not ID_RE.fullmatch(expected_previous):
        raise OverlayError("invalid expected_previous_correction_id")

    rows: list[dict[str, str]] = []
    provided_fields: dict[str, list[str]] = {}
    seen: set[str] = set()
    allowed_fields = set(HOLDING_COLUMNS) - {"date"}
    for index, item in enumerate(holdings, start=1):
        if not isinstance(item, dict):
            raise OverlayError(f"holding {index} must be an object")
        unknown = sorted(set(item) - allowed_fields)
        if unknown:
            raise OverlayError(f"holding {index} has unknown fields: {', '.join(unknown)}")
        ticker = str(item.get("ticker") or "").strip().upper()
        if not TICKER_RE.fullmatch(ticker):
            raise OverlayError(f"holding {index} has invalid ticker")
        if ticker in seen:
            raise OverlayError(f"holding {index} duplicates ticker {ticker}")
        seen.add(ticker)

        row = {column: "" for column in OVERLAY_COLUMNS}
        row.update(
            {
                "correction_id": correction_id,
                "corrected_at": corrected_at.isoformat(),
                "source": "user_chat",
                "date": corrected_at.date().isoformat(),
                "ticker": ticker,
                "position_type": str(item.get("position_type") or "").strip(),
            }
        )
        fields: list[str] = ["ticker"]
        for field in allowed_fields - {"ticker", "position_type", "notes"}:
            raw = item.get(field)
            if raw is None or str(raw).strip() in {"", "待确认"}:
                continue
            if field in NUMERIC_FIELDS:
                row[field] = validate_number(raw, field)
            elif field in {"entry_date", "next_earnings_date"}:
                row[field] = validate_date(raw, field)
            else:
                row[field] = str(raw).strip()
            fields.append(field)
        user_notes = str(item.get("notes") or "").strip()
        system_note = "用户聊天稀疏校正；未提供字段保持空值；仅用于分析，不作为EXACT_USER_ANCHOR"
        row["notes"] = f"{user_notes}；{system_note}" if user_notes else system_note
        if user_notes:
            fields.append("notes")
        rows.append(row)
        provided_fields[ticker] = sorted(fields)

    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    audit = {
        "status": "SUCCESS",
        "overlay_status": "ACTIVE",
        "correction_id": correction_id,
        "expected_previous_correction_id": expected_previous or None,
        "corrected_at": corrected_at.isoformat(),
        "source": "user_chat",
        "tickers": sorted(seen),
        "provided_fields": provided_fields,
        "correction_payload_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "transaction_id": f"overlay-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}",
    }
    return rows, audit


def replacement_status(
    output_path: Path,
    audit_path: Path,
    manifest_path: Path,
    new_audit: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    paths = (output_path, audit_path, manifest_path)
    present = [path for path in paths if path.exists()]
    expected_previous = str(new_audit.get("expected_previous_correction_id") or "")
    if not present:
        if expected_previous:
            raise OverlayError("expected_previous_correction_id was supplied but no overlay exists")
        return "COMMIT", None
    if len(present) != len(paths):
        raise OverlayError("existing analysis overlay files are incomplete")

    current = verify_overlay(manifest_path, output_path, audit_path)
    current_id = str(current.get("correction_id") or "")
    new_id = str(new_audit.get("correction_id") or "")
    if new_id == current_id:
        if (
            current.get("overlay_status") == "ACTIVE"
            and current.get("correction_payload_sha256")
            == new_audit.get("correction_payload_sha256")
        ):
            return "DUPLICATE_NOOP", current
        raise OverlayError("correction_id already exists with different content or state")
    if expected_previous != current_id:
        raise OverlayError("expected_previous_correction_id does not match current overlay")

    current_time_field = (
        "corrected_at" if current.get("overlay_status") == "ACTIVE" else "cleared_at"
    )
    current_time = parse_time(current.get(current_time_field), current_time_field)
    new_time = parse_time(new_audit.get("corrected_at"), "corrected_at")
    if new_time <= current_time:
        raise OverlayError("new correction must be later than the current overlay state")
    return "COMMIT", current


def later_position_event_ids(events_path: Path, corrected_at: Any) -> list[str]:
    cutoff = parse_time(corrected_at, "corrected_at")
    try:
        with events_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {
                "source_message_id",
                "execution_id",
                "event_type",
                "status",
                "trade_time",
                "affects_position",
                "parse_confidence",
            }
            if not required.issubset(set(reader.fieldnames or [])):
                raise OverlayError("broker event ledger is missing overlay freshness fields")
            rows = list(reader)
    except OSError as exc:
        raise OverlayError("broker event ledger is unavailable") from exc

    later: list[str] = []
    for row_number, row in enumerate(rows, start=2):
        affects = str(row.get("affects_position") or "").strip().lower()
        confidence = str(row.get("parse_confidence") or "").strip().upper()
        event_type = str(row.get("event_type") or "").strip().upper()
        status = str(row.get("status") or "").strip().upper()
        if affects not in {"true", "1", "yes"} or confidence != "CONFIRMED":
            continue
        if not (
            (event_type == "TRADE" and status in {"FILLED", "PARTIALLY_FILLED"})
            or (event_type == "STOCK_REWARD" and status == "CREDITED")
        ):
            continue
        try:
            trade_time = parse_time(row.get("trade_time"), f"trade_time row {row_number}")
        except OverlayError as exc:
            raise OverlayError(f"invalid broker event time at row {row_number}") from exc
        if trade_time > cutoff:
            identity = str(row.get("execution_id") or row.get("source_message_id") or row_number)
            later.append(identity)
    return later


def stage_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", newline="", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=OVERLAY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        return Path(handle.name)


def stage_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        return Path(handle.name)


def restore(path: Path, existed: bool, content: bytes | None) -> None:
    if not existed:
        path.unlink(missing_ok=True)
        return
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(content or b"")
        staged = Path(handle.name)
    os.replace(staged, path)


def commit_overlay(
    output_path: Path,
    rows: list[dict[str, str]],
    audit_path: Path,
    audit: dict[str, Any],
    manifest_path: Path,
) -> None:
    before = {
        path: (path.exists(), path.read_bytes() if path.exists() else None)
        for path in (output_path, audit_path, manifest_path)
    }
    staged_output = stage_csv(output_path, rows)
    staged_audit = stage_json(audit_path, audit)
    manifest = {
        "manifest_version": 1,
        "transaction_id": audit["transaction_id"],
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "overlay_path": str(output_path.resolve()),
        "overlay_sha256": sha256_path(staged_output),
        "audit_path": str(audit_path.resolve()),
        "audit_sha256": sha256_path(staged_audit),
    }
    staged_manifest = stage_json(manifest_path, manifest)
    try:
        os.replace(staged_output, output_path)
        os.replace(staged_audit, audit_path)
        os.replace(staged_manifest, manifest_path)
    except OSError:
        for staged in (staged_output, staged_audit, staged_manifest):
            staged.unlink(missing_ok=True)
        for path, (existed, content) in before.items():
            restore(path, existed, content)
        raise


def verify_overlay(
    manifest_path: Path, output_path: Path, audit_path: Path | None = None
) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OverlayError("analysis overlay manifest is missing or invalid") from exc
    if Path(str(manifest.get("overlay_path") or "")).resolve() != output_path.resolve():
        raise OverlayError("analysis overlay path differs from manifest")
    if not output_path.is_file() or sha256_path(output_path) != manifest.get("overlay_sha256"):
        raise OverlayError("analysis overlay hash differs from manifest")
    committed_audit = Path(str(manifest.get("audit_path") or ""))
    if audit_path is not None and committed_audit.resolve() != audit_path.resolve():
        raise OverlayError("analysis overlay audit path differs from manifest")
    if not committed_audit.is_file() or sha256_path(committed_audit) != manifest.get("audit_sha256"):
        raise OverlayError("analysis overlay audit hash differs from manifest")
    try:
        audit = json.loads(committed_audit.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OverlayError("analysis overlay audit is invalid") from exc
    if audit.get("transaction_id") != manifest.get("transaction_id"):
        raise OverlayError("analysis overlay transaction_id mismatch")
    if audit.get("overlay_status") not in {"ACTIVE", "INACTIVE"}:
        raise OverlayError("analysis overlay has invalid status")
    return audit


def clear_overlay(
    output_path: Path,
    audit_path: Path,
    manifest_path: Path,
    expected_correction_id: str,
) -> dict[str, Any]:
    current = verify_overlay(manifest_path, output_path, audit_path)
    if current.get("overlay_status") != "ACTIVE":
        raise OverlayError("analysis overlay is already inactive")
    if current.get("correction_id") != expected_correction_id:
        raise OverlayError("expected correction_id does not match active overlay")
    now = datetime.now(timezone.utc)
    audit = {
        "status": "SUCCESS",
        "overlay_status": "INACTIVE",
        "correction_id": expected_correction_id,
        "cleared_at": now.isoformat(),
        "previous_transaction_id": current.get("transaction_id"),
        "transaction_id": f"overlay-clear-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}",
    }
    commit_overlay(output_path, [], audit_path, audit, manifest_path)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="JSON sparse holdings correction")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--expected-correction-id")
    parser.add_argument("--output", default=data_path("holdings_analysis_overlay.csv"))
    parser.add_argument(
        "--audit", default=data_path("reports", "latest_holdings_analysis_overlay.json")
    )
    parser.add_argument(
        "--manifest", default=data_path("holdings_analysis_overlay_manifest.json")
    )
    args = parser.parse_args()
    output_path = Path(args.output)
    audit_path = Path(args.audit)
    manifest_path = Path(args.manifest)
    try:
        if args.clear:
            if not args.expected_correction_id or args.input:
                raise OverlayError("--clear requires --expected-correction-id and no --input")
            audit = clear_overlay(
                output_path, audit_path, manifest_path, args.expected_correction_id
            )
        else:
            if not args.input or args.expected_correction_id:
                raise OverlayError("active overlay requires --input only")
            value = json.loads(Path(args.input).read_text(encoding="utf-8"))
            rows, audit = normalize_payload(value)
            status, current = replacement_status(
                output_path, audit_path, manifest_path, audit
            )
            if status == "DUPLICATE_NOOP":
                audit = {**(current or audit), "commit_status": status}
            else:
                commit_overlay(output_path, rows, audit_path, audit, manifest_path)
                verify_overlay(manifest_path, output_path, audit_path)
    except (OSError, json.JSONDecodeError, OverlayError) as exc:
        print(f"analysis overlay failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
