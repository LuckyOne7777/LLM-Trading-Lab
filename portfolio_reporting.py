"""Read-only weekly research data export for the portfolio experiment.

Uses the existing snapshot/trade CSVs. Does not place trades or modify state.
Missing market prices, transaction rationale, or external cash flows are never
silently inferred. The research agent supplies investment scores and actions.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def _number(value):
    """Return a finite float or None, instead of silently converting missing data to zero."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if np.isfinite(result) else None


def _money(value):
    return "N/A" if value is None else f"${value:,.2f}"


def _percent(value):
    return "N/A" if value is None else f"{value:+.2f}%"


def load_external_flows(path: Path):
    """Read explicit external account cash flows: Date,Amount (positive deposits).

    Return None when not provided; an empty, valid CSV means there were no flows.
    Reject duplicate/invalid dates and invalid amounts rather than guessing.
    """
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if not {"Date", "Amount"}.issubset(df.columns):
        raise ValueError("External flow CSV must contain Date and Amount columns")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
    if df["Date"].isna().any() or not np.isfinite(df["Amount"]).all():
        raise ValueError("Invalid date or amount in external cash-flow CSV")
    return df.groupby("Date")["Amount"].sum()


def time_weighted_returns(equity: pd.Series, flows: pd.Series | None):
    """Daily TWR from end-of-day equity and same-day external flows.

    Assumes external contributions occur at end of day. Without explicit flow
    records, do not call raw equity changes investment returns.
    """
    if flows is None:
        return None
    values = pd.to_numeric(equity, errors="coerce").sort_index()
    if len(values) < 2 or values.isna().any() or (values <= 0).any():
        return None
    daily = []
    for i in range(1, len(values)):
        date = pd.Timestamp(values.index[i]).normalize()
        amount = float(flows.get(date, 0.0))
        daily.append((float(values.iloc[i]) - amount) / float(values.iloc[i - 1]) - 1.0)
    return pd.Series(daily, index=values.index[1:], dtype=float)


def capm_statistics(portfolio_returns, benchmark_returns, annual_rf=0.045):
    """Estimate CAPM on aligned daily returns; never compound the regression intercept.

    R-squared reflects fit, not statistical significance of alpha. Require an
    adequate sample and report alpha as descriptive, not proof of skill.
    """
    if portfolio_returns is None or benchmark_returns is None:
        return None
    aligned = pd.concat([portfolio_returns.rename("p"), benchmark_returns.rename("m")], axis=1).dropna()
    aligned = aligned[np.isfinite(aligned["p"]) & np.isfinite(aligned["m"])]
    if len(aligned) < 60 or aligned["m"].std() <= 0:
        return None
    rf_daily = (1.0 + annual_rf) ** (1.0 / 252.0) - 1.0
    x = aligned["m"].to_numpy() - rf_daily
    y = aligned["p"].to_numpy() - rf_daily
    beta, alpha_daily = np.polyfit(x, y, 1)
    r2 = float(np.corrcoef(x, y)[0, 1] ** 2)
    if not all(np.isfinite([beta, alpha_daily, r2])):
        return None
    return {
        "beta": float(beta),
        "alpha_annual_arithmetic_pct": float(alpha_daily) * 252.0 * 100.0,
        "r2": r2,
        "observations": len(aligned),
    }


def risk_statistics(equity: pd.Series, flows: pd.Series | None, benchmark_prices=None):
    """Return only validated flow-adjusted risk metrics, or None when unavailable."""
    returns = time_weighted_returns(equity, flows)
    if returns is None or len(returns) < 20:
        return None
    rf_daily = (1.0 + 0.045) ** (1.0 / 252.0) - 1.0
    sigma = float(returns.std(ddof=1))
    downside = float(np.sqrt(np.mean(np.minimum(returns - rf_daily, 0.0) ** 2)))
    index = (1.0 + returns).cumprod()
    index = pd.concat([pd.Series([1.0], index=[equity.index[0]]), index])
    drawdown = float((index / index.cummax() - 1.0).min() * 100.0)
    result = {
        "return_pct": float((1.0 + returns).prod() - 1.0) * 100.0,
        "max_drawdown_pct": drawdown,
        "sharpe": ((returns.mean() - rf_daily) / sigma * np.sqrt(252.0)) if sigma > 0 else None,
        "sortino": ((returns.mean() - rf_daily) / downside * np.sqrt(252.0)) if downside > 0 else None,
    }
    if benchmark_prices is not None:
        b = pd.to_numeric(benchmark_prices, errors="coerce").sort_index()
        b_returns = b.pct_change().dropna()
        capm = capm_statistics(returns, b_returns)
        if capm:
            result.update(capm)
        common = b.reindex([equity.index[0], equity.index[-1]])
        if common.notna().all() and common.iloc[0] > 0:
            result["benchmark_return_pct"] = float((common.iloc[-1] / common.iloc[0] - 1.0) * 100.0)
            result["excess_return_pp"] = result["return_pct"] - result["benchmark_return_pct"]
    return result


def holdings_table(holdings, prices, equity):
    """Build positions with validated shares, prices, cost bases and true equity weights."""
    rows = []
    for position in holdings:
        ticker = str(position.get("ticker", "")).upper().strip()
        shares = _number(position.get("shares"))
        if not ticker or shares is None or shares <= 0:
            continue
        price = _number(prices.get(ticker))
        basis = _number(position.get("cost_basis"))
        value = shares * price if price is not None else None
        weight = 100 * value / equity if value is not None and equity > 0 else None
        pnl = value - basis if value is not None and basis is not None else None
        rows.append((ticker, shares, value, weight, pnl))
    return rows


def recent_trades(trade_log, as_of, days=14):
    """Parse the existing trade-log schema, retaining only the most recent window."""
    cutoff = pd.Timestamp(as_of).normalize() - pd.Timedelta(days=days)
    if trade_log is None or trade_log.empty:
        return []
    data = trade_log.copy()
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data[data["Date"].between(cutoff, pd.Timestamp(as_of).normalize())].sort_values("Date")
    trades = []
    for _, row in data.iterrows():
        reason = str(row.get("Reason", ""))
        action = "BUY" if "BUY" in reason.upper() else ("SELL" if "SELL" in reason.upper() else None)
        if action is None:
            continue
        price = _number(row.get("Buy Price" if action == "BUY" else "Sell Price"))
        shares = _number(row.get("Shares Bought" if action == "BUY" else "Shares Sold"))
        amount = _number(row.get("Cost Basis")) if action == "BUY" else (
            price * shares if price is not None and shares is not None else None
        )
        pnl = _number(row.get("PnL")) if action == "SELL" else None
        trades.append((row["Date"].strftime("%Y-%m-%d"), str(row.get("Ticker", "")),
                       action, amount, pnl, reason or "Not recorded"))
    return trades


def weekly_research_input(holdings, prices, cash, total_equity, trade_log, as_of,
                          equity_history=None, external_flows=None, benchmark_prices=None):
    """Produce Markdown facts for weekly research; scores/theses remain research tasks."""
    equity = _number(total_equity)
    cash = _number(cash)
    if equity is None or equity <= 0:
        raise ValueError("A positive current portfolio equity is required")
    lines = [
        "# Weekly Portfolio Research Data",
        "",
        f"Data as of: {pd.Timestamp(as_of).date()}",
        "Research policy: equity_portfolio_strategy.md",
        "This document contains portfolio facts, not trading instructions.",
        "",
        "## C. Portfolio Snapshot and Benchmark Comparison",
        "",
        "| Metric | Portfolio | SPY / Comparison | Period / Notes |",
        "|---|---:|---:|---|",
        f"| Total value | {_money(equity)} | N/A | As of report date |",
        f"| Cash amount and percentage | {_money(cash)} ({100 * cash / equity:.2f}%) | N/A | As of report date |",
    ]
    stats = risk_statistics(equity_history, external_flows, benchmark_prices) if equity_history is not None else None
    for key, title, suffix in [
        ("return_pct", "Return", "%"), ("excess_return_pp", "Excess return", " pp"),
        ("max_drawdown_pct", "Drawdown", "%"), ("sharpe", "Sharpe (annualized)", ""),
        ("sortino", "Sortino (annualized)", ""), ("beta", "Beta", ""),
        ("alpha_annual_arithmetic_pct", "CAPM alpha (arithmetic annualized)", "%"),
    ]:
        v = stats.get(key) if stats else None
        formatted = "N/A" if v is None else f"{v:+.2f}{suffix}"
        benchmark = ""
        if key == "return_pct" and stats and "benchmark_return_pct" in stats:
            benchmark = _percent(stats["benchmark_return_pct"])
        lines.append(f"| {title} | {formatted} | {benchmark or 'N/A'} | Verified flows required |")
    if external_flows is None:
        lines += ["", "> External cash-flow CSV unavailable. Returns and risk metrics are N/A, not raw equity returns."]
    if stats and "alpha_annual_arithmetic_pct" in stats:
        lines += ["", f"> CAPM fit R²={stats['r2']:.3f}, n={stats['observations']}; alpha is descriptive, not proven skill."]
    lines += [
        "",
        "## D. Current Holdings",
        "",
        "| Ticker | Shares | Market Value | Weight | Unrealized P&L | Score | Thesis Status | Action |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for ticker, shares, value, weight, pnl in holdings_table(holdings, prices, equity):
        weight_text = "N/A" if weight is None else f"{weight:.2f}%"
        lines.append(f"| {ticker} | {shares:.4f} | {_money(value)} | {weight_text} | "
                     f"{_money(pnl)} | Research | Research | Research |")
    lines += [
        "",
        "## E. Last 14 Days - Transaction Audit",
        "",
        "| Date | Ticker | Action | Amount | Realized P&L | Recorded Reason | Review Finding |",
        "|---|---|---|---:|---:|---|---|",
    ]
    trades = recent_trades(trade_log, as_of)
    for date, ticker, action, amount, pnl, reason in trades:
        safe_reason = reason.replace("|", "/").replace("\n", " ")
        lines.append(f"| {date} | {ticker} | {action} | {_money(amount)} | "
                     f"{_money(pnl) if action == 'SELL' else 'N/A'} | {safe_reason} | Research |")
    if not trades:
        lines.append("| N/A | N/A | No transactions | N/A | N/A | N/A | N/A |")
    lines += [
        "",
        "## F. Market Environment and Opportunity Discovery",
        "",
        "Independent research required: macro, sectors, candidates, catalysts, and valuations.",
        "Do not fabricate investment scores or recommendations from transaction data.",
    ]
    return "\n".join(lines) + "\n"
