import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rebuild_holdings_from_broker_events.py"
SPEC = importlib.util.spec_from_file_location("rebuild_holdings", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
EVENT_COLUMNS = MODULE.BROKER_EVENT_COLUMNS
QUARANTINE_COLUMNS = MODULE.BROKER_QUARANTINE_COLUMNS
ANCHOR_COLUMNS = MODULE.HOLDINGS_ANCHOR_COLUMNS
MESSAGE_INDEX_COLUMNS = MODULE.BROKER_MESSAGE_INDEX_COLUMNS


def event(**overrides):
    row = {column: "" for column in EVENT_COLUMNS}
    row.update(
        {
            "source_account": "investor@example.com",
            "source_message_id": "m1",
            "source_thread_id": "t1",
            "message_received_at": "2026-07-16T13:31:00+00:00",
            "sender": "trades@verified.example",
            "subject": "Trade filled",
            "content_hash": "hash",
            "parser_version": "1.0.0",
            "broker": "ZA Bank 3",
            "account_ref": "acct-1",
            "order_id": "o1",
            "execution_id": "e1",
            "event_type": "TRADE",
            "status": "FILLED",
            "side": "BUY",
            "ticker": "TEST",
            "company_name": "Test Corp",
            "exchange": "NASDAQ",
            "security_id": "",
            "quantity": "10",
            "price": "100",
            "fees": "1",
            "currency": "USD",
            "trade_time": "2026-07-16T09:31:00-04:00",
            "trade_time_source": "BROKER_EXECUTION_TIME",
            "settlement_date": "2026-07-17",
            "affects_position": "true",
            "parse_confidence": "CONFIRMED",
            "supersedes_execution_id": "",
            "parsed_at": "2026-07-16T13:32:00+00:00",
            "notes": "",
        }
    )
    row.update(overrides)
    return row


class RebuildHoldingsTest(unittest.TestCase):
    def run_case(
        self,
        rows,
        *,
        confirmed=True,
        seed_output="sentinel\n",
        quarantine_rows=None,
        bootstrap_mode="FULL_HISTORY",
        anchor_rows=None,
        message_index_rows=None,
        profile_overrides=None,
        as_of="2026-07-16T15:00:00+00:00",
    ):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            profile = {
                "target_account": "investor@example.com",
                "profile_status": "CONFIRMED" if confirmed else "PENDING_AUTHORIZATION",
                "confirmed_senders": ["trades@verified.example"],
                "confirmed_subject_patterns": ["^Trade filled$"],
                "confirmed_execution_terms": ["FILLED"],
                "confirmed_timezone": "America/New_York",
                "bootstrap_mode": bootstrap_mode,
                "bootstrap_completed_at": "2026-07-16T13:00:00+00:00",
                "bootstrap_oldest_message_at": "2026-07-16T13:00:00+00:00",
                "bootstrap_event_count": len(rows),
                "anchor_at": (
                    "2026-07-16T13:00:00+00:00" if bootstrap_mode != "FULL_HISTORY" else None
                ),
                "parser_version": "1.0.0",
            }
            profile.update(profile_overrides or {})
            profile_path = tmp_path / "profile.json"
            events_path = tmp_path / "events.csv"
            quarantine_path = tmp_path / "quarantine.csv"
            anchor_path = tmp_path / "anchor.csv"
            message_index_path = tmp_path / "message_index.csv"
            output_path = tmp_path / "holdings.csv"
            audit_path = tmp_path / "audit.json"
            manifest_path = tmp_path / "manifest.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            with events_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=EVENT_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            with quarantine_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=QUARANTINE_COLUMNS)
                writer.writeheader()
                writer.writerows(quarantine_rows or [])
            with anchor_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=ANCHOR_COLUMNS)
                writer.writeheader()
                writer.writerows(anchor_rows or [])
            with message_index_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=MESSAGE_INDEX_COLUMNS)
                writer.writeheader()
                writer.writerows(message_index_rows or [])
            output_path.write_text(seed_output, encoding="utf-8")
            command = [
                    "python3",
                    str(SCRIPT),
                    "--profile",
                    str(profile_path),
                    "--events",
                    str(events_path),
                    "--quarantine",
                    str(quarantine_path),
                    "--anchor",
                    str(anchor_path),
                    "--message-index",
                    str(message_index_path),
                    "--output",
                    str(output_path),
                    "--audit",
                    str(audit_path),
                    "--manifest",
                    str(manifest_path),
                ]
            if as_of is not None:
                command.extend(["--as-of", as_of])
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
            )
            return result, output_path.read_text(encoding="utf-8"), (
                json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else None
            )

    def test_buy_then_partial_sell_keeps_average_cost(self):
        rows = [
            event(),
            event(
                source_message_id="m2",
                order_id="o2",
                execution_id="e2",
                side="SELL",
                quantity="4",
                price="120",
                fees="1",
                trade_time="2026-07-16T10:31:00-04:00",
                message_received_at="2026-07-16T14:32:00+00:00",
            ),
        ]
        result, output, audit = self.run_case(rows)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = list(csv.DictReader(output.splitlines()))
        self.assertEqual(parsed[0]["shares"], "6")
        self.assertEqual(parsed[0]["avg_cost"], "100.1")
        self.assertEqual(audit["realized_pnl_usd_from_ledger"], "78.6")

    def test_sell_beyond_position_fails_without_overwrite(self):
        rows = [event(side="SELL")]
        result, output, audit = self.run_case(rows)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output, "sentinel\n")
        self.assertIsNone(audit)

    def test_pending_profile_fails_without_overwrite(self):
        result, output, _ = self.run_case([event()], confirmed=False)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output, "sentinel\n")

    def test_conflicting_duplicate_fails(self):
        rows = [event(), event(quantity="11")]
        result, output, _ = self.run_case(rows)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output, "sentinel\n")

    def test_same_execution_id_across_messages_is_not_double_counted(self):
        rows = [
            event(),
            event(source_message_id="m2", source_thread_id="t2", content_hash="hash-2"),
        ]
        result, output, audit = self.run_case(rows)
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = list(csv.DictReader(output.splitlines()))
        self.assertEqual(parsed[0]["shares"], "10")
        self.assertEqual(audit["ignored_or_duplicate_rows"], 1)

    def test_future_event_fails_without_explicit_as_of(self):
        future = event(
            message_received_at="2099-01-01T14:32:00+00:00",
            trade_time="2099-01-01T09:31:00-05:00",
        )
        result, output, audit = self.run_case([future], as_of=None)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output, "sentinel\n")
        self.assertIsNone(audit)

    def test_unresolved_quarantine_fails_without_overwrite(self):
        quarantined = {column: "" for column in QUARANTINE_COLUMNS}
        quarantined.update(
            {
                "source_message_id": "unknown-template",
                "reason": "unknown template",
                "resolution_status": "UNRESOLVED",
            }
        )
        result, output, _ = self.run_case([event()], quarantine_rows=[quarantined])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output, "sentinel\n")

    def test_unconfirmed_subject_fails_without_overwrite(self):
        result, output, _ = self.run_case([event(subject="Unexpected subject")])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output, "sentinel\n")

    def test_exact_anchor_applies_only_later_events(self):
        anchor = {column: "" for column in ANCHOR_COLUMNS}
        anchor.update(
            {
                "anchor_at": "2026-07-16T13:00:00+00:00",
                "source": "user_chat",
                "account_ref": "acct-1",
                "ticker": "TEST",
                "company_name": "Test Corp",
                "shares": "10",
                "avg_cost": "100",
                "entry_date": "2026-07-10",
            }
        )
        sell = event(
            side="SELL",
            quantity="4",
            price="120",
            fees="0",
            trade_time="2026-07-16T10:31:00-04:00",
            message_received_at="2026-07-16T14:32:00+00:00",
        )
        result, output, audit = self.run_case(
            [sell], bootstrap_mode="EXACT_USER_ANCHOR", anchor_rows=[anchor]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = list(csv.DictReader(output.splitlines()))
        self.assertEqual(parsed[0]["shares"], "6")
        self.assertEqual(parsed[0]["avg_cost"], "100")
        self.assertEqual(audit["bootstrap_mode"], "EXACT_USER_ANCHOR")

    def test_exact_user_anchor_rejects_missing_cost(self):
        anchor = {column: "" for column in ANCHOR_COLUMNS}
        anchor.update(
            {
                "anchor_at": "2026-07-16T13:00:00+00:00",
                "source": "user_chat",
                "account_ref": "acct-1",
                "ticker": "TEST",
                "company_name": "Test Corp",
                "shares": "10",
                "avg_cost": "",
                "entry_date": "2026-07-10",
            }
        )
        result, output, _ = self.run_case(
            [event()], bootstrap_mode="EXACT_USER_ANCHOR", anchor_rows=[anchor]
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output, "sentinel\n")

    def test_notification_proxy_must_match_message_time(self):
        proxied = event(
            trade_time_source="NOTIFICATION_TIME_PROXY",
            trade_time="2026-07-16T09:32:00-04:00",
        )
        result, output, _ = self.run_case([proxied])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output, "sentinel\n")

    def test_stock_reward_keeps_quantity_with_unknown_cost(self):
        reward = event(
            event_type="STOCK_REWARD",
            status="CREDITED",
            side="CREDIT",
            ticker="NVDA",
            company_name="NVIDIA Corp",
            quantity="0.2396",
            price="",
            fees="",
        )
        result, output, audit = self.run_case([reward])
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = list(csv.DictReader(output.splitlines()))
        self.assertEqual(parsed[0]["shares"], "0.2396")
        self.assertEqual(parsed[0]["avg_cost"], "")
        self.assertEqual(audit["unknown_cost_tickers"], ["NVDA"])

    def test_verified_position_anchor_requires_local_message_evidence(self):
        anchor = {column: "" for column in ANCHOR_COLUMNS}
        anchor.update(
            {
                "anchor_at": "2026-07-16T13:00:00+00:00",
                "source": "gmail_full_mailbox_verification",
                "account_ref": "acct-1",
                "ticker": "NVDA",
                "company_name": "NVIDIA Corp",
                "shares": "0.2396",
                "avg_cost": "",
                "entry_date": "2026-07-16",
            }
        )
        profile = {"anchor_evidence_message_ids": ["evidence-message"]}
        missing_result, output, _ = self.run_case(
            [event()],
            bootstrap_mode="VERIFIED_POSITION_ANCHOR",
            anchor_rows=[anchor],
            profile_overrides=profile,
        )
        self.assertEqual(missing_result.returncode, 2)
        self.assertEqual(output, "sentinel\n")

        evidence = {column: "" for column in MESSAGE_INDEX_COLUMNS}
        evidence.update(
            {
                "source_message_id": "evidence-message",
                "message_received_at": "2026-07-16T12:59:00+00:00",
                "processing_status": "ANCHOR_EVIDENCE",
            }
        )
        success, output, audit = self.run_case(
            [event()],
            bootstrap_mode="VERIFIED_POSITION_ANCHOR",
            anchor_rows=[anchor],
            message_index_rows=[evidence],
            profile_overrides=profile,
        )
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(list(csv.DictReader(output.splitlines()))[0]["avg_cost"], "")
        self.assertEqual(audit["bootstrap_mode"], "VERIFIED_POSITION_ANCHOR")

    def test_ticker_alias_is_normalized(self):
        result, output, _ = self.run_case(
            [event(ticker="OLDTST")], profile_overrides={"ticker_aliases": {"OLDTST": "NEWTST"}}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = list(csv.DictReader(output.splitlines()))
        self.assertEqual(parsed[0]["ticker"], "NEWTST")

    def test_transaction_rolls_back_both_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_path = tmp_path / "holdings.csv"
            audit_path = tmp_path / "audit.json"
            manifest_path = tmp_path / "manifest.json"
            output_path.write_text("old holdings\n", encoding="utf-8")
            audit_path.write_text("old audit\n", encoding="utf-8")
            manifest_path.write_text("old manifest\n", encoding="utf-8")
            real_replace = MODULE.os.replace
            failed = False

            def fail_first_audit_replace(source, destination):
                nonlocal failed
                if Path(destination) == audit_path and not failed:
                    failed = True
                    raise OSError("simulated audit commit failure")
                return real_replace(source, destination)

            with mock.patch.object(MODULE.os, "replace", side_effect=fail_first_audit_replace):
                with self.assertRaises(OSError):
                    MODULE.commit_outputs(
                        output_path,
                        [],
                        audit_path,
                        {"status": "SUCCESS", "transaction_id": "txn-test"},
                        manifest_path,
                    )

            self.assertEqual(output_path.read_text(encoding="utf-8"), "old holdings\n")
            self.assertEqual(audit_path.read_text(encoding="utf-8"), "old audit\n")
            self.assertEqual(manifest_path.read_text(encoding="utf-8"), "old manifest\n")

    def test_manifest_detects_tampered_holdings(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_path = tmp_path / "holdings.csv"
            audit_path = tmp_path / "audit.json"
            manifest_path = tmp_path / "manifest.json"
            source_path = tmp_path / "events.csv"
            source_path.write_text("source\n", encoding="utf-8")
            MODULE.commit_outputs(
                output_path,
                [],
                audit_path,
                {"status": "SUCCESS", "transaction_id": "txn-manifest"},
                manifest_path,
                {"events": source_path},
            )
            MODULE.verify_commit_manifest(manifest_path, output_path)
            output_path.write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(MODULE.ReconciliationError):
                MODULE.verify_commit_manifest(manifest_path, output_path)

    def test_manifest_detects_tampered_source_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_path = tmp_path / "holdings.csv"
            audit_path = tmp_path / "audit.json"
            manifest_path = tmp_path / "manifest.json"
            source_path = tmp_path / "events.csv"
            source_path.write_text("source\n", encoding="utf-8")
            MODULE.commit_outputs(
                output_path,
                [],
                audit_path,
                {"status": "SUCCESS", "transaction_id": "txn-source"},
                manifest_path,
                {"events": source_path},
            )
            MODULE.verify_commit_manifest(manifest_path, output_path)
            source_path.write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(MODULE.ReconciliationError):
                MODULE.verify_commit_manifest(manifest_path, output_path)


if __name__ == "__main__":
    unittest.main()
