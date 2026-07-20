"""Demo mode must stay offline, deterministic and clearly labelled."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyze_portfolio import (  # noqa: E402
    DEMO_BANNER,
    DEMO_SERIES,
    EXAMPLE_DEMO_HOLDINGS,
    demo_price_frames,
)

ANCHOR = date(2026, 3, 2)
TICKERS = ["MSFT", "NVDA", "SPY", "^VIX"]


class DemoPriceFrameTests(unittest.TestCase):
    def test_is_deterministic_across_calls(self) -> None:
        first = demo_price_frames(TICKERS, sessions=120, anchor=ANCHOR)
        second = demo_price_frames(TICKERS, sessions=120, anchor=ANCHOR)
        self.assertEqual(sorted(first), sorted(second))
        for ticker in first:
            self.assertTrue(first[ticker].equals(second[ticker]), ticker)

    def test_closes_at_the_configured_price(self) -> None:
        frames = demo_price_frames(TICKERS, sessions=120, anchor=ANCHOR)
        for ticker, frame in frames.items():
            expected = DEMO_SERIES[ticker][0]
            self.assertAlmostEqual(float(frame["Close"].iloc[-1]), expected, places=2)

    def test_emits_ohlcv_the_analyser_can_consume(self) -> None:
        frame = demo_price_frames(["MSFT"], sessions=90, anchor=ANCHOR)["MSFT"]
        self.assertEqual(len(frame), 90)
        for column in ("Open", "High", "Low", "Close", "Volume"):
            self.assertIn(column, frame.columns)
        self.assertTrue((frame["High"] >= frame["Low"]).all())
        self.assertTrue((frame["High"] >= frame["Close"]).all())
        self.assertTrue((frame["Low"] <= frame["Close"]).all())
        self.assertTrue((frame["Close"] > 0).all())

    def test_bars_stop_before_the_anchor_session(self) -> None:
        frame = demo_price_frames(["SPY"], sessions=30, anchor=ANCHOR)["SPY"]
        self.assertLess(frame.index[-1].date(), ANCHOR)

    def test_unknown_tickers_get_stable_parameters(self) -> None:
        first = demo_price_frames(["ZZZZ"], sessions=40, anchor=ANCHOR)["ZZZZ"]
        second = demo_price_frames(["ZZZZ"], sessions=40, anchor=ANCHOR)["ZZZZ"]
        self.assertTrue(first.equals(second))

    def test_market_factor_moves_vix_against_the_index(self) -> None:
        frames = demo_price_frames(["SPY", "^VIX"], sessions=240, anchor=ANCHOR)
        spy = frames["SPY"]["Close"].pct_change().dropna()
        vix = frames["^VIX"]["Close"].pct_change().dropna()
        self.assertLess(spy.corr(vix), -0.5)

    def test_cash_is_not_priced(self) -> None:
        self.assertNotIn("CASH", demo_price_frames(["CASH", "SPY"], sessions=30, anchor=ANCHOR))


class DemoContractTests(unittest.TestCase):
    def test_banner_marks_the_output_as_synthetic(self) -> None:
        self.assertIn("DEMO MODE", DEMO_BANNER)
        self.assertIn("synthetic", DEMO_BANNER.lower())
        self.assertIn("not investment advice", DEMO_BANNER.lower())

    def test_demo_portfolio_ships_with_the_repository(self) -> None:
        path = Path(__file__).resolve().parents[1] / EXAMPLE_DEMO_HOLDINGS
        self.assertTrue(path.is_file(), f"missing demo portfolio at {path}")
        header = path.read_text(encoding="utf-8").splitlines()[0]
        for column in ("ticker", "shares", "avg_cost", "max_allocation_pct"):
            self.assertIn(column, header)


if __name__ == "__main__":
    unittest.main()
