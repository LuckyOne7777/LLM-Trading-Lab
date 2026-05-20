import yfinance as yf
import os
import time
import random
import requests
import pandas as pd
from pandas import Series
from datetime import date
from typing import Any
from dateutil.relativedelta import relativedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def truncate(text: str, limit: int):
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."

def get_macro_news(n: int = 5, summary_limit: int = 200):
    """
    Fetch and format broad market (macro) news using yfinance.

      CRITICAL LIMITATION 
    -------------------------
    This function ONLY returns news available on the CURRENT DAY.
    It relies on `yf.Ticker("^GSPC").news`, which is subject to Yahoo Finance
    backend limitations:
      - No access to historical macro news
      - No pagination or date filtering
      - Increasing `n` does NOT retrieve older articles
      - Headline availability is non-deterministic and may change over time

    Treat the output strictly as a real-time snapshot of market headlines.

    Parameters
    ----------
    n : int, optional
        Maximum number of macro news headlines to include from today's
        available set. Defaults to 5.

    summary_limit : int, optional
        Maximum number of characters to include in the truncated summary.
        Defaults to 200.

    Returns
    -------
    str
        A newline-separated string of formatted macro news items in the form:
        "<TITLE> - <TRUNCATED SUMMARY>".

    Notes
    -----
    Uses the S&P 500 index ("^GSPC") as a proxy for general market news.
    Yahoo Finance may return fewer items than requested or none at all.
    """
    ticker = yf.Ticker("^GSPC")
    news_headlines = ticker.news[:n]
    output = []
    for item in news_headlines:
        content = item.get("content", {})
        titles = content.get("title", "").strip()
        raw_summary = (
            content.get("summary")
            or item.get("summary")
            or ""  # Fallback if neither exists
        )
        summaries = truncate(raw_summary, summary_limit)
        output.append(f"{titles} - {summaries}")
    return "\n".join(output)

# =========================================================
# CONFIG
# =========================================================

MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY")
ADANOS_API_KEY = os.getenv("ADANOS_API_KEY")
BASE_URL = "https://api.polygon.io"
ADANOS_API_BASE_URL = (os.getenv("ADANOS_API_BASE_URL") or "https://api.adanos.org").rstrip("/")
REQUEST_TIMEOUT = 20
ADANOS_REQUEST_TIMEOUT = 8
ADANOS_PLATFORMS = ("reddit", "x", "news", "polymarket")

session = requests.Session()

retry = Retry(
    total=2,
    backoff_factor=0.5,
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=frozenset(["GET"]),
)

session.mount("https://", HTTPAdapter(max_retries=retry))
session.mount("http://", HTTPAdapter(max_retries=retry))


# =========================================================
# SAFE HTTP LAYER
# =========================================================

def _request_json(url: str, params: dict) -> dict | list | None:
    if not MASSIVE_API_KEY:
        raise RuntimeError("Missing MASSIVE_API_KEY")

    params = dict(params)
    params["apiKey"] = MASSIVE_API_KEY

    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)

        # soft skip
        if resp.status_code == 404:
            return None

        if resp.status_code == 429:
            time.sleep(1.5 + random.random())
            return None

        resp.raise_for_status()
        return resp.json()

    except (requests.RequestException, ValueError):
        return None


