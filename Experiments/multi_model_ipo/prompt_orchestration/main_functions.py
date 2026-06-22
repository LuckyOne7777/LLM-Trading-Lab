from __future__ import annotations

from datetime import date
from typing import Iterable

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import  Iterable

import pandas as pd
import yfinance as yf
from dateutil.relativedelta import relativedelta

from .get_prompt_data.fetching import *
from .get_prompt_data.utilities import *
from .get_prompt_data.config import *

# =========================================================
# MAIN UNIVERSE
# =========================================================


def get_ipo_universe(lookback_years=3, max_results=25):
    end = date.today()
    start = end - relativedelta(years=lookback_years)

    ipos = get_ipos(str(start), str(end))

    if not ipos:
        return []

    tickers = list({
        normalize_ticker(ipo.get("symbol"))
        for ipo in ipos
        if normalize_ticker(ipo.get("symbol"))
    })

    results = []

    with ThreadPoolExecutor(max_workers=DEFAULT_MAX_WORKERS) as ex:
        futures = {ex.submit(enrich_company, ticker): ticker for ticker in tickers}

        for fut in as_completed(futures):
            result = fut.result()
            if result:
                results.append(result)


    # BETTER RANKING
    def score(x):
        mcap = x.get("market_cap", 0)

        liquidity = x.get("dollar_volume", 0)

        momentum = x.get("mom_3m", 0)

        return (
            (mcap / 1e9)
            + (liquidity / 1e7)
            + momentum
        )

    results.sort(key=score, reverse=True)

    return results[:max_results]

def build_eligibility_series(
    tickers,
    min_mcap=200_000_000,
    years_back=3,
):
    """
    Flexible overload-style wrapper.

    Supports:
    - pandas Series
    - list[str]
    - list[dict] (from `get_ipo_universe()`)
    - tuple[str]
    - set[str]
    - single ticker string

    Usage:
        build_eligibility_series(df["ticker"])

        build_eligibility_series(
            df["ticker"],
            get_polygon_details=_get_polygon_ticker_details,
            min_mcap=MIN_MARKET_CAP
        )
    """

    # ---------------------------------------------------------
    # normalize input
    # ---------------------------------------------------------

    if tickers is None:
        return ""

    if isinstance(tickers, str):
        tickers = [tickers]

    elif isinstance(tickers, pd.Series):
        tickers = tickers.dropna().astype(str).tolist()

    elif isinstance(tickers, list):
        if tickers and isinstance(tickers[0], dict):
            # if tickers is list[dict]
            return build_eligibility_series_from_universe(tickers)

    elif not isinstance(tickers, Iterable):
        tickers = [str(tickers)]

    # ---------------------------------------------------------
    # defaults
    # ---------------------------------------------------------

    today = date.today()
    cutoff = today.replace(year=today.year - years_back)

    lines = []

    # ---------------------------------------------------------
    # main logic
    # ---------------------------------------------------------

    for t in tickers:
        t = normalize_ticker(t)

        if not t:
            continue

        ticker_data = get_fmp_data(t)

        market_data = get_market_data(t)
        dollar_volume = market_data.get("dollar_volume", 0)

        if not ticker_data:
            lines.append(f"{t} | NO_DATA | BUY_BLOCKED | NOT_ELIGIBLE")
            continue

        listing = parse_date(
            ticker_data["profile"].get("ipoDate")
        )

        if not listing:
            lines.append(f"{t} | NO_DATE | BUY_BLOCKED | NOT_ELIGIBLE")
            continue

        ipo_ok = listing >= cutoff
        if ipo_ok:
            expiry = listing + relativedelta(years=years_back)
            days_left = (expiry - today).days
            
            days_left = str(days_left)
        else:
            days_left = "NOT_ELIGIBLE"

        mcap = ticker_data["quote"].get("marketCap")

        mcap_ok = mcap is not None and mcap >= MIN_MARKET_CAP

        liq_ok = (dollar_volume >= MIN_DOLLAR_VOLUME)

        if ipo_ok and mcap_ok and liq_ok:
            status = "BUY_ALLOWED"
        else:
            reasons = []
            if not ipo_ok:
                reasons.append("IPO_OUT_OF_WINDOW")
            if not mcap_ok:
                reasons.append("MCAP_TOO_SMALL")
            if not liq_ok:
                reasons.append("ILLIQUID")
            status = "BUY_BLOCKED:" + "+".join(reasons)

        lines.append(
            f"{t} | "
            f"IPO={listing} | "
            f"IPO_OK={ipo_ok} | "
            f"DAYS_LEFT={days_left}"
            f"MCAP={fmt_billions(mcap)} | "
            f"MCAP_OK={mcap_ok} | "
            f"DOLLAR_VOL={fmt_millions(dollar_volume)} | "
            f"LIQ_OK={liq_ok} | "
            f"{status}"
        )

    return "\n".join(lines)

