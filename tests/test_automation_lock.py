import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation_lock.py"


class AutomationLockTest(unittest.TestCase):
    def call(self, root, action, run_id):
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                action,
                "--name",
                "intraday",
                "--run-id",
                run_id,
                "--root",
                str(root),
                "--stale-minutes",
                "75",
            ],
            capture_output=True,
            text=True,
        )

    def test_lock_is_exclusive_and_owner_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "locks"
            first = self.call(root, "acquire", "run-one")
            second = self.call(root, "acquire", "run-two")
            wrong_release = self.call(root, "release", "run-two")
            right_release = self.call(root, "release", "run-one")
            third = self.call(root, "acquire", "run-two")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 3)
            self.assertEqual(wrong_release.returncode, 4)
            self.assertEqual(right_release.returncode, 0, right_release.stderr)
            self.assertEqual(third.returncode, 0, third.stderr)


if __name__ == "__main__":
    unittest.main()