def _request_adanos_json(platform: str, ticker: str) -> dict | None:
    if not ADANOS_API_KEY:
        return None

    try:
        resp = session.get(
            f"{ADANOS_API_BASE_URL}/{platform}/stocks/v1/stock/{ticker}",
            headers={"X-API-Key": ADANOS_API_KEY},
            timeout=ADANOS_REQUEST_TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except requests.RequestException:
        return None


def _format_adanos_sentiment_row(ticker: str, platform: str, payload: dict) -> str | None:
    if payload.get("found") is False:
        return None

    fields = [
        f"TICKER={ticker}",
        f"SOURCE={platform}",
        f"SENTIMENT={payload.get('sentiment_score', 'UNKNOWN')}",
        f"BUZZ={payload.get('buzz_score', 'UNKNOWN')}",
        f"MENTIONS={payload.get('mentions', 'UNKNOWN')}",
        f"BULLISH_PCT={payload.get('bullish_pct', 'UNKNOWN')}",
        f"BEARISH_PCT={payload.get('bearish_pct', 'UNKNOWN')}",
        f"TREND={payload.get('trend', 'UNKNOWN')}",
    ]
    return " | ".join(fields)


def get_adanos_sentiment_context(tickers, max_tickers: int = 12) -> str:
    """
    Return optional Adanos Market Sentiment context for already-allowed tickers.

    This helper never expands the model's trading universe. Callers should pass
    only portfolio tickers or the explicit IPO whitelist already included in the
    prompt.
    """
    normalized: list[str] = []
    for ticker in tickers:
        if not isinstance(ticker, str):
            continue
        value = ticker.strip().upper()
        if value and value not in normalized:
            normalized.append(value)
        if len(normalized) >= max_tickers:
            break

    lines = ["ADANOS_SENTIMENT_START"]
    if not normalized:
        lines.append("No eligible tickers supplied.")
    elif not ADANOS_API_KEY:
        lines.append("Optional Adanos Market Sentiment API disabled: ADANOS_API_KEY is not configured.")
    else:
        for ticker in normalized:
            for platform in ADANOS_PLATFORMS:
                payload = _request_adanos_json(platform, ticker)
                if not payload:
                    continue
                row = _format_adanos_sentiment_row(ticker, platform, payload)
                if row:
                    lines.append(row)
    lines.append("ADANOS_SENTIMENT_END")
    return "\n".join(lines)


# =========================================================
# FEATURE HELPERS
# =========================================================

def _safe_float(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _extract_value(d: dict, *keys: str) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict) and v.get("value") is not None:
            return _safe_float(v["value"])
    return None


def _completeness(fin: dict | None) -> float:
    if not fin:
        return 0.0

    fields = [
        fin.get("revenue"),
        fin.get("net_income"),
        fin.get("cash"),
        fin.get("total_debt"),
        fin.get("operating_cash_flow"),
    ]

    return round(sum(v is not None for v in fields) / len(fields), 2)

def _truncate(text: str, n: int = 220) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "..."


# =========================================================
# API LAYERS
# =========================================================

def _get_ipos(start: str, end: str) -> list[dict]:
    data = _request_json(
        f"{BASE_URL}/vX/reference/ipos",
        {
            "listing_date.gte": start,
            "listing_date.lte": end,
            "ipo_status": "history",
            "limit": 200,
        },
    )
    return data.get("results", []) if isinstance(data, dict) else []


def _get_ticker_details(ticker: str) -> dict | None:
    data = _request_json(
        f"{BASE_URL}/v3/reference/tickers/{ticker}",
        {},
    )
    if not isinstance(data, dict):
        return None
    return data.get("results")


def _get_financials(ticker: str) -> dict | None:
    data = _request_json(
        f"{BASE_URL}/vX/reference/financials",
        {
            "ticker": ticker,
            "limit": 1,
            "order": "desc",
            "sort": "filing_date",
        },
    )

    if not isinstance(data, dict):
        return None

    results = data.get("results", [])
    if not results:
        return None

    latest = results[0]
    fin = latest.get("financials", {}) or {}

    income = fin.get("income_statement", {}) or {}
    balance = fin.get("balance_sheet", {}) or {}
    cashflow = fin.get("cash_flow_statement", {}) or {}

    revenue = _extract_value(income, "revenues")
    net_income = _extract_value(income, "net_income_loss")

    cash = _extract_value(
        balance,
        "cash",
        "cash_and_cash_equivalents",
        "cash_equivalents",
    )

    debt = _extract_value(
        balance,
        "long_term_debt",
        "total_debt",
    )

    ocf = _extract_value(
        cashflow,
        "net_cash_flow_from_operating_activities",
    )

    return {
        "revenue": revenue,
        "net_income": net_income,
        "cash": cash,
        "total_debt": debt,
        "operating_cash_flow": ocf,
        "period": latest.get("end_date", ""),
        "data_completeness": _completeness({
            "revenue": revenue,
            "net_income": net_income,
            "cash": cash,
            "total_debt": debt,
            "operating_cash_flow": ocf,
        }),
    }


# =========================================================
# MAIN PIPELINE
# =========================================================

def get_ipo_universe(
    lookback_years: int = 3,
    min_market_cap: float = 100_000_000,
    max_results: int = 3,
) -> list[dict]:

    end = date.today()
    start = end - relativedelta(years=lookback_years)

    ipos = _get_ipos(str(start), str(end))
    if not ipos:
        return []

    candidates = []

    for ipo in ipos:
        ticker = (ipo.get("ticker") or "").upper().strip()
        if not ticker:
            continue

        details = _get_ticker_details(ticker)
        if not details:
            continue

        mcap = details.get("market_cap")
        if not isinstance(mcap, (int, float)) or mcap < min_market_cap:
            continue

        fin = _get_financials(ticker)

        candidates.append({
            "ticker": ticker,
            "name": details.get("name", ""),
            "description": details.get("description", ""),
            "sector": details.get("sic_description") or "Unknown",
            "listing_date": ipo.get("listing_date", ""),
            "market_cap": mcap,
            "financials": fin,
        })

        time.sleep(0.25 + random.random() * 0.2)

        if len(candidates) >= max_results:
            break

    return candidates

def enrich_ticker_universe(
    tickers: Series,
    sleep: bool = True,
) -> list[dict]:
    """
    Enrich tickers from a list.

    - Extracts tickers internally
    - Returns same schema as IPO universe
    """

    results = []

    for ticker in tickers:
        if not ticker:
            continue

        details = _get_ticker_details(ticker)
        if not details:
            continue

        mcap = details.get("market_cap")

        fin = _get_financials(ticker)

        results.append({
            "ticker": ticker,
            "name": details.get("name", ""),
            "description": details.get("description", ""),
            "sector": details.get("sic_description") or "UNKNOWN",
            "listing_date": details.get("list_date", ""),
            "market_cap": mcap,
            "financials": fin,
        })

        if sleep:
            time.sleep(0.25 + random.random() * 0.2)

    return results


# =========================================================
# PROMPT FORMATTER
# =========================================================

def format_universe_for_prompt(companies: list[dict]) -> str:
    if not companies:
        return "IPO_UNIVERSE_START\nIPO_UNIVERSE_END"

    def fmt(v):
        return "UNKNOWN" if v is None else f"{v/1_000_000:.1f}M"

    lines = ["IPO_UNIVERSE_START"]

    for c in companies:
        fin = c.get("financials") or {}

        mcap = c.get("market_cap")

        mcap_b = round(mcap / 1e9, 2) if isinstance(mcap, (int, float)) else "UNKNOWN"

        desc = c.get('description','UNKNOWN')
        if not isinstance(desc, str) or not desc.strip():
            desc = "UNKNOWN"

        lines.append(
            f"TICKER={c.get('ticker')} | "
            f"NAME={c.get('name')} | "
            f"IPO={c.get('listing_date')} | "
            f"MCAP_B={mcap_b} | "
            f"SECTOR={c.get('sector')} | "
            f"FIN=rev:{fmt(fin.get('revenue'))}, "
            f"ni:{fmt(fin.get('net_income'))}, "
            f"cash:{fmt(fin.get('cash'))}, "
            f"debt:{fmt(fin.get('total_debt'))}, "
            f"ocf:{fmt(fin.get('operating_cash_flow'))}, "
            f"data_completeness:{fin.get('data_completeness',0):.2f} | "
            f"DESC={_truncate(desc)}"
        )

    lines.append("IPO_UNIVERSE_END")
    return "\n".join(lines)


from pandas import Series
from datetime import date
import pandas as pd

MIN_MARKET_CAP = 200_000_000


def build_eligibility_series(
    tickers: Series,
    today: date | None = None,
    max_years: int = 3,
) -> str:
    """
    Returns:

    TICKER | IPO_STATUS | DAYS_REMAINING | MCAP_STATUS | BUY_ALLOWED
    """

    today = today or date.today()
    max_days = max_years * 365

    lines = []

    for ticker in tickers:
        if not isinstance(ticker, str) or not ticker:
            continue

        ticker = ticker.upper().strip()

        data = _get_ticker_details(ticker)

        # -------------------------
        # FAIL SAFE: no data
        # -------------------------
        if not data:
            lines.append(f"{ticker} | UNKNOWN | UNKNOWN | UNKNOWN | BUY_BLOCKED")
            continue

        # -------------------------
        # IPO LOGIC
        # -------------------------
        listing_date = data.get("listing_date") or data.get("list_date") or data.get("ipo_date")

        if not listing_date:
            lines.append(f"{ticker} | UNKNOWN | UNKNOWN | UNKNOWN | BUY_BLOCKED")
            continue

        try:
            ipo_date = pd.to_datetime(listing_date).date()
            days_since = (today - ipo_date).days
            remaining = max_days - days_since

            ipo_eligible = remaining > 0
            if remaining > 0:
                remaining_str = f"{remaining} DAYS LEFT"
            else:
                remaining_str = f"{remaining} DAYS OVERDUE"
 

        except Exception:
            lines.append(f"{ticker} | UNKNOWN | UNKNOWN | UNKNOWN | BUY_BLOCKED")
            continue

        # -------------------------
        # MARKET CAP LOGIC
        # -------------------------
        mcap = data.get("market_cap")

        if isinstance(mcap, (int, float)):
            mcap_eligible = mcap >= MIN_MARKET_CAP
            mcap_str = f"{mcap/1e9:.2f}B"
        else:
            mcap_eligible = False
            mcap_str = "UNKNOWN"

        # -------------------------
        # FINAL DECISION
        # -------------------------
        buy_allowed = ipo_eligible and mcap_eligible

        ipo_status = "ELIGIBLE" if ipo_eligible else "INELIGIBLE"
        mcap_status = "ELIGIBLE" if mcap_eligible else "INELIGIBLE"

        lines.append(
            f"{ticker} | {ipo_status} | {remaining_str} | {mcap_status} ({mcap_str}) | "
            f"{'BUY_ALLOWED' if buy_allowed else 'BUY_BLOCKED'}"
        )

    return "\n".join(lines)
def build_eligibility_series_from_universe(companies: list[dict], today: date | None = None, max_years: int = 3) -> str:
    today = today or date.today()
    max_days = max_years * 365
    lines = []

    for c in companies:
        ticker = c.get("ticker", "")
        mcap = c.get("market_cap")
        listing_date = c.get("listing_date")

        try:
            if listing_date:
                ipo_date = date.fromisoformat(listing_date)
            else:
                lines.append(f"{ticker} | UNKNOWN | UNKNOWN | UNKNOWN | BUY_BLOCKED")
                continue
            days_since = (today - ipo_date).days
            remaining = max_days - days_since
            ipo_eligible = remaining > 0
            remaining_str = f"{remaining} DAYS LEFT" if remaining > 0 else f"{abs(remaining)} DAYS OVERDUE"
        except Exception:
            lines.append(f"{ticker} | UNKNOWN | UNKNOWN | UNKNOWN | BUY_BLOCKED")
            continue

        if isinstance(mcap, (int, float)):
            mcap_eligible = mcap >= MIN_MARKET_CAP
            mcap_str = f"{mcap/1e9:.2f}B"
        else:
            mcap_eligible = False
            mcap_str = "UNKNOWN"

        buy_allowed = ipo_eligible and mcap_eligible
        lines.append(
            f"{ticker} | {'ELIGIBLE' if ipo_eligible else 'INELIGIBLE'} | {remaining_str} | "
            f"{'ELIGIBLE' if mcap_eligible else 'INELIGIBLE'} ({mcap_str}) | "
            f"{'BUY_ALLOWED' if buy_allowed else 'BUY_BLOCKED'}"
        )

    return "\n".join(lines)

# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    df = pd.DataFrame({"tickers": ["SNOW"]})
    universe = get_ipo_universe(lookback_years=3, max_results=3)
    output = enrich_ticker_universe(df["tickers"])
    print(format_universe_for_prompt(output))
    print(build_eligibility_series(df["tickers"]))