def build_eligibility_series_from_universe(universe: list[dict]) -> str:
    """
    Takes the already-enriched list of companies from get_ipo_universe()
    and builds an eligibility report string without making any additional
    API calls — all required data is already on each company dict.
    """
    if not universe:
        return ""

    today = date.today()
    cutoff = today - relativedelta(years=DEFAULT_LOOKBACK_YEARS)

    lines = []

    for c in universe:
        ticker = normalize_ticker(c.get("ticker", ""))

        if not ticker:
            continue

        listing = parse_date(c.get("listing_date"))
        ipo_ok = listing is not None and listing >= cutoff

        if ipo_ok and listing is not None:
            expiry = listing + relativedelta(years=3)
            days_left = (expiry - today).days
            
            days_left = str(days_left)
        else:
            days_left = "NOT_ELIGIBLE"

        mcap = safe_float(c.get("market_cap"))
        mcap_ok = mcap is not None and mcap >= MIN_MARKET_CAP

        liq_ok = (c.get("dollar_volume") or 0) >= MIN_DOLLAR_VOLUME

        if ipo_ok and mcap_ok and liq_ok:
            status = "BUY_ALLOWED"
        else:
            reasons = []
            if not ipo_ok:
                reasons.append("IPO_OUT_OF_WINDOW")
            if not mcap_ok:
                reasons.append("MCAP_TOO_SMALL")
            if not liq_ok:
                reasons.append("ILLIQUID")
            status = "BUY_BLOCKED:" + "+".join(reasons)

        lines.append(
            f"{ticker} | "
            f"IPO={c.get('listing_date')} | "
            f"IPO_OK={ipo_ok} | "
            f"DAYS_LEFT={days_left} | "
            f"MCAP={fmt_billions(mcap)} | "
            f"MCAP_OK={mcap_ok} | "
            f"DOLLAR_VOL={fmt_millions(c.get('dollar_volume'))} | "
            f"LIQ_OK={liq_ok} | "
            f"{status}"
        )

    return "\n".join(lines)

import yfinance as yf
from datetime import date


def truncate(text, limit=200):
    if not text:
        return ""
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def get_macro_news(n=5):
    """
    PURE DATA LAYER ONLY.

    No interpretation.
    No sentiment.
    No labeling.

    Goal:
    Feed LLM raw macro + market state so it can infer structure itself.
    """

    lines = []

    # -------------------------------------------------
    # SPY RAW MARKET DATA
    # -------------------------------------------------

    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(period="5d")

        if len(hist) > 0:
            latest = hist.iloc[-1]

            lines.append(
                f"SPY_CLOSE={round(float(latest['Close']), 2)}\n"
                f"SPY_VOLUME={round(float(latest['Volume']), 2)}"
            )
    except:
        lines.append("SPY=ERROR")

    # -------------------------------------------------
    # VIX RAW DATA
    # -------------------------------------------------

    try:
        vix = yf.Ticker("^VIX")
        vh = vix.history(period="5d")

        if len(vh) > 0:
            latest = vh.iloc[-1]
            lines.append(f"VIX_CLOSE={round(float(latest['Close']), 2)}")
    except:
        lines.append("VIX=ERROR")

    # -------------------------------------------------
    # TREASURY YIELD (10Y proxy via ^TNX)
    # -------------------------------------------------

    try:
        tn = yf.Ticker("^TNX")
        th = tn.history(period="5d")

        if len(th) > 0:
            latest = th.iloc[-1]
            lines.append(f"TNX_10Y={round(float(latest['Close']), 2)}")
    except:
        lines.append("TNX=ERROR")

    # -------------------------------------------------
    # MACRO HEADLINES (RAW ONLY)
    # -------------------------------------------------

    try:
        news = yf.Ticker("^GSPC").news or []
    except:
        news = []

    lines.append("HEADLINES_START")

    for item in news[:n]:
        content = item.get("content", {}) if isinstance(item, dict) else {}

        title = content.get("title") or ""
        summary = content.get("summary") or item.get("summary") or ""

        lines.append(
            f"- {title} | {truncate(summary, 180)}"
        )

    lines.append("HEADLINES_END")

    # -------------------------------------------------
    # RETURN RAW BLOCK
    # -------------------------------------------------

    return "MACRO_RAW_START\n" + "\n".join(lines) + "\nMACRO_RAW_END"

# =========================================================
# TEST
# =========================================================

if __name__ == "__main__":
    universe = get_ipo_universe(
        lookback_years=3,
        max_results=15,
    )

    print(format_universe_for_prompt(universe))