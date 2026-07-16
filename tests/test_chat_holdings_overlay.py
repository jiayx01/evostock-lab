import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from apply_chat_holdings_overlay import OverlayError, later_position_event_ids, verify_overlay
from rebuild_holdings_from_broker_events import BROKER_EVENT_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "apply_chat_holdings_overlay.py"


class ChatHoldingsOverlayTest(unittest.TestCase):
    def payload(self):
        return {
            "correction_id": "chat-20260716-1500",
            "corrected_at": "2026-07-16T15:00:00+08:00",
            "source": "user_chat",
            "holdings": [{"ticker": "MSFT", "avg_cost": "", "shares": ""}],
        }

    def paths(self, root):
        return (
            root / "overlay.csv",
            root / "audit.json",
            root / "manifest.json",
        )

    def call(self, root, payload=None, *, clear=False):
        output, audit, manifest = self.paths(root)
        command = [
            "python3",
            str(SCRIPT),
            "--output",
            str(output),
            "--audit",
            str(audit),
            "--manifest",
            str(manifest),
        ]
        if clear:
            command.extend(
                ["--clear", "--expected-correction-id", "chat-20260716-1500"]
            )
        else:
            input_path = root / "correction.json"
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            command.extend(["--input", str(input_path)])
        return subprocess.run(command, capture_output=True, text=True), (output, audit, manifest)

    def test_sparse_overlay_does_not_inherit_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, paths = self.call(root, self.payload())
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = list(csv.DictReader(paths[0].read_text(encoding="utf-8").splitlines()))
            self.assertEqual([row["ticker"] for row in rows], ["MSFT"])
            self.assertEqual(rows[0]["shares"], "")
            self.assertEqual(rows[0]["avg_cost"], "")
            self.assertEqual(rows[0]["position_type"], "")
            audit = verify_overlay(paths[2], paths[0], paths[1])
            self.assertEqual(audit["overlay_status"], "ACTIVE")

    def test_empty_replacement_explicitly_represents_full_liquidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = self.payload()
            payload["holdings"] = []
            result, paths = self.call(root, payload)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                list(csv.DictReader(paths[0].read_text(encoding="utf-8").splitlines())),
                [],
            )
            audit = verify_overlay(paths[2], paths[0], paths[1])
            self.assertEqual(audit["overlay_status"], "ACTIVE")
            self.assertEqual(audit["tickers"], [])

    def test_replacement_requires_matching_parent_and_increasing_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, paths = self.call(root, self.payload())
            self.assertEqual(first.returncode, 0, first.stderr)
            committed_before = tuple(path.read_bytes() for path in paths)

            wrong_parent = self.payload()
            wrong_parent.update(
                {
                    "correction_id": "chat-20260716-1600",
                    "corrected_at": "2026-07-16T16:00:00+08:00",
                    "expected_previous_correction_id": "chat-does-not-match",
                }
            )
            rejected_parent, _ = self.call(root, wrong_parent)
            self.assertEqual(rejected_parent.returncode, 2)
            self.assertEqual(tuple(path.read_bytes() for path in paths), committed_before)

            stale = self.payload()
            stale.update(
                {
                    "correction_id": "chat-20260716-1400",
                    "corrected_at": "2026-07-16T14:00:00+08:00",
                    "expected_previous_correction_id": "chat-20260716-1500",
                }
            )
            rejected_stale, _ = self.call(root, stale)
            self.assertEqual(rejected_stale.returncode, 2)
            self.assertEqual(tuple(path.read_bytes() for path in paths), committed_before)

    def test_non_finite_number_fails_without_commit(self):
        for value in ("Infinity", "-Infinity", "NaN"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                payload = self.payload()
                payload["holdings"][0]["shares"] = value
                result, paths = self.call(root, payload)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(any(path.exists() for path in paths))

    def test_overlay_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, paths = self.call(root, self.payload())
            self.assertEqual(result.returncode, 0, result.stderr)
            paths[0].write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(OverlayError):
                verify_overlay(paths[2], paths[0], paths[1])

    def test_verified_position_event_after_correction_marks_overlay_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            events_path = Path(tmp) / "events.csv"
            columns = BROKER_EVENT_COLUMNS
            rows = []
            for execution_id, trade_time in (
                ("before", "2026-07-16T14:59:00+08:00"),
                ("after", "2026-07-16T15:01:00+08:00"),
            ):
                row = {column: "" for column in columns}
                row.update(
                    {
                        "source_message_id": f"message-{execution_id}",
                        "execution_id": execution_id,
                        "event_type": "TRADE",
                        "status": "FILLED",
                        "trade_time": trade_time,
                        "affects_position": "true",
                        "parse_confidence": "CONFIRMED",
                    }
                )
                rows.append(row)
            with events_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
            self.assertEqual(
                later_position_event_ids(events_path, "2026-07-16T15:00:00+08:00"),
                ["after"],
            )

    def test_clear_requires_matching_correction_and_keeps_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, paths = self.call(root, self.payload())
            cleared, _ = self.call(root, clear=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(cleared.returncode, 0, cleared.stderr)
            audit = verify_overlay(paths[2], paths[0], paths[1])
            self.assertEqual(audit["overlay_status"], "INACTIVE")
            self.assertEqual(len(paths[0].read_text(encoding="utf-8").splitlines()), 1)

    def test_naive_correction_time_fails_without_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = self.payload()
            payload["corrected_at"] = "2026-07-16T15:00:00"
            result, paths = self.call(root, payload)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(paths[0].exists())


if __name__ == "__main__":
    unittest.main()
