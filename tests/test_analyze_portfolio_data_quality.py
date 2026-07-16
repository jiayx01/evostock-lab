import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from analyze_portfolio import (
    build_metrics,
    keep_completed_daily_bars,
    market_heat,
    market_regime,
    portfolio_snapshot_drawdown,
    technical_state,
    upsert_snapshot_log,
)


ET = ZoneInfo("America/New_York")


def price_frame(periods=40):
    index = pd.bdate_range(end="2026-07-15", periods=periods)
    close = pd.Series(range(100, 100 + periods), index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1_000_000,
        }
    )


class AnalyzeDataQualityTest(unittest.TestCase):
    def test_empty_market_is_not_neutral(self):
        self.assertEqual(market_regime({})[0], "市场数据不足/待确认")
        self.assertEqual(market_heat({})[0], "待确认")

    def test_short_history_is_data_insufficient(self):
        metric = build_metrics("NEW", price_frame(periods=3), None)
        self.assertEqual(metric.observation_count, 3)
        self.assertEqual(technical_state(metric), "数据不足")

    def test_intraday_daily_bar_is_excluded(self):
        frame = price_frame(periods=2)
        extra = frame.iloc[[-1]].copy()
        extra.index = pd.DatetimeIndex(["2026-07-16"])
        combined = pd.concat([frame, extra])
        intraday = keep_completed_daily_bars(
            combined, datetime(2026, 7, 16, 10, 30, tzinfo=ET)
        )
        after_close = keep_completed_daily_bars(
            combined, datetime(2026, 7, 16, 17, 0, tzinfo=ET)
        )
        self.assertEqual(intraday.index[-1].date().isoformat(), "2026-07-15")
        self.assertEqual(after_close.index[-1].date().isoformat(), "2026-07-16")

    def test_snapshot_uses_downloaded_metric_and_replaces_whole_date(self):
        metric = build_metrics("TEST", price_frame(), None)
        holdings = pd.DataFrame([{"ticker": "TEST", "shares": 2, "avg_cost": 100}])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshots.csv"
            path.write_text(
                "snapshot_date,ticker,shares,avg_cost,last_price,market_value,cost_basis,unrealized_pnl,unrealized_pnl_pct\n"
                "2026-07-15,OLD,1,1,1,1,1,0,0\n",
                encoding="utf-8",
            )
            status = upsert_snapshot_log(holdings, {"TEST": metric}, path)
            rows = pd.read_csv(path)
            self.assertIn("已写入 2026-07-15", status)
            self.assertEqual(rows["ticker"].tolist(), ["TEST"])
            self.assertEqual(rows.iloc[0]["last_price"], metric.close)

    def test_drawdown_stops_when_holdings_or_shares_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshots.csv"
            path.write_text(
                "snapshot_date,ticker,shares,avg_cost,last_price,market_value,cost_basis,unrealized_pnl,unrealized_pnl_pct\n"
                "2026-07-14,AAA,1,10,10,10,10,0,0\n"
                "2026-07-15,BBB,1,10,10,10,10,0,0\n",
                encoding="utf-8",
            )
            self.assertIn("停止计算跨期回撤", portfolio_snapshot_drawdown(path))


if __name__ == "__main__":
    unittest.main()
