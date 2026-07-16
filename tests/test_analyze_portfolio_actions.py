import unittest

import pandas as pd

from analyze_portfolio import QuoteMetrics, action_for_holding


def metric(rsi_14=None):
    return QuoteMetrics(
        ticker="TEST",
        close=50.0,
        ret_1d_pct=None,
        ret_5d_pct=None,
        ret_20d_pct=5.0,
        ret_60d_pct=None,
        vol_30d_ann_pct=None,
        max_drawdown_1y_pct=None,
        sma_20_gap_pct=None,
        sma_50_gap_pct=None,
        sma_200_gap_pct=None,
        rsi_14=rsi_14,
        macd_hist=None,
        atr_14_pct=None,
        volume_ratio_20d=None,
        beta_spy=None,
        rel_spy_20d_pct=None,
        rel_spy_60d_pct=None,
        price_as_of="2026-07-15",
        observation_count=252,
    )


class HoldingActionTest(unittest.TestCase):
    def test_missing_thresholds_do_not_turn_pnl_into_action(self):
        action, reasons = action_for_holding(pd.Series(dtype=object), metric(rsi_14=80), -50.0, "市场分化/中性")
        self.assertEqual(action, "继续持有")
        self.assertNotIn("止损", " ".join(reasons))

    def test_explicit_stop_line_can_trigger_review(self):
        row = pd.Series({"stop_loss_pct": -10})
        action, reasons = action_for_holding(row, metric(), -12.0, "市场分化/中性")
        self.assertEqual(action, "卖出审查")
        self.assertIn("用户明确", " ".join(reasons))

    def test_explicit_trim_line_requires_overheating_confirmation(self):
        row = pd.Series({"trim_profit_pct": 40})
        action, _ = action_for_holding(row, metric(rsi_14=80), 50.0, "市场分化/中性")
        self.assertEqual(action, "减仓候选")


if __name__ == "__main__":
    unittest.main()
