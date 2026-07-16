import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import commit_broker_sync_batch as sync_commit
import rebuild_holdings_from_broker_events as rebuild


ROOT = Path(__file__).resolve().parents[1]
EVENT_COLUMNS = rebuild.BROKER_EVENT_COLUMNS
QUARANTINE_COLUMNS = rebuild.BROKER_QUARANTINE_COLUMNS
ANCHOR_COLUMNS = rebuild.HOLDINGS_ANCHOR_COLUMNS
LEGACY_INDEX_COLUMNS = [
    "source_message_id",
    "source_thread_id",
    "message_received_at",
    "sender",
    "subject",
    "lifecycle_type",
    "normalized_event_count",
    "processing_status",
    "processed_at",
    "notes",
]


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


class BrokerSyncBatchTest(unittest.TestCase):
    def make_paths(self, root: Path) -> sync_commit.BrokerPaths:
        return sync_commit.BrokerPaths(
            profile=root / "broker_email_profile.json",
            events=root / "broker_events.csv",
            quarantine=root / "broker_event_quarantine.csv",
            message_index=root / "broker_message_index.csv",
            sync_state=root / "broker_sync_state.json",
            anchor=root / "holdings_anchor.csv",
            holdings=root / "holdings_current.csv",
            audit=root / "reports/latest_holdings_reconciliation.json",
            manifest=root / "holdings_commit_manifest.json",
            commits_dir=root / ".broker_commits",
            current_pointer=root / ".broker_current",
            lock=root / ".runtime/broker-sync.lock",
        )

    def seed_v2(self, root: Path) -> sync_commit.BrokerPaths:
        paths = self.make_paths(root)
        profile = {
            "target_account": "investor@example.com",
            "profile_status": "CONFIRMED",
            "confirmed_senders": ["trades@verified.example"],
            "confirmed_subject_patterns": ["^Trade filled$"],
            "confirmed_execution_terms": ["FILLED"],
            "confirmed_timezone": "America/New_York",
            "bootstrap_mode": "EXACT_USER_ANCHOR",
            "bootstrap_completed_at": "2026-07-16T13:00:00+00:00",
            "anchor_at": "2026-07-16T13:00:00+00:00",
            "parser_version": "1.0.0",
        }
        paths.profile.write_text(json.dumps(profile), encoding="utf-8")
        write_csv(paths.events, EVENT_COLUMNS, [])
        write_csv(paths.quarantine, QUARANTINE_COLUMNS, [])
        write_csv(paths.message_index, LEGACY_INDEX_COLUMNS, [])
        paths.sync_state.write_text(
            json.dumps(
                {
                    "target_account": "investor@example.com",
                    "last_successful_scan_at": "2026-07-16T14:00:00+00:00",
                    "bootstrap_anchor_at": "2026-07-16T13:00:00+00:00",
                    "overlap_days": 7,
                    "last_verified_history_id": "100",
                }
            ),
            encoding="utf-8",
        )
        anchor = {column: "" for column in ANCHOR_COLUMNS}
        anchor.update(
            {
                "anchor_at": "2026-07-16T13:00:00+00:00",
                "source": "user_chat",
                "account_ref": "acct-1",
                "ticker": "TEST",
                "company_name": "Test Corp",
                "shares": "5",
                "avg_cost": "90",
                "entry_date": "2026-07-10",
                "notes": "test anchor",
            }
        )
        write_csv(paths.anchor, ANCHOR_COLUMNS, [anchor])

        holding = {column: "" for column in rebuild.HOLDING_COLUMNS}
        holding.update(
            {
                "date": "2026-07-16",
                "ticker": "TEST",
                "company_name": "Test Corp",
                "position_type": "stock",
                "shares": "5",
                "avg_cost": "90",
                "entry_date": "2026-07-10",
            }
        )
        audit = {
            "status": "SUCCESS",
            "target_account": "investor@example.com",
            "transaction_id": "legacy-transaction-000001",
        }
        rebuild.commit_outputs(
            paths.holdings,
            [holding],
            paths.audit,
            audit,
            paths.manifest,
            {
                "profile": paths.profile,
                "events": paths.events,
                "quarantine": paths.quarantine,
                "anchor": paths.anchor,
            },
        )
        rebuild.verify_commit_manifest(paths.manifest, paths.holdings)
        return paths

    def migrate_v3(self, root: Path) -> tuple[sync_commit.BrokerPaths, dict]:
        paths = self.seed_v2(root)
        result = sync_commit.migrate_existing(paths)
        self.assertEqual(result["status"], "MIGRATED")
        manifest = rebuild.verify_commit_manifest(paths.manifest, paths.holdings)
        self.assertEqual(manifest["manifest_version"], 3)
        return paths, manifest

    def event(self, **overrides) -> dict[str, str]:
        row = {column: "" for column in EVENT_COLUMNS}
        row.update(
            {
                "broker": "ZA Bank 3",
                "account_ref": "acct-1",
                "order_id": "order-1",
                "execution_id": "execution-1",
                "event_type": "TRADE",
                "status": "FILLED",
                "side": "BUY",
                "ticker": "TEST",
                "company_name": "Test Corp",
                "exchange": "NASDAQ",
                "quantity": "10",
                "price": "100",
                "fees": "1",
                "currency": "USD",
                "trade_time": "2026-07-16T14:31:00+00:00",
                "trade_time_source": "BROKER_EXECUTION_TIME",
                "settlement_date": "2026-07-17",
                "affects_position": "true",
                "parse_confidence": "CONFIRMED",
                "parsed_at": "2026-07-16T14:34:00+00:00",
            }
        )
        row.update(overrides)
        return row

    def batch(
        self,
        parent_transaction_id: str,
        *,
        batch_id: str = "batch-test-000001",
        pagination_complete: bool = True,
        terminal_next_page_token=None,
        messages=None,
    ) -> dict:
        if messages is None:
            messages = [
                {
                    "source_message_id": "message-1",
                    "source_thread_id": "thread-1",
                    "message_received_at": "2026-07-16T14:32:00+00:00",
                    "sender": "trades@verified.example",
                    "subject": "Trade filled",
                    "content_hash": "content-hash-1",
                    "parser_version": "1.0.0",
                    "lifecycle_type": "TRADE_FILLED",
                    "processing_status": "COMMITTED",
                    "discovered_at": "2026-07-16T14:33:00+00:00",
                    "processed_at": "2026-07-16T14:34:00+00:00",
                    "events": [self.event()],
                    "quarantine": None,
                    "notes": "",
                }
            ]
        return {
            "schema_version": 1,
            "run_id": "run-test-000001",
            "batch_id": batch_id,
            "target_account": "investor@example.com",
            "expected_parent_transaction_id": parent_transaction_id,
            "scan": {
                "started_at": "2026-07-16T14:00:00+00:00",
                "completed_at": "2026-07-16T15:00:00+00:00",
                "window_start": "2026-07-09T14:00:00+00:00",
                "pagination_complete": pagination_complete,
                "pages_fetched": 1,
                "terminal_next_page_token": terminal_next_page_token,
                "history_id_before": "100",
                "history_id_after": "101",
            },
            "messages": messages,
        }

    def assert_single_active_generation(self, paths: sync_commit.BrokerPaths) -> Path:
        self.assertTrue(paths.current_pointer.is_symlink())
        active_generation = paths.current_pointer.resolve()
        for logical_path in sync_commit.logical_artifacts(paths).values():
            self.assertTrue(logical_path.is_symlink(), logical_path)
            self.assertEqual(logical_path.resolve().parent, active_generation)
        return active_generation

    def test_v2_migration_publishes_all_top_level_files_through_one_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.seed_v2(Path(tmp))
            legacy_manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))

            result = sync_commit.migrate_existing(paths)

            self.assertEqual(result["status"], "MIGRATED")
            manifest = rebuild.verify_commit_manifest(paths.manifest, paths.holdings)
            self.assertEqual(manifest["manifest_version"], 3)
            self.assertEqual(
                manifest["parent_transaction_id"], legacy_manifest["transaction_id"]
            )
            active_generation = self.assert_single_active_generation(paths)
            self.assertEqual(Path(manifest["generation_path"]), active_generation)
            self.assertEqual(
                json.loads(paths.sync_state.read_text(encoding="utf-8"))[
                    "last_transaction_id"
                ],
                manifest["transaction_id"],
            )

    def test_success_batch_commits_index_events_state_and_holdings_in_same_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, parent = self.migrate_v3(Path(tmp))

            result = sync_commit.commit_batch(
                paths, self.batch(parent["transaction_id"])
            )

            self.assertEqual(result["commit_status"], "SUCCESS")
            manifest = rebuild.verify_commit_manifest(paths.manifest, paths.holdings)
            active_generation = self.assert_single_active_generation(paths)
            self.assertEqual(Path(manifest["generation_path"]), active_generation)
            for item in manifest["transaction_files"].values():
                self.assertEqual(Path(item["path"]).parent, active_generation)
            index_rows = read_csv(paths.message_index)
            self.assertEqual(
                [row["processing_status"] for row in index_rows[-2:]],
                ["DISCOVERED", "COMMITTED"],
            )
            self.assertEqual(len(read_csv(paths.events)), 1)
            state = json.loads(paths.sync_state.read_text(encoding="utf-8"))
            self.assertEqual(state["last_transaction_id"], manifest["transaction_id"])
            self.assertEqual(state["last_verified_history_id"], "101")
            holdings = read_csv(paths.holdings)
            self.assertEqual(holdings[0]["ticker"], "TEST")
            self.assertEqual(holdings[0]["shares"], "15")

    def test_incomplete_pagination_or_terminal_token_commits_blocked_without_waterline_move(self):
        cases = ((False, None), (True, "next-page-token"))
        for pagination_complete, terminal_token in cases:
            with self.subTest(
                pagination_complete=pagination_complete, terminal_token=terminal_token
            ):
                with tempfile.TemporaryDirectory() as tmp:
                    paths, parent = self.migrate_v3(Path(tmp))
                    before = json.loads(paths.sync_state.read_text(encoding="utf-8"))
                    batch = self.batch(
                        parent["transaction_id"],
                        batch_id=(
                            "batch-pagination-false"
                            if not pagination_complete
                            else "batch-terminal-token"
                        ),
                        pagination_complete=pagination_complete,
                        terminal_next_page_token=terminal_token,
                        messages=[],
                    )

                    result = sync_commit.commit_batch(paths, batch)

                    self.assertEqual(result["commit_status"], "BLOCKED")
                    with self.assertRaises(rebuild.ReconciliationError):
                        rebuild.verify_commit_manifest(paths.manifest, paths.holdings)
                    manifest = rebuild.verify_commit_manifest(
                        paths.manifest, paths.holdings, allow_blocked=True
                    )
                    self.assertEqual(manifest["commit_status"], "BLOCKED")
                    after = json.loads(paths.sync_state.read_text(encoding="utf-8"))
                    self.assertEqual(
                        after["last_successful_scan_at"], before["last_successful_scan_at"]
                    )
                    self.assertEqual(
                        after["last_verified_history_id"],
                        before["last_verified_history_id"],
                    )
                    self.assertEqual(result["waterline_before"], result["waterline_after"])

    def test_identical_batch_is_noop_and_same_id_with_different_content_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, parent = self.migrate_v3(Path(tmp))
            batch = self.batch(parent["transaction_id"])
            first = sync_commit.commit_batch(paths, batch)
            pointer_before = os.readlink(paths.current_pointer)
            generations_before = set(paths.commits_dir.iterdir())

            duplicate = sync_commit.commit_batch(paths, batch)

            self.assertEqual(duplicate["status"], "DUPLICATE_NOOP")
            self.assertEqual(duplicate["transaction_id"], first["transaction_id"])
            self.assertEqual(os.readlink(paths.current_pointer), pointer_before)
            self.assertEqual(set(paths.commits_dir.iterdir()), generations_before)

            conflicting = json.loads(json.dumps(batch))
            conflicting["scan"]["pages_fetched"] = 2
            with self.assertRaisesRegex(
                sync_commit.BrokerSyncError, "batch_id already committed with different content"
            ):
                sync_commit.commit_batch(paths, conflicting)

    def test_replaying_batch_a_after_batch_b_finds_receipt_in_active_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, parent = self.migrate_v3(Path(tmp))
            batch_a = self.batch(
                parent["transaction_id"], batch_id="batch-chain-receipt-a"
            )
            result_a = sync_commit.commit_batch(paths, batch_a)
            batch_b = self.batch(
                result_a["transaction_id"],
                batch_id="batch-chain-receipt-b",
                messages=[],
            )
            batch_b["run_id"] = "run-test-000002"
            batch_b["scan"].update(
                {
                    "started_at": "2026-07-16T15:00:00+00:00",
                    "completed_at": "2026-07-16T16:00:00+00:00",
                    "history_id_before": "101",
                    "history_id_after": "102",
                }
            )
            result_b = sync_commit.commit_batch(paths, batch_b)
            active_before_replay = os.readlink(paths.current_pointer)
            generations_before_replay = set(paths.commits_dir.iterdir())

            replay = sync_commit.commit_batch(paths, batch_a)

            self.assertEqual(replay["status"], "DUPLICATE_NOOP")
            self.assertEqual(replay["transaction_id"], result_a["transaction_id"])
            self.assertEqual(os.readlink(paths.current_pointer), active_before_replay)
            self.assertEqual(set(paths.commits_dir.iterdir()), generations_before_replay)
            active = rebuild.verify_commit_manifest(paths.manifest, paths.holdings)
            self.assertEqual(active["transaction_id"], result_b["transaction_id"])

    def test_stale_parent_is_rejected_without_switching_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, _ = self.migrate_v3(Path(tmp))
            active_before = os.readlink(paths.current_pointer)

            with self.assertRaisesRegex(sync_commit.BrokerSyncError, "STALE_PARENT"):
                sync_commit.commit_batch(
                    paths, self.batch("stale-parent-000001", batch_id="batch-stale-parent")
                )

            self.assertEqual(os.readlink(paths.current_pointer), active_before)
            rebuild.verify_commit_manifest(paths.manifest, paths.holdings)

    def test_tampered_index_or_sync_state_generation_artifact_fails_verification(self):
        for artifact_name in ("broker_message_index", "broker_sync_state"):
            with self.subTest(artifact_name=artifact_name):
                with tempfile.TemporaryDirectory() as tmp:
                    paths, parent = self.migrate_v3(Path(tmp))
                    sync_commit.commit_batch(paths, self.batch(parent["transaction_id"]))
                    manifest = rebuild.verify_commit_manifest(
                        paths.manifest, paths.holdings
                    )
                    artifact = Path(
                        manifest["transaction_files"][artifact_name]["path"]
                    )
                    artifact.write_bytes(artifact.read_bytes() + b"\ntampered")

                    with self.assertRaises(rebuild.ReconciliationError):
                        rebuild.verify_commit_manifest(paths.manifest, paths.holdings)

    def test_pointer_replace_failure_keeps_previous_generation_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, parent = self.migrate_v3(Path(tmp))
            pointer_before = os.readlink(paths.current_pointer)
            manifest_before = paths.manifest.read_bytes()
            real_replace = sync_commit.os.replace

            def fail_pointer_switch(source, destination):
                if Path(destination) == paths.current_pointer:
                    raise OSError("simulated pointer switch failure")
                return real_replace(source, destination)

            with mock.patch.object(
                sync_commit.os, "replace", side_effect=fail_pointer_switch
            ):
                with self.assertRaisesRegex(OSError, "pointer switch failure"):
                    sync_commit.commit_batch(
                        paths,
                        self.batch(
                            parent["transaction_id"], batch_id="batch-pointer-failure"
                        ),
                    )

            self.assertEqual(os.readlink(paths.current_pointer), pointer_before)
            self.assertEqual(paths.manifest.read_bytes(), manifest_before)
            active = rebuild.verify_commit_manifest(paths.manifest, paths.holdings)
            self.assertEqual(active["transaction_id"], parent["transaction_id"])

    def test_pointer_failure_orphan_does_not_block_immediate_same_batch_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, parent = self.migrate_v3(Path(tmp))
            batch = self.batch(
                parent["transaction_id"], batch_id="batch-pointer-immediate-retry"
            )
            generations_before = set(paths.commits_dir.iterdir())
            pointer_before = os.readlink(paths.current_pointer)
            real_replace = sync_commit.os.replace

            def fail_pointer_switch(source, destination):
                if Path(destination) == paths.current_pointer:
                    raise OSError("simulated pointer switch failure")
                return real_replace(source, destination)

            with mock.patch.object(
                sync_commit.os, "replace", side_effect=fail_pointer_switch
            ):
                with self.assertRaisesRegex(OSError, "pointer switch failure"):
                    sync_commit.commit_batch(paths, batch)

            orphan_generations = set(paths.commits_dir.iterdir()) - generations_before
            self.assertEqual(len(orphan_generations), 1)
            self.assertEqual(os.readlink(paths.current_pointer), pointer_before)

            retry = sync_commit.commit_batch(paths, batch)

            self.assertEqual(retry["status"], "COMMITTED")
            self.assertEqual(retry["commit_status"], "SUCCESS")
            active = rebuild.verify_commit_manifest(paths.manifest, paths.holdings)
            self.assertEqual(active["transaction_id"], retry["transaction_id"])
            self.assertNotIn(Path(active["generation_path"]), orphan_generations)
            self.assertEqual(
                len(set(paths.commits_dir.iterdir()) - generations_before), 2
            )


if __name__ == "__main__":
    unittest.main()
