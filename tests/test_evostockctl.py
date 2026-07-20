import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.evostockctl import (
    ControlError,
    cmd_activate,
    cmd_init,
    cmd_record_task,
    cmd_status,
    cmd_verify_broker,
    cmd_verify_gmail,
    local_wakeups,
    load_state,
    required_tasks,
    schedule_plan,
)


def namespace(**values):
    return argparse.Namespace(**values)


class EvoStockControlTest(unittest.TestCase):
    def initialize(self, data_dir: Path, preset: str = "full", executor: str = "codex"):
        return cmd_init(
            namespace(
                data_dir=data_dir,
                target_account="Investor@Example.com",
                executor=executor,
                timezone="Asia/Shanghai",
                preset=preset,
                runtime_python=sys.executable,
                project_root=str(Path(__file__).resolve().parents[1]),
                replace=False,
            )
        )

    def test_init_normalizes_account_without_storing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            result = self.initialize(data_dir)
            state = result["deployment"]
            self.assertEqual(state["target_account"], "investor@example.com")
            self.assertNotIn("token", json.dumps(state).lower())

    def test_gmail_mismatch_does_not_mutate_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.initialize(data_dir)
            before = load_state(data_dir)
            with self.assertRaises(ControlError):
                cmd_verify_gmail(
                    namespace(
                        data_dir=data_dir,
                        observed_account="wrong@example.com",
                        provider="codex",
                    )
                )
            self.assertEqual(load_state(data_dir), before)

    def test_gmail_provider_must_match_active_executor(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.initialize(data_dir, executor="claude")
            before = load_state(data_dir)
            with self.assertRaises(ControlError):
                cmd_verify_gmail(
                    namespace(
                        data_dir=data_dir,
                        observed_account="investor@example.com",
                        provider="codex",
                    )
                )
            self.assertEqual(load_state(data_dir), before)

    def test_init_rejects_silent_executor_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.initialize(data_dir, executor="codex")
            with self.assertRaises(ControlError):
                self.initialize(data_dir, executor="claude")

    def test_activation_requires_gmail_broker_and_every_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.initialize(data_dir, preset="intraday")
            cmd_verify_gmail(
                namespace(
                    data_dir=data_dir,
                    observed_account="investor@example.com",
                    provider="codex",
                )
            )
            profile = data_dir / "broker_email_profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "target_account": "investor@example.com",
                        "broker": "ZA Bank",
                        "profile_status": "CONFIRMED",
                        "confirmed_senders": ["broker@example.com"],
                        "confirmed_subject_patterns": ["Trade Confirmation"],
                        "confirmed_execution_terms": ["FILLED"],
                        "confirmed_timezone": "Asia/Hong_Kong",
                        "bootstrap_completed_at": "2026-07-20T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            cmd_verify_broker(namespace(data_dir=data_dir, profile=profile))
            with self.assertRaises(ControlError):
                cmd_activate(namespace(data_dir=data_dir))

            for kind in required_tasks(load_state(data_dir)):
                cmd_record_task(
                    namespace(
                        data_dir=data_dir,
                        kind=kind,
                        platform="codex",
                        task_id=f"task-{kind}",
                        schedule="test schedule",
                    )
                )
            activated = cmd_activate(namespace(data_dir=data_dir))
            self.assertEqual(activated["deployment"]["status"], "ACTIVE")
            self.assertTrue(cmd_status(namespace(data_dir=data_dir))["ready_for_activation"])

    def test_profile_tampering_blocks_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self.initialize(data_dir, preset="daily")
            cmd_verify_gmail(
                namespace(
                    data_dir=data_dir,
                    observed_account="investor@example.com",
                    provider="codex",
                )
            )
            profile = data_dir / "broker_email_profile.json"
            profile.write_text(
                json.dumps(
                    {
                        "target_account": "investor@example.com",
                        "broker": "Example Broker",
                        "profile_status": "CONFIRMED",
                        "confirmed_senders": ["broker@example.com"],
                        "confirmed_subject_patterns": ["Trade Confirmation"],
                        "confirmed_execution_terms": ["FILLED"],
                        "confirmed_timezone": "America/New_York",
                        "bootstrap_completed_at": "2026-07-20T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            cmd_verify_broker(namespace(data_dir=data_dir, profile=profile))
            for kind in required_tasks(load_state(data_dir)):
                cmd_record_task(
                    namespace(
                        data_dir=data_dir,
                        kind=kind,
                        platform="codex",
                        task_id=f"task-{kind}",
                        schedule="test schedule",
                    )
                )

            profile.write_text(profile.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            status = cmd_status(namespace(data_dir=data_dir))
            self.assertIn("BROKER_PROFILE_HASH_MISMATCH", status["issues"])
            with self.assertRaises(ControlError):
                cmd_activate(namespace(data_dir=data_dir))

    def test_timezone_plan_covers_us_dst_offsets(self):
        wakeups = local_wakeups("Asia/Shanghai", ["09:30"])
        self.assertEqual(
            wakeups,
            [
                {"local_time": "21:30", "et_day_offset": 0},
                {"local_time": "22:30", "et_day_offset": 0},
            ],
        )

    def test_schedule_plan_uses_executor_specific_skill_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            codex_state = self.initialize(data_dir, preset="daily")["deployment"]
            codex_prompts = [task["prompt"] for task in schedule_plan(codex_state, data_dir)["tasks"]]
            self.assertTrue(all("Use $evostock-run" in prompt for prompt in codex_prompts))

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            claude_state = self.initialize(
                data_dir, preset="daily", executor="claude"
            )["deployment"]
            claude_prompts = [
                task["prompt"] for task in schedule_plan(claude_state, data_dir)["tasks"]
            ]
            self.assertTrue(
                all(
                    "Use /evostock-lab:evostock-run" in prompt
                    for prompt in claude_prompts
                )
            )
            self.assertTrue(all("$evostock-run" not in prompt for prompt in claude_prompts))


if __name__ == "__main__":
    unittest.main()
