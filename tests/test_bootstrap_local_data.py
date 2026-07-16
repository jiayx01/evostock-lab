import csv
import tempfile
import unittest
from pathlib import Path

from bootstrap_local_data import initialize
from rebuild_holdings_from_broker_events import BROKER_EVENT_COLUMNS


class BootstrapLocalDataTest(unittest.TestCase):
    def test_initializes_private_runtime_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = initialize(root)
            profile = root / "broker_email_profile.json"
            profile.write_text("private-user-config\n", encoding="utf-8")
            second = initialize(root)

            self.assertIn("broker_email_profile.json", first["created"])
            self.assertIn("broker_email_profile.json", second["skipped"])
            self.assertEqual(profile.read_text(encoding="utf-8"), "private-user-config\n")
            with (root / "broker_events.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle)), BROKER_EVENT_COLUMNS)


if __name__ == "__main__":
    unittest.main()
