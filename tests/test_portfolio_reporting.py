"""Tests for read-only weekly reporting. Run: python -m unittest discover -s tests"""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from portfolio_reporting import (
    capm_statistics,
    holdings_table,
    load_external_flows,
    recent_trades,
    risk_statistics,
    time_weighted_returns,
    weekly_research_input,
)


class ReportingTests(unittest.TestCase):
    def test_holdings_weights_and_unrealized_pnl(self):
        rows = holdings_table(
            [{"ticker": "ABC", "shares": 2, "cost_basis": 80}],
            {"ABC": 50}, 200,
        )
        self.assertEqual(rows[0], ("ABC", 2.0, 100.0, 50.0, 20.0))

    def test_missing_prices_never_become_zero(self):
        rows = holdings_table([{"ticker": "ABC", "shares": 2, "cost_basis": 80}], {}, 200)
        self.assertIsNone(rows[0][2])
        self.assertIsNone(rows[0][4])

    def test_external_contribution_is_not_profit(self):
        index = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
        equity = pd.Series([100., 150., 165.], index=index)
        flows = pd.Series([50.], index=[index[1]])
        daily = time_weighted_returns(equity, flows)
        self.assertAlmostEqual(daily.iloc[0], 0)
        self.assertAlmostEqual(daily.iloc[1], 0.10)
        self.assertIsNone(time_weighted_returns(equity, None))

    def test_flow_file_missing_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_external_flows(Path(tmp) / "not_found.csv"))

    def test_capm_does_not_exponentiate_intercept(self):
        dates = pd.bdate_range("2026-01-01", periods=100)
        market = pd.Series([0.003 if i % 2 else -0.002 for i in range(100)], index=dates)
        portfolio = market + 0.001
        result = capm_statistics(portfolio, market, annual_rf=0.0)
        self.assertAlmostEqual(result["beta"], 1.0, places=6)
        self.assertAlmostEqual(result["alpha_annual_arithmetic_pct"], 25.2, places=6)

    def test_trade_audit_uses_fourteen_days(self):
        df = pd.DataFrame([
            {"Date": "2026-10-01", "Ticker": "OLD", "Reason": "MANUAL SELL"},
            {"Date": "2026-11-13", "Ticker": "NEW", "Reason": "MANUAL BUY",
             "Shares Bought": 2, "Buy Price": 5, "Cost Basis": 10},
        ])
        trades = recent_trades(df, "2026-11-17")
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0][1:4], ("NEW", "BUY", 10.0))

    def test_report_does_not_fabricate_research(self):
        output = weekly_research_input(
            [{"ticker": "ABC", "shares": 2, "cost_basis": 80}], {"ABC": 50},
            100, 200, pd.DataFrame(), "2026-11-17",
        )
        self.assertIn("Research | Research | Research", output)
        self.assertIn("N/A", output)
        self.assertIn("No transactions", output)

    def test_no_flow_adjusted_risk_without_verified_flows(self):
        dates = pd.bdate_range("2026-01-01", periods=30)
        equity = pd.Series([100 + i for i in range(30)], index=dates)
        self.assertIsNone(risk_statistics(equity, None))


if __name__ == "__main__":
    unittest.main()
