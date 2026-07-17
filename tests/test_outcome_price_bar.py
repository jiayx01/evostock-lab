import unittest
from datetime import timedelta

import exchange_calendars as xcals
import pandas as pd

from append_outcome_price_bar import PriceBarError, normalize


XNYS = xcals.get_calendar("XNYS")


class OutcomePriceBarTest(unittest.TestCase):
    def daily_close_bar(self, session_date="2026-07-16"):
        session = pd.Timestamp(session_date)
        market_close = XNYS.session_close(session).to_pydatetime()
        return {
            "bar_id": f"MSFT-{session_date}-close",
            "ticker": "MSFT",
            "bar_at": market_close.isoformat(),
            "session_date": session_date,
            "bar_type": "DAILY_CLOSE",
            "close": "101.25",
            "source": "test-fixture",
            "collected_at": (market_close + timedelta(minutes=1)).isoformat(),
        }

    def test_valid_daily_close_is_accepted(self):
        normalized = normalize(self.daily_close_bar())
        self.assertEqual(normalized["session_date"], "2026-07-16")
        self.assertEqual(normalized["bar_type"], "DAILY_CLOSE")
        self.assertEqual(normalized["close"], "101.25")

    def test_non_finite_close_is_rejected(self):
        for value in ("Infinity", "-Infinity", "NaN"):
            with self.subTest(value=value):
                bar = self.daily_close_bar()
                bar["close"] = value
                with self.assertRaises(PriceBarError):
                    normalize(bar)

    def test_daily_close_must_match_declared_session(self):
        bar = self.daily_close_bar("2026-07-16")
        prior_close = XNYS.session_close(pd.Timestamp("2026-07-15")).to_pydatetime()
        bar["bar_at"] = prior_close.isoformat()
        bar["collected_at"] = (prior_close + timedelta(minutes=1)).isoformat()
        with self.assertRaises(PriceBarError):
            normalize(bar)

    def test_daily_close_cannot_use_market_open_timestamp(self):
        bar = self.daily_close_bar()
        market_open = XNYS.session_open(pd.Timestamp("2026-07-16")).to_pydatetime()
        bar["bar_at"] = market_open.isoformat()
        bar["collected_at"] = (market_open + timedelta(minutes=1)).isoformat()
        with self.assertRaises(PriceBarError):
            normalize(bar)

    def test_intraday_bar_must_be_inside_regular_session(self):
        bar = self.daily_close_bar()
        market_open = XNYS.session_open(pd.Timestamp("2026-07-16")).to_pydatetime()
        bar.update(
            {
                "bar_id": "MSFT-20260716-preopen",
                "bar_type": "INTRADAY",
                "bar_at": (market_open - timedelta(minutes=1)).isoformat(),
                "collected_at": market_open.isoformat(),
            }
        )
        with self.assertRaises(PriceBarError):
            normalize(bar)

    def test_intraday_bar_must_be_collected_after_minute_ends(self):
        bar = self.daily_close_bar()
        interval_start = XNYS.session_open(pd.Timestamp("2026-07-16")).to_pydatetime()
        bar.update(
            {
                "bar_id": "MSFT-20260716-incomplete",
                "bar_type": "INTRADAY",
                "bar_at": interval_start.isoformat(),
                "collected_at": (interval_start + timedelta(seconds=30)).isoformat(),
            }
        )
        with self.assertRaises(PriceBarError):
            normalize(bar)

    def test_collection_time_cannot_precede_observation(self):
        bar = self.daily_close_bar()
        bar["collected_at"] = (
            pd.Timestamp(bar["bar_at"]).to_pydatetime() - timedelta(seconds=1)
        ).isoformat()
        with self.assertRaises(PriceBarError):
            normalize(bar)


if __name__ == "__main__":
    unittest.main()
