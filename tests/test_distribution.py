import unittest

from scripts.validate_distribution import validate_distribution


class DistributionTest(unittest.TestCase):
    def test_cross_agent_distribution_is_consistent(self):
        self.assertEqual(validate_distribution(), [])


if __name__ == "__main__":
    unittest.main()
