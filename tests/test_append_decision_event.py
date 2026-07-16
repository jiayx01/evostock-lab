import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "append_decision_event.py"


class AppendDecisionEventTest(unittest.TestCase):
    def run_event(self, tmp_path, event):
        input_path = tmp_path / "event.json"
        log_path = tmp_path / "decision_log.jsonl"
        input_path.write_text(json.dumps(event), encoding="utf-8")
        result = subprocess.run(
            ["python3", str(SCRIPT), "--input", str(input_path), "--log", str(log_path)],
            capture_output=True,
            text=True,
        )
        return result, log_path

    def base_event(self):
        return {
            "event_id": "decision-20260716-created",
            "event_type": "DECISION_CREATED",
            "decision_id": "20260716-2130-NEWTST-abcd1234",
            "occurred_at": "2026-07-16T21:30:00+08:00",
            "payload": {"action": "继续持有"},
        }

    def intent_event(self):
        return {
            "event_id": "decision-20260716-email-intent",
            "event_type": "EMAIL_SEND_INTENT",
            "decision_id": "20260716-2130-NEWTST-abcd1234",
            "occurred_at": "2026-07-16T21:31:00+08:00",
            "payload": {
                "recipient": "investor@example.com",
                "subject": "[美股盘中复盘 09:30] 继续持有",
                "idempotency_marker": "portfolio-email:20260716-2130-NEWTST-abcd1234",
            },
        }

    def sent_event(self, event_id="decision-20260716-email-sent"):
        return {
            "event_id": event_id,
            "event_type": "EMAIL_SENT",
            "decision_id": "20260716-2130-NEWTST-abcd1234",
            "occurred_at": "2026-07-16T21:32:00+08:00",
            "payload": {
                "message_id": "gmail-message-1",
                "idempotency_marker": "portfolio-email:20260716-2130-NEWTST-abcd1234",
            },
        }

    def test_duplicate_identical_event_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first, log_path = self.run_event(tmp_path, self.base_event())
            second, _ = self.run_event(tmp_path, self.base_event())
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(len(log_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_conflicting_duplicate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first, log_path = self.run_event(tmp_path, self.base_event())
            changed = self.base_event()
            changed["payload"] = {"action": "减仓候选"}
            second, _ = self.run_event(tmp_path, changed)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(len(log_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_naive_timestamp_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = self.base_event()
            event["occurred_at"] = "2026-07-16T21:30:00"
            result, log_path = self.run_event(Path(tmp), event)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(log_path.exists())

    def test_email_sent_without_decision_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self.run_event(Path(tmp), self.sent_event())
            self.assertEqual(result.returncode, 2)

    def test_second_email_sent_with_new_event_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_event(root, self.base_event())
            self.run_event(root, self.intent_event())
            first, log = self.run_event(root, self.sent_event())
            duplicate = self.sent_event("decision-20260716-email-sent-again")
            duplicate["occurred_at"] = "2026-07-16T21:33:00+08:00"
            second, _ = self.run_event(root, duplicate)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 2)
            self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 3)

    def test_email_marker_must_match_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_event(root, self.base_event())
            self.run_event(root, self.intent_event())
            sent = self.sent_event()
            sent["payload"]["idempotency_marker"] = "wrong-marker"
            result, _ = self.run_event(root, sent)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
