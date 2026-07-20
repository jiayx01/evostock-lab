import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from automation_gate import GateError, evaluate_gate, parse_now


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation_gate.py"


class AutomationGateTest(unittest.TestCase):
    def test_intraday_slot_executes_during_regular_session(self):
        result = evaluate_gate(
            datetime.fromisoformat("2026-07-20T09:35:00-04:00"), "intraday"
        )
        self.assertTrue(result["execute_gate"])
        self.assertEqual(result["scheduled_slot"], "2026-07-20-0930-ET-intraday")

    def test_duplicate_dst_wakeup_is_rejected_by_slot_window(self):
        result = evaluate_gate(
            datetime.fromisoformat("2026-07-20T10:05:00-04:00"), "intraday"
        )
        self.assertFalse(result["scheduled_window_ok"])
        self.assertEqual(result["skip_reason"], "SKIP_OUTSIDE_SCHEDULED_WINDOW")

    def test_post_close_requires_closed_market(self):
        result = evaluate_gate(
            datetime.fromisoformat("2026-07-20T16:35:00-04:00"), "post-close"
        )
        self.assertTrue(result["after_regular_session"])
        self.assertTrue(result["execute_gate"])

    def test_weekly_review_only_runs_on_last_session(self):
        thursday = evaluate_gate(
            datetime.fromisoformat("2026-07-16T16:50:00-04:00"), "weekly-review"
        )
        friday = evaluate_gate(
            datetime.fromisoformat("2026-07-17T16:50:00-04:00"), "weekly-review"
        )
        self.assertFalse(thursday["execute_gate"])
        self.assertEqual(thursday["skip_reason"], "SKIP_CADENCE_NOT_DUE")
        self.assertTrue(friday["execute_gate"])

    def test_monthly_review_only_runs_on_last_session(self):
        result = evaluate_gate(
            datetime.fromisoformat("2026-07-31T17:05:00-04:00"), "monthly-review"
        )
        self.assertTrue(result["execute_gate"])

    def test_non_session_fails_closed(self):
        result = evaluate_gate(
            datetime.fromisoformat("2026-07-19T09:35:00-04:00"), "intraday"
        )
        self.assertFalse(result["execute_gate"])
        self.assertEqual(result["skip_reason"], "SKIP_NON_XNYS_SESSION")

    def test_naive_now_is_rejected(self):
        with self.assertRaises(GateError):
            parse_now("2026-07-20T09:35:00")

    def test_cli_appends_one_audit_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_log = Path(tmp) / "automation_memory.jsonl"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--mode",
                    "intraday",
                    "--now",
                    "2026-07-19T09:35:00-04:00",
                    "--append-event-log",
                    str(event_log),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["event_append_status"], "APPENDED")
            records = event_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(records), 1)
            self.assertEqual(json.loads(records[0])["event"], "AUTOMATION_GATE")


if __name__ == "__main__":
    unittest.main()
