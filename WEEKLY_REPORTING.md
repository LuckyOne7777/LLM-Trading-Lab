# Weekly Portfolio Reporting

The existing trading script can print a research-ready Markdown supplement when
weekly mode is enabled. Trading, stops, CSV schemas, and order execution are unchanged.

## What is generated

- Portfolio equity and cash percentage.
- Current positions, market value, equity weights, and unrealized P&L.
- A 14-day audit of recorded buys and sells, including recorded reasons.
- Returns, Sharpe, Sortino, drawdown, and CAPM **only when explicit external
  cash-flow records are available**. Otherwise these metrics are shown as N/A.
- Fields requiring new research (investment score, thesis, recommendation, and
  market opportunity discovery) remain marked `Research` rather than fabricated.

The full four-week trade log remains visible in the legacy output for reference.

## Optional: verified external cash flows

Create `external_cash_flows.csv` in the active portfolio data directory
(the same directory containing `chatgpt_trade_log.csv`).

CSV columns:

```csv
Date,Amount
2026-10-01,250.00
2026-11-03,-50.00
```

Positive amounts are deposits and negative amounts are withdrawals.
Use **actual external account transfers**, not purchases or sales.
An otherwise empty file containing only `Date,Amount` explicitly means
that you have verified no external transfers over the full equity history.

The current TWR assumes transfers occur **at the end of the recorded day**.
For intraday transfers, use the appropriate valuation/cash flow convention.
Do not construct an empty file without verifying transaction history first.

The script no longer uses unexplained cash changes as proof of deposits in
the six-month performance chart. Raw equity curves are explicitly labeled
as unadjusted when verified flow data is missing.

## Research workflow

Use these inputs together:

1. `equity_portfolio_strategy.md` — governing investment policy.
2. `Start Your Own/weekly prompt.md` — independent research mandate.
3. The weekly script output — account facts and recent transactions.
4. The previous weekly research report, if available.

The existing trade log is the source of transaction facts. Research findings
and recommendations are not stored or inferred from trade rows alone.

## Tests

Install the repository requirements and run:

```bash
python -m unittest discover -s tests -v
```

No test places a trade, connects to a broker, or requests market data.

## Known limitations

- Benchmark prices use the current upstream `^GSPC` pipeline, which is
  price-only; for fully dividend-adjusted total returns, use SPY adjusted
  closes or a proper total-return index series.
- The optional cash-flow file is not a substitute for a full cash ledger,
  especially when there are split-adjustment and fee effects.
- New positions are valued using the last market prices supplied to the
  daily report; missing data stays N/A rather than becoming zero.
