import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from midnight_execution_gate import (
    GateError,
    append_skip_memory,
    error_result,
    evaluate_gate,
    parse_now,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "midnight_execution_gate.py"


class MidnightExecutionGateTest(unittest.TestCase):
    def test_regular_session_inside_shanghai_window_executes(self):
        result = evaluate_gate(datetime.fromisoformat("2026-07-21T00:05:00+08:00"))
        self.assertTrue(result["scheduled_window_ok"])
        self.assertTrue(result["xnys_is_session"])
        self.assertTrue(result["in_regular_session"])
        self.assertTrue(result["execute_gate"])
        self.assertIsNone(result["skip_reason"])
        self.assertEqual(result["scheduled_slot"], "2026-07-21-0000-CST")

    def test_non_session_fails_closed(self):
        result = evaluate_gate(datetime.fromisoformat("2026-07-20T00:05:00+08:00"))
        self.assertTrue(result["scheduled_window_ok"])
        self.assertFalse(result["xnys_is_session"])
        self.assertFalse(result["in_regular_session"])
        self.assertFalse(result["execute_gate"])
        self.assertEqual(result["skip_reason"], "SKIP_NON_XNYS_SESSION")

    def test_window_is_half_open(self):
        result = evaluate_gate(datetime.fromisoformat("2026-07-21T00:15:00+08:00"))
        self.assertFalse(result["scheduled_window_ok"])
        self.assertTrue(result["xnys_is_session"])
        self.assertTrue(result["in_regular_session"])
        self.assertFalse(result["execute_gate"])
        self.assertEqual(result["skip_reason"], "SKIP_OUTSIDE_SCHEDULED_WINDOW")

    def test_time_before_regular_session_is_reported(self):
        result = evaluate_gate(datetime.fromisoformat("2026-07-20T18:00:00+08:00"))
        self.assertTrue(result["xnys_is_session"])
        self.assertFalse(result["in_regular_session"])
        self.assertFalse(result["execute_gate"])

    def test_now_must_include_timezone(self):
        with self.assertRaises(GateError):
            parse_now("2026-07-21T00:05:00")

    def test_calendar_error_fails_closed_and_can_be_recorded(self):
        now = datetime.fromisoformat("2026-07-21T00:05:00+08:00")
        result = error_result(now, RuntimeError("calendar unavailable"))
        self.assertTrue(result["scheduled_window_ok"])
        self.assertIsNone(result["xnys_is_session"])
        self.assertIsNone(result["in_regular_session"])
        self.assertFalse(result["execute_gate"])
        self.assertEqual(result["skip_reason"], "SKIP_STAGE0_ERROR")

        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp) / "memory.md"
            append_skip_memory(memory, result)
            self.assertIn("skip_reason=SKIP_STAGE0_ERROR", memory.read_text())

    def test_cli_appends_memory_only_on_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp) / "memory.md"
            skipped = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--now",
                    "2026-07-20T00:05:00+08:00",
                    "--append-skip-memory",
                    str(memory),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(skipped.returncode, 0, skipped.stderr)
            skipped_result = json.loads(skipped.stdout)
            self.assertEqual(skipped_result["skip_memory_append_status"], "APPENDED")
            self.assertIn("skip_reason=SKIP_NON_XNYS_SESSION", memory.read_text())

            before = memory.read_text()
            allowed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--now",
                    "2026-07-21T00:05:00+08:00",
                    "--append-skip-memory",
                    str(memory),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            allowed_result = json.loads(allowed.stdout)
            self.assertTrue(allowed_result["execute_gate"])
            self.assertEqual(
                allowed_result["skip_memory_append_status"], "NOT_REQUIRED"
            )
            self.assertEqual(memory.read_text(), before)


if __name__ == "__main__":
    unittest.main()
