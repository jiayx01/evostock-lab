#!/usr/bin/env python3
"""Create private EvoStock Lab runtime files without overwriting existing data."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from append_outcome_price_bar import BAR_COLUMNS
from calculate_decision_outcomes import OUTPUT_COLUMNS
from evostock_paths import DATA_DIR
from rebuild_holdings_from_broker_events import (
    BROKER_EVENT_COLUMNS,
    BROKER_MESSAGE_INDEX_COLUMNS,
    BROKER_QUARANTINE_COLUMNS,
    HOLDING_COLUMNS,
    HOLDINGS_ANCHOR_COLUMNS,
)


CANDIDATE_COLUMNS = [
    "ticker",
    "company_name",
    "theme",
    "state",
    "added_at_et",
    "last_reviewed_at_et",
    "selection_score",
    "coverage_pct",
    "quality_score",
    "growth_score",
    "valuation_score",
    "trend_score",
    "catalyst_score",
    "risk_control_score",
    "market_fit",
    "portfolio_overlap",
    "thesis",
    "next_catalyst",
    "next_event_at",
    "entry_condition",
    "invalidation_condition",
    "consecutive_qualified_reviews",
    "last_email_at",
    "source_dates",
    "notes",
]

WATCHLIST_COLUMNS = [
    "date",
    "ticker",
    "company_name",
    "watch_reason",
    "target_buy_zone",
    "starter_allocation_pct",
    "max_allocation_pct",
    "thesis",
    "key_risks",
    "notes",
]

SNAPSHOT_COLUMNS = [
    "snapshot_date",
    "ticker",
    "shares",
    "avg_cost",
    "last_price",
    "market_value",
    "cost_basis",
    "unrealized_pnl",
    "unrealized_pnl_pct",
]


def pending_profile() -> dict[str, Any]:
    return {
        "target_account": "investor@example.com",
        "broker": "configure-me",
        "profile_status": "PENDING_AUTHORIZATION",
        "confirmed_senders": [],
        "excluded_senders": [],
        "confirmed_subject_patterns": [],
        "confirmed_execution_terms": [],
        "confirmed_non_position_terms": [],
        "confirmed_timezone": "PENDING",
        "bootstrap_mode": "FULL_HISTORY",
        "bootstrap_completed_at": None,
        "bootstrap_oldest_message_at": None,
        "bootstrap_event_count": 0,
        "anchor_at": None,
        "anchor_evidence_message_ids": [],
        "ticker_aliases": {},
        "parser_version": "1.0.0",
        "notes": "Replace placeholders only after mailbox and broker-template verification.",
    }


def write_csv(path: Path, columns: list[str]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerow(columns)


def initialize(data_dir: Path) -> dict[str, list[str]]:
    data_dir.mkdir(parents=True, exist_ok=True)
    for directory in ("reports", "screenshots", ".runtime"):
        (data_dir / directory).mkdir(exist_ok=True)

    csv_schemas = {
        "broker_events.csv": BROKER_EVENT_COLUMNS,
        "broker_event_quarantine.csv": BROKER_QUARANTINE_COLUMNS,
        "broker_message_index.csv": BROKER_MESSAGE_INDEX_COLUMNS,
        "holdings_anchor.csv": HOLDINGS_ANCHOR_COLUMNS,
        "holdings_current.csv": HOLDING_COLUMNS,
        "candidate_watchlist.csv": CANDIDATE_COLUMNS,
        "watchlist_current.csv": WATCHLIST_COLUMNS,
        "portfolio_snapshots.csv": SNAPSHOT_COLUMNS,
        "outcome_price_bars.csv": BAR_COLUMNS,
        "decision_outcomes.csv": OUTPUT_COLUMNS,
    }
    json_files = {
        "broker_email_profile.json": pending_profile(),
        "broker_sync_state.json": {
            "target_account": "investor@example.com",
            "last_successful_scan_at": None,
            "last_verified_history_id": None,
            "overlap_days": 7,
            "notes": "Pending first verified mailbox bootstrap.",
        },
    }
    text_files = ("candidate_state_log.jsonl", "decision_log.jsonl")

    created: list[str] = []
    skipped: list[str] = []
    for name, columns in csv_schemas.items():
        path = data_dir / name
        if path.exists():
            skipped.append(name)
        else:
            write_csv(path, columns)
            created.append(name)
    for name, value in json_files.items():
        path = data_dir / name
        if path.exists():
            skipped.append(name)
        else:
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            created.append(name)
    for name in text_files:
        path = data_dir / name
        if path.exists():
            skipped.append(name)
        else:
            path.touch()
            created.append(name)
    return {"created": sorted(created), "skipped": sorted(skipped)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    print(json.dumps(initialize(args.data_dir.expanduser()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
