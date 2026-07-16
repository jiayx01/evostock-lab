import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from append_candidate_event import CandidateEventError, validate_candidate_watchlist


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "append_candidate_event.py"


class AppendCandidateEventTest(unittest.TestCase):
    def event(self):
        return {
            "event_id": "candidate-AMZN-added-20260716",
            "event_type": "CANDIDATE_ADDED",
            "ticker": "AMZN",
            "occurred_at": "2026-07-16T16:00:00-04:00",
            "previous_state": None,
            "new_state": "研究队列",
            "payload": {"reason": "initial research"},
        }

    def call(self, root, event):
        input_path = root / "event.json"
        log_path = root / "events.jsonl"
        input_path.write_text(json.dumps(event), encoding="utf-8")
        result = subprocess.run(
            ["python3", str(SCRIPT), "--input", str(input_path), "--log", str(log_path)],
            capture_output=True,
            text=True,
        )
        return result, log_path

    def test_duplicate_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, log = self.call(root, self.event())
            second, _ = self.call(root, self.event())
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_conflicting_duplicate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, log = self.call(root, self.event())
            changed = self.event()
            changed["new_state"] = "开仓候选/待确认"
            second, _ = self.call(root, changed)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_invalid_state_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = self.event()
            event["new_state"] = "立即买入"
            result, log = self.call(Path(tmp), event)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(log.exists())

    def test_illegal_direct_jump_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, log = self.call(root, self.event())
            jump = {
                "event_id": "candidate-AMZN-jump-20260717",
                "event_type": "STATE_CHANGED",
                "ticker": "AMZN",
                "occurred_at": "2026-07-17T16:00:00-04:00",
                "previous_state": "研究队列",
                "new_state": "开仓候选/待确认",
                "payload": {},
            }
            second, _ = self.call(root, jump)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_forged_previous_state_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.call(root, self.event())
            changed = {
                "event_id": "candidate-AMZN-review-20260717",
                "event_type": "STATE_CHANGED",
                "ticker": "AMZN",
                "occurred_at": "2026-07-17T16:00:00-04:00",
                "previous_state": "持续观察",
                "new_state": "接近触发",
                "payload": {},
            }
            result, log = self.call(root, changed)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 1)

    def test_watchlist_state_must_match_event_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, log = self.call(root, self.event())
            watchlist = root / "watchlist.csv"
            watchlist.write_text(
                "ticker,state\nAMZN,持续观察\n", encoding="utf-8"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaises(CandidateEventError):
                validate_candidate_watchlist(log, watchlist)


if __name__ == "__main__":
    unittest.main()
