import csv
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from append_outcome_price_bar import BAR_COLUMNS, normalize as normalize_bar
from calculate_decision_outcomes import (
    ET,
    OutcomeError,
    build_rows,
    load_bars,
    load_broker_events,
    load_decisions,
    sha256_inputs,
    write_rows,
)


XNYS = xcals.get_calendar("XNYS")


class DecisionOutcomeTest(unittest.TestCase):
    def decision(self):
        return {
            "event_id": "event-decision-main",
            "event_type": "DECISION_CREATED",
            "decision_id": "decision-main",
            "occurred_at": "2026-07-16T10:25:00-04:00",
            "payload": {
                "decision_kind": "PORTFOLIO_REVIEW",
                "market_phase": "REGULAR",
                "holdings": [
                    {
                        "ticker": "MSFT",
                        "action": "继续持有",
                        "reference_price": "100",
                        "reference_price_at": "2026-07-15T16:00:00-04:00",
                        "shares": "10",
                        "recommended_exposure": "1",
                    }
                ],
            },
        }

    def daily_close_bar(self, ticker, session_date, close):
        session = pd.Timestamp(session_date)
        market_close = XNYS.session_close(session).to_pydatetime()
        raw = {
            "bar_id": f"{ticker}-{session_date}-close",
            "ticker": ticker,
            "bar_at": market_close.isoformat(),
            "session_date": session_date,
            "bar_type": "DAILY_CLOSE",
            "close": str(close),
            "source": "test-fixture",
            "collected_at": (market_close + timedelta(minutes=1)).isoformat(),
        }
        normalized = normalize_bar(raw)
        return {
            **normalized,
            "bar_at_dt": datetime.fromisoformat(normalized["bar_at"]),
            "price_at_dt": datetime.fromisoformat(normalized["bar_at"]),
            "collected_at_dt": datetime.fromisoformat(normalized["collected_at"]),
            "close_value": float(normalized["close"]),
        }

    def intraday_raw(self, ticker, bar_at, close, *, bar_id=None, collected_at=None):
        observed = datetime.fromisoformat(bar_at)
        return {
            "bar_id": bar_id or f"{ticker}-{observed.isoformat()}-intraday",
            "ticker": ticker,
            "bar_at": observed.isoformat(),
            "session_date": observed.astimezone(ET).date().isoformat(),
            "bar_type": "INTRADAY",
            "close": str(close),
            "source": "test-fixture",
            "collected_at": collected_at or (observed + timedelta(minutes=1)).isoformat(),
        }

    def intraday_bar(self, ticker, bar_at, close):
        raw = self.intraday_raw(ticker, bar_at, close)
        normalized = normalize_bar(raw)
        interval_start = datetime.fromisoformat(normalized["bar_at"])
        return {
            **normalized,
            "bar_at_dt": interval_start,
            "price_at_dt": interval_start + timedelta(minutes=1),
            "collected_at_dt": datetime.fromisoformat(normalized["collected_at"]),
            "close_value": float(normalized["close"]),
        }

    def execution_reference_bar(self, ticker="MSFT", close=100):
        return self.intraday_bar(ticker, "2026-07-16T10:30:00-04:00", close)

    def delivered_at(self):
        return {"decision-main": datetime.fromisoformat("2026-07-16T10:30:00-04:00")}

    def find_horizon(self, rows, horizon):
        return next(row for row in rows if row["outcome_horizon"] == horizon)

    def test_close_and_1d_anchor_to_decision_session_not_reference_session(self):
        bars = [
            self.execution_reference_bar(),
            self.daily_close_bar("SPY", "2026-07-16", 600),
            self.daily_close_bar("SPY", "2026-07-17", 605),
            self.daily_close_bar("MSFT", "2026-07-16", 110),
            self.daily_close_bar("MSFT", "2026-07-17", 121),
        ]
        rows = build_rows(
            [self.decision()],
            self.delivered_at(),
            bars,
            [],
            datetime.fromisoformat("2026-07-17T17:00:00-04:00"),
            10.0,
            "fixture-hash",
        )

        close = self.find_horizon(rows, "close")
        one_day = self.find_horizon(rows, "1d")
        self.assertEqual(close["outcome_status"], "MATURED")
        self.assertEqual(one_day["outcome_status"], "MATURED")
        self.assertEqual(
            datetime.fromisoformat(close["end_price_at"]).astimezone(ET).date().isoformat(),
            "2026-07-16",
        )
        self.assertEqual(
            datetime.fromisoformat(one_day["end_price_at"]).astimezone(ET).date().isoformat(),
            "2026-07-17",
        )
        self.assertEqual(close["hold_return_pct"], "10.00000000")
        self.assertEqual(one_day["hold_return_pct"], "21.00000000")

    def test_missing_target_spy_bar_does_not_shift_1d_to_later_session(self):
        bars = [
            self.execution_reference_bar(),
            self.daily_close_bar("SPY", "2026-07-16", 600),
            self.daily_close_bar("SPY", "2026-07-20", 610),
            self.daily_close_bar("MSFT", "2026-07-17", 110),
            self.daily_close_bar("MSFT", "2026-07-20", 120),
        ]
        rows = build_rows(
            [self.decision()],
            self.delivered_at(),
            bars,
            [],
            datetime.fromisoformat("2026-07-20T17:00:00-04:00"),
            10.0,
            "fixture-hash",
        )

        one_day = self.find_horizon(rows, "1d")
        self.assertEqual(one_day["outcome_status"], "PENDING_DATA")
        self.assertEqual(one_day["end_price"], "")
        self.assertEqual(one_day["end_price_at"], "")

    def test_one_hour_uses_nearest_bar_within_symmetric_tolerance(self):
        bars = [
            self.execution_reference_bar(),
            self.intraday_bar("MSFT", "2026-07-16T11:19:00-04:00", 108),
            self.intraday_bar("MSFT", "2026-07-16T11:39:00-04:00", 112),
        ]
        rows = build_rows(
            [self.decision()],
            self.delivered_at(),
            bars,
            [],
            datetime.fromisoformat("2026-07-16T12:00:00-04:00"),
            10.0,
            "fixture-hash",
        )
        one_hour = self.find_horizon(rows, "1h")
        self.assertEqual(one_hour["outcome_status"], "MATURED")
        self.assertEqual(
            datetime.fromisoformat(one_hour["end_price_at"]).astimezone(ET).strftime("%H:%M"),
            "11:40",
        )

    def test_missing_post_delivery_reference_is_not_backfilled_from_analysis_price(self):
        bars = [
            self.daily_close_bar("SPY", "2026-07-16", 600),
            self.daily_close_bar("MSFT", "2026-07-16", 110),
        ]
        rows = build_rows(
            [self.decision()],
            self.delivered_at(),
            bars,
            [],
            datetime.fromisoformat("2026-07-16T17:00:00-04:00"),
            10.0,
            "fixture-hash",
        )

        close = self.find_horizon(rows, "close")
        self.assertEqual(close["outcome_status"], "PENDING_DATA")
        self.assertEqual(close["reference_price"], "")
        self.assertIn("未使用送达前分析参考价", close["notes"])

    def test_reference_uses_first_complete_minute_starting_after_delivery(self):
        delivered = {"decision-main": datetime.fromisoformat("2026-07-16T10:30:30-04:00")}
        bars = [
            self.intraday_bar("MSFT", "2026-07-16T10:30:00-04:00", 99),
            self.intraday_bar("MSFT", "2026-07-16T10:31:00-04:00", 100),
            self.daily_close_bar("SPY", "2026-07-16", 600),
            self.daily_close_bar("MSFT", "2026-07-16", 110),
        ]
        rows = build_rows(
            [self.decision()],
            delivered,
            bars,
            [],
            datetime.fromisoformat("2026-07-16T17:00:00-04:00"),
            10.0,
            "fixture-hash",
        )

        close = self.find_horizon(rows, "close")
        self.assertEqual(close["reference_price"], "100.00000000")
        self.assertEqual(
            datetime.fromisoformat(close["reference_price_at"]).astimezone(ET).strftime("%H:%M"),
            "10:32",
        )
        self.assertEqual(close["hold_return_pct"], "10.00000000")

    def test_reference_does_not_skip_over_missing_first_post_delivery_minute(self):
        delivered = {"decision-main": datetime.fromisoformat("2026-07-16T10:30:30-04:00")}
        bars = [
            self.intraday_bar("MSFT", "2026-07-16T10:32:00-04:00", 100),
            self.daily_close_bar("SPY", "2026-07-16", 600),
            self.daily_close_bar("MSFT", "2026-07-16", 110),
        ]
        rows = build_rows(
            [self.decision()],
            delivered,
            bars,
            [],
            datetime.fromisoformat("2026-07-16T17:00:00-04:00"),
            10.0,
            "fixture-hash",
        )

        close = self.find_horizon(rows, "close")
        self.assertEqual(close["outcome_status"], "PENDING_DATA")
        self.assertEqual(close["reference_price"], "")

    def test_load_bars_ignores_legacy_incomplete_minute_when_final_exists(self):
        provisional = self.intraday_raw(
            "MSFT",
            "2026-07-16T10:36:00-04:00",
            101,
            bar_id="MSFT-1036-provisional",
            collected_at="2026-07-16T10:36:24-04:00",
        )
        final = self.intraday_raw(
            "MSFT",
            "2026-07-16T10:36:00-04:00",
            102,
            bar_id="MSFT-1036-final",
            collected_at="2026-07-16T10:37:10-04:00",
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "bars.csv"
            with ledger.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=BAR_COLUMNS)
                writer.writeheader()
                writer.writerows([provisional, final])
            before_final = load_bars(
                ledger, datetime.fromisoformat("2026-07-16T10:36:30-04:00")
            )
            after_final = load_bars(
                ledger, datetime.fromisoformat("2026-07-16T10:38:00-04:00")
            )

        self.assertEqual(before_final, [])
        self.assertEqual([bar["bar_id"] for bar in after_final], ["MSFT-1036-final"])

    def test_load_bars_rejects_conflicting_completed_minutes(self):
        first = self.intraday_raw(
            "MSFT",
            "2026-07-16T10:36:00-04:00",
            101,
            bar_id="MSFT-1036-final-a",
            collected_at="2026-07-16T10:37:10-04:00",
        )
        second = self.intraday_raw(
            "MSFT",
            "2026-07-16T10:36:00-04:00",
            102,
            bar_id="MSFT-1036-final-b",
            collected_at="2026-07-16T10:38:10-04:00",
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "bars.csv"
            with ledger.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=BAR_COLUMNS)
                writer.writeheader()
                writer.writerows([first, second])
            with self.assertRaises(OutcomeError):
                load_bars(ledger, datetime.fromisoformat("2026-07-16T10:39:00-04:00"))

    def test_decision_and_email_visibility_are_cut_off_at_as_of(self):
        visible_decision = self.decision()
        visible_decision["decision_id"] = "decision-visible"
        visible_decision["event_id"] = "event-visible-created"
        future_email_intent = {
            "event_id": "event-visible-intent",
            "event_type": "EMAIL_SEND_INTENT",
            "decision_id": "decision-visible",
            "occurred_at": "2026-07-16T10:31:00-04:00",
            "payload": {
                "idempotency_marker": "portfolio-email:decision-visible",
                "recipient": "owner@example.com",
                "subject": "test",
            },
        }
        future_email_sent = {
            "event_id": "event-visible-sent",
            "event_type": "EMAIL_SENT",
            "decision_id": "decision-visible",
            "occurred_at": "2026-07-16T10:35:00-04:00",
            "payload": {
                "idempotency_marker": "portfolio-email:decision-visible",
                "message_id": "message-visible",
            },
        }
        future_decision = self.decision()
        future_decision.update(
            {
                "event_id": "event-future-created",
                "decision_id": "decision-future",
                "occurred_at": "2026-07-16T11:00:00-04:00",
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "decision_log.jsonl"
            events = [
                visible_decision,
                future_email_intent,
                future_email_sent,
                future_decision,
            ]
            log.write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            decisions, delivered = load_decisions(
                log, datetime.fromisoformat("2026-07-16T10:32:00-04:00")
            )

        self.assertEqual([item["decision_id"] for item in decisions], ["decision-visible"])
        self.assertEqual(delivered, {})

    def test_broker_event_requires_receipt_and_parse_to_be_visible_as_of(self):
        columns = [
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
        ]
        visible = {
            "ticker": "MSFT",
            "event_type": "TRADE",
            "status": "FILLED",
            "side": "SELL",
            "quantity": "1",
            "price": "105",
            "fees": "0",
            "trade_time": "2026-07-16T10:30:00-04:00",
            "trade_time_source": "BROKER_EXECUTION_TIME",
            "message_received_at": "2026-07-16T10:31:00-04:00",
            "parsed_at": "2026-07-16T10:32:00-04:00",
            "affects_position": "true",
            "parse_confidence": "CONFIRMED",
        }
        learned_later = {
            **visible,
            "ticker": "NVDA",
            "trade_time": "2026-07-16T10:40:00-04:00",
            "message_received_at": "2026-07-17T09:00:00-04:00",
            "parsed_at": "2026-07-17T09:01:00-04:00",
        }
        non_position = {
            **{column: "" for column in columns},
            "ticker": "MSFT",
            "event_type": "ORDER",
            "status": "SUBMITTED",
            "affects_position": "false",
            "parse_confidence": "CONFIRMED",
        }

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "broker_events.csv"
            with ledger.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows([visible, learned_later, non_position])
            rows = load_broker_events(
                ledger, datetime.fromisoformat("2026-07-16T17:00:00-04:00")
            )

        self.assertEqual([row["ticker"] for row in rows], ["MSFT"])

    def test_same_inputs_and_as_of_produce_identical_output_bytes(self):
        bars = [
            self.execution_reference_bar(),
            self.daily_close_bar("SPY", "2026-07-16", 600),
            self.daily_close_bar("MSFT", "2026-07-16", 110),
        ]
        as_of = datetime.fromisoformat("2026-07-16T17:00:00-04:00")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            source.write_text('{"stable":true}\n', encoding="utf-8")
            first_hash = sha256_inputs([source], as_of, 10.0)
            second_hash = sha256_inputs([source], as_of, 10.0)
            self.assertEqual(first_hash, second_hash)

            first_rows = build_rows(
                [self.decision()], self.delivered_at(), bars, [], as_of, 10.0, first_hash
            )
            second_rows = build_rows(
                [self.decision()], self.delivered_at(), bars, [], as_of, 10.0, second_hash
            )
            self.assertEqual(first_rows, second_rows)

            output = root / "outcomes.csv"
            write_rows(output, first_rows)
            first_bytes = output.read_bytes()
            write_rows(output, second_rows)
            self.assertEqual(output.read_bytes(), first_bytes)


if __name__ == "__main__":
    unittest.main()
