"""Utilities for maintaining the ChatGPT micro-cap portfolio.

This module rewrites the original script to:
- Centralize market data fetching with a robust Yahoo->Stooq fallback
- Ensure ALL price requests go through the same accessor
- Handle empty Yahoo frames (no exception) so fallback actually triggers
- Normalize Stooq output to Yahoo-like columns
- Make weekend handling consistent and testable
- Keep behavior and CSV formats compatible with prior runs

Notes:
- Some tickers/indices are not available on Stooq (e.g., ^RUT). These stay on Yahoo.
- Stooq end date is exclusive; we add +1 day for ranges.
- "Adj Close" is set equal to "Close" for Stooq to match downstream expectations.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime
from pathlib import Path
from typing import Any, cast
import os
import warnings

import numpy as np
import pandas as pd
import yfinance as yf
import json
import logging

from market_enrichment import build_market_enrichment

# Optional pandas-datareader import for Stooq access
try:
    import pandas_datareader.data as pdr
    _HAS_PDR = True
except Exception:
    _HAS_PDR = False

# -------- AS-OF override --------
ASOF_DATE: pd.Timestamp | None = None
OVERRIDE_CASH: float | None = None

def set_asof(date: str | datetime.datetime | pd.Timestamp | None) -> None:
    """Set a global 'as of' date so the script treats that day as 'today'. Use 'YYYY-MM-DD' format."""
    global ASOF_DATE
    if date is None:
        print("No prior date passed. Using today's date...")
        ASOF_DATE = None
        return
    ASOF_DATE = pd.Timestamp(date).normalize()
    pure_date = ASOF_DATE.date()

    print(f"Setting date as {pure_date}.")

# Allow env var override:  ASOF_DATE=YYYY-MM-DD python trading_script.py
_env_asof = os.environ.get("ASOF_DATE")
if _env_asof:
    set_asof(_env_asof)

def set_override_cash(cash: float | None) -> None:
    """Set a global override for the cash balance."""
    global OVERRIDE_CASH
    OVERRIDE_CASH = cash
    if cash is not None:
        logger.info(f"Cash override set to: ${cash:.2f}")

def _effective_now() -> datetime.datetime:
    return (ASOF_DATE.to_pydatetime() if ASOF_DATE is not None else datetime.datetime.now())

# ------------------------------
# Globals / file locations
# ------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR  # Save files alongside this script by default
PORTFOLIO_CSV = DATA_DIR / "Start Your Own" / "chatgpt_portfolio_update.csv"
TRADE_LOG_CSV = DATA_DIR / "Start Your Own" / "chatgpt_trade_log.csv"
DEFAULT_BENCHMARKS = ["IWO", "XBI", "SPY", "IWM"]

# Set up logger for this module
logger = logging.getLogger(__name__)

# Log initial global state configuration (only when run as main script)
def _log_initial_state():
    """Log the initial global file path configuration."""
    logger.info("=== Trading Script Initial Configuration ===")
    logger.info("Script directory: %s", SCRIPT_DIR)
    logger.info("Data directory: %s", DATA_DIR)
    logger.info("Portfolio CSV: %s", PORTFOLIO_CSV)
    logger.info("Trade log CSV: %s", TRADE_LOG_CSV)
    logger.info("Default benchmarks: %s", DEFAULT_BENCHMARKS)
    logger.info("==============================================")

# ------------------------------
# Configuration helpers — benchmark tickers (tickers.json)
# ------------------------------

def _read_json_file(path: Path) -> dict | None:
    """Read and parse JSON from `path`. Return dict on success, None if not found or invalid.

    - FileNotFoundError -> return None
    - JSON decode error -> log a warning and return None
    - Other IO errors -> log a warning and return None
    """
    try:
        logger.info("Reading JSON file: %s", path)
        with path.open("r", encoding="utf-8") as fh:
            result = json.load(fh)
            logger.info("Successfully read JSON file: %s", path)
            return result
    except FileNotFoundError:
        logger.info("JSON file not found: %s", path)
        return None
    except json.JSONDecodeError as exc:
        logger.warning("tickers.json present but malformed: %s -> %s. Falling back to defaults.", path, exc)
        return None
    except Exception as exc:
        logger.warning("Unable to read tickers.json (%s): %s. Falling back to defaults.", path, exc)
        return None

def load_benchmarks(script_dir: Path | None = None) -> list[str]:
    """Return a list of benchmark tickers.

    Looks for a `tickers.json` file in either:
      - script_dir (if provided) OR the module SCRIPT_DIR, and then
      - script_dir.parent (project root candidate).

    Expected schema:
      {"benchmarks": ["IWO", "XBI", "SPY", "IWM"]}

    Behavior:
    - If file missing or malformed -> return DEFAULT_BENCHMARKS copy.
    - If 'benchmarks' key missing or not a list -> log warning and return defaults.
    - Normalizes tickers (strip, upper) and preserves order while removing duplicates.
    """
    base = Path(script_dir) if script_dir else SCRIPT_DIR
    candidates = [base, base.parent]

    cfg = None
    cfg_path = None
    for c in candidates:
        p = (c / "tickers.json").resolve()
        data = _read_json_file(p)
        if data is not None:
            cfg = data
            cfg_path = p
            break

    if not cfg:
        return DEFAULT_BENCHMARKS.copy()

    benchmarks = cfg.get("benchmarks")
    if not isinstance(benchmarks, list):
        logger.warning("tickers.json at %s missing 'benchmarks' array. Falling back to defaults.", cfg_path)
        return DEFAULT_BENCHMARKS.copy()

    seen = set()
    result: list[str] = []
    for t in benchmarks:
        if not isinstance(t, str):
            continue
        up = t.strip().upper()
        if not up:
            continue
        if up not in seen:
            seen.add(up)
            result.append(up)

    return result if result else DEFAULT_BENCHMARKS.copy()


# ------------------------------
# Date helpers
# ------------------------------

def last_trading_date(today: datetime.datetime | None = None) -> pd.Timestamp:
    """Return last trading date (Mon–Fri), mapping Sat/Sun -> Fri and pre-market hours to previous day.

    If running before 9:30 AM ET on a weekday, uses the previous trading day since
    market data for the current day may not be available yet.

    IMPORTANT: Uses ET timezone to determine the date, not local timezone, to handle
    international users correctly (e.g., Israel, Europe, Asia).
    """
    global ASOF_DATE
    if ASOF_DATE is not None and today is None:
        return ASOF_DATE

    try:
        import pytz
        from datetime import datetime as dt_module

        et_tz = pytz.timezone('US/Eastern')

        if today is not None:
            # If explicit date provided, use it
            dt = pd.Timestamp(today)
        else:
            # If no explicit date, use the effective 'now' which respects ASOF_DATE
            dt = pd.Timestamp(_effective_now())

        # Get current time in ET timezone (not local timezone!)
        # This ensures that users in any timezone get consistent behavior.
        # For naive datetimes we treat the value as ET directly (the script
        # is always operating on US market hours, so this is the correct assumption).
        if dt.tzinfo is None:
            dt = pd.Timestamp(et_tz.localize(dt.to_pydatetime()))
        else:
            # If already timezone-aware, convert to ET
            dt = pd.Timestamp(dt.astimezone(et_tz))

        # Handle weekends first
        if dt.weekday() == 5:  # Sat -> Fri
            friday_date = (dt - pd.Timedelta(days=1)).normalize()
            logger.info("Script running on Saturday - using Friday's data (%s) instead of today's date", friday_date.date())
            return friday_date
        if dt.weekday() == 6:  # Sun -> Fri
            friday_date = (dt - pd.Timedelta(days=2)).normalize()
            logger.info("Script running on Sunday - using Friday's data (%s) instead of today's date", friday_date.date())
            return friday_date

        # For weekdays, check if we're before market hours or after market close
        # Market hours are 9:30 AM to 4:00 PM ET
        # After market close (4:00 PM ET), use current day for logging trades
        market_open_time = dt_module.strptime("09:30", "%H:%M").time()
        market_close_time = dt_module.strptime("16:01", "%H:%M").time()  # 4:01 PM ET

        # Use previous trading day if before market open
        if dt.time() < market_open_time:
            reason = f"before market hours ({dt.strftime('%H:%M')} ET)"
            use_previous = True
        elif dt.time() < market_close_time:
            reason = f"market still open ({dt.strftime('%H:%M')} ET, market closes at 4:00 PM ET)"
            use_previous = True
        else:
            use_previous = False

        if use_previous:
            prev_date = dt - pd.Timedelta(days=1)
            # If previous day was a weekend, go back further
            while prev_date.weekday() >= 5:  # Sat=5, Sun=6
                prev_date = prev_date - pd.Timedelta(days=1)
            logger.info("Script running %s - using previous trading day (%s)",
                       reason, prev_date.date())
            return prev_date.normalize()

        return dt.normalize()

    except Exception as e:
        logger.warning("Timezone conversion failed: %s. Using fallback to naive datetime.", e)
        # If timezone handling fails, fall back to naive datetime with conservative logic
        dt = pd.Timestamp(today or _effective_now())
        # Use previous day if before 9 AM or after 9 PM local (conservative approach)
        if dt.hour < 9 or dt.hour >= 21:
            prev_date = dt - pd.Timedelta(days=1)
            # If previous day was a weekend, go back further
            while prev_date.weekday() >= 5:  # Sat=5, Sun=6
                prev_date = prev_date - pd.Timedelta(days=1)
            reason = "before 9 AM local" if dt.hour < 9 else "after 9 PM local"
            logger.info("Script running %s - using previous trading day (%s)",
                       reason, prev_date.date())
            return prev_date.normalize()
        return dt.normalize()

def check_weekend() -> str:
    """Backwards-compatible wrapper returning ISO date string for last trading day."""
    return last_trading_date().date().isoformat()

def trading_day_window(target: datetime.datetime | None = None) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) window for the last trading day (Fri on weekends)."""
    d = last_trading_date(target)
    return d, (d + pd.Timedelta(days=1))


# ------------------------------
# Data access layer
# ------------------------------

# Known Stooq symbol remaps for common indices
STOOQ_MAP = {
    "^GSPC": "^SPX",  # S&P 500
    "^DJI": "^DJI",   # Dow Jones
    "^IXIC": "^IXIC", # Nasdaq Composite
    # "^RUT": not on Stooq; keep Yahoo
}

# Symbols we should *not* attempt on Stooq
STOOQ_BLOCKLIST = {"^RUT"}


# ------------------------------
# Data access layer (UPDATED)
# ------------------------------

@dataclass
class FetchResult:
    df: pd.DataFrame
    source: str  # "yahoo" | "stooq-pdr" | "stooq-csv" | "yahoo:<proxy>-proxy" | "empty"

def _to_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            pass
    return df

def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    # Flatten multiIndex frame so we can lazily lookup values by index.
    if isinstance(df.columns, pd.MultiIndex):
        try:
            # If the second level is the same ticker for all cols, drop it
            if len(set(df.columns.get_level_values(1))) == 1:
                df = df.copy()
                df.columns = df.columns.get_level_values(0)
            else:
                # multiple tickers: flatten with join
                df = df.copy()
                df.columns = ["_".join(map(str, t)).strip("_") for t in df.columns.to_flat_index()]
        except Exception:
            df = df.copy()
            df.columns = ["_".join(map(str, t)).strip("_") for t in df.columns.to_flat_index()]

    # Ensure all expected columns exist
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c not in df.columns:
            df[c] = np.nan
    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"]
    cols = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    return df[cols]

def _yahoo_download(ticker: str, **kwargs: Any) -> pd.DataFrame:
    """Call yfinance.download with a real UA and silence all chatter."""
    import io, logging
    from contextlib import redirect_stderr, redirect_stdout

    kwargs.setdefault("progress", False)
    kwargs.setdefault("threads", False)

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    buf = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                df = cast(pd.DataFrame, yf.download(ticker, **kwargs))
        except Exception:
            return pd.DataFrame()
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

def _stooq_csv_download(ticker: str, start: pd.Timestamp, end: pd.Timestamp, _delay: float = 0.5) -> pd.DataFrame:
    """Fetch OHLCV from Stooq CSV endpoint (daily). Good for US tickers and many ETFs.

    _delay: seconds to sleep before the request to avoid Stooq rate-limiting when
    called in a tight loop over many tickers. Pass 0 to disable.
    """
    import requests, io, time
    if ticker in STOOQ_BLOCKLIST:
        return pd.DataFrame()
    t = STOOQ_MAP.get(ticker, ticker)

    # Stooq daily CSV: lowercase; equities/ETFs use .us, indices keep ^ prefix
    if not t.startswith("^"):
        sym = t.lower()
        if not sym.endswith(".us"):
            sym = f"{sym}.us"
    else:
        sym = t.lower()

    url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
    try:
        if _delay > 0:
            time.sleep(_delay)
        r = requests.get(url, timeout=10)
        if r.status_code != 200 or not r.text.strip():
            return pd.DataFrame()
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty:
            return pd.DataFrame()

        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        df.sort_index(inplace=True)

        # Filter to [start, end) (Stooq end is exclusive)
        df = df.loc[(df.index >= start.normalize()) & (df.index < end.normalize())]

        # Normalize to Yahoo-like schema
        if "Adj Close" not in df.columns:
            df["Adj Close"] = df["Close"]
        return df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
    except Exception:
        return pd.DataFrame()

def _stooq_download(
    ticker: str,
    start: datetime | pd.Timestamp,
    end: datetime | pd.Timestamp,
) -> pd.DataFrame:
    """Fetch OHLCV from Stooq via pandas-datareader; returns empty DF on failure."""
    if not _HAS_PDR or ticker in STOOQ_BLOCKLIST:
        return pd.DataFrame()

    t = STOOQ_MAP.get(ticker, ticker)
    if not t.startswith("^"):
        t = t.lower()

    try:
        import pandas_datareader.data as pdr_local
        df = cast(pd.DataFrame, pdr_local.DataReader(t, "stooq", start=start, end=end))
        df.sort_index(inplace=True)
        return df
    except Exception:
        return pd.DataFrame()

def _weekend_safe_range(period: str | None, start: Any, end: Any) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Compute a concrete [start, end) window.
    - If explicit start/end provided: use them (add +1 day to end to make it exclusive).
    - If period is '1d': use the last trading day's [Fri, Sat) window on weekends.
    - If period like '2d'/'5d': build a window ending at the last trading day.
    """
    if start is not None or end is not None:
        end_ts = pd.Timestamp(end) if end is not None else last_trading_date() + pd.Timedelta(days=1)
        start_ts = pd.Timestamp(start) if start is not None else (end_ts - pd.Timedelta(days=5))
        return start_ts.normalize(), pd.Timestamp(end_ts).normalize()

    # No explicit dates; derive from period
    if isinstance(period, str) and period.endswith("d"):
        days = int(period[:-1])
    else:
        days = 1

    # Anchor to last trading day (Fri on Sun/Sat)
    end_trading = last_trading_date()
    start_ts = (end_trading - pd.Timedelta(days=days)).normalize()
    end_ts = (end_trading + pd.Timedelta(days=1)).normalize()
    return start_ts, end_ts

def download_price_data(ticker: str, **kwargs: Any) -> FetchResult:
    """
    Robust OHLCV fetch with multi-stage fallbacks:

    Order:
      1) Yahoo Finance via yfinance
      2) Stooq via pandas-datareader
      3) Stooq direct CSV
      4) Index proxies (e.g., ^GSPC->SPY, ^RUT->IWM) via Yahoo
    Returns a DataFrame with columns [Open, High, Low, Close, Adj Close, Volume].
    """
    # Pull out range args, compute a weekend-safe window
    period = kwargs.pop("period", None)
    start = kwargs.pop("start", None)
    end = kwargs.pop("end", None)
    kwargs.setdefault("progress", False)
    kwargs.setdefault("threads", False)

    s, e = _weekend_safe_range(period, start, end)

    # ---------- 1) Yahoo (date-bounded) ----------
    df_y = _yahoo_download(ticker, start=s, end=e, **kwargs)
    if isinstance(df_y, pd.DataFrame) and not df_y.empty:
        return FetchResult(_normalize_ohlcv(_to_datetime_index(df_y)), "yahoo")

    # ---------- 2) Stooq via pandas-datareader ----------
    df_s = _stooq_download(ticker, start=s, end=e)
    if isinstance(df_s, pd.DataFrame) and not df_s.empty:
        return FetchResult(_normalize_ohlcv(_to_datetime_index(df_s)), "stooq-pdr")

    # ---------- 3) Stooq direct CSV ----------
    df_csv = _stooq_csv_download(ticker, s, e)
    if isinstance(df_csv, pd.DataFrame) and not df_csv.empty:
        return FetchResult(_normalize_ohlcv(_to_datetime_index(df_csv)), "stooq-csv")

    # ---------- 4) Proxy indices if applicable ----------
    proxy_map = {"^GSPC": "SPY", "^RUT": "IWM"}
    proxy = proxy_map.get(ticker)
    if proxy:
        df_proxy = _yahoo_download(proxy, start=s, end=e, **kwargs)
        if isinstance(df_proxy, pd.DataFrame) and not df_proxy.empty:
            return FetchResult(_normalize_ohlcv(_to_datetime_index(df_proxy)), f"yahoo:{proxy}-proxy")

    # ---------- Nothing worked ----------
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"])
    return FetchResult(empty, "empty")



# ------------------------------
# File path configuration
# ------------------------------

def set_data_dir(data_dir: Path) -> None:
    global DATA_DIR, PORTFOLIO_CSV, TRADE_LOG_CSV
    logger.info("Setting data directory: %s", data_dir)
    DATA_DIR = Path(data_dir)
    logger.debug("Creating data directory if it doesn't exist: %s", DATA_DIR)
    os.makedirs(DATA_DIR, exist_ok=True)

    # If data_dir already contains chatgpt_portfolio_update.csv, use it directly
    # Otherwise assume data_dir is the repo root and look in "Start Your Own" subdirectory
    if (DATA_DIR / "chatgpt_portfolio_update.csv").exists():
        PORTFOLIO_CSV = DATA_DIR / "chatgpt_portfolio_update.csv"
        TRADE_LOG_CSV = DATA_DIR / "chatgpt_trade_log.csv"
    else:
        PORTFOLIO_CSV = DATA_DIR / "Start Your Own" / "chatgpt_portfolio_update.csv"
        TRADE_LOG_CSV = DATA_DIR / "chatgpt_trade_log.csv"

    logger.info("Data directory configured - Portfolio CSV: %s, Trade Log CSV: %s", PORTFOLIO_CSV, TRADE_LOG_CSV)


# ------------------------------
# Excel helper functions
# ------------------------------


def save_portfolio_to_csv_with_formatting(df: pd.DataFrame) -> None:
    """Save portfolio data to CSV file with blank lines after TOTAL rows for better readability."""
    try:
        csv_path = PORTFOLIO_CSV
        logger.info("Writing formatted CSV file: %s", csv_path)

        # Create formatted data with blank lines after TOTAL rows
        formatted_rows = []

        for idx, row in df.iterrows():
            # Add the current row
            formatted_rows.append(row)

            # If this is a TOTAL row, add a blank line after it
            if row['Ticker'] == 'TOTAL':
                # Create a blank row (all NaN/empty values)
                blank_row = pd.Series([None] * len(df.columns), index=df.columns)
                formatted_rows.append(blank_row)

        # Create DataFrame with blank lines
        df_formatted = pd.DataFrame(formatted_rows).reset_index(drop=True)

        # Format Shares column to 4 decimal places to avoid truncation (e.g., 0.4375 -> 0.43)
        if 'Shares' in df_formatted.columns:
            df_formatted['Shares'] = df_formatted['Shares'].apply(
                lambda x: f'{x:.4f}' if pd.notna(x) and isinstance(x, (int, float)) else x
            )

        # Save to CSV
        df_formatted.to_csv(csv_path, index=False)
        logger.info("Successfully wrote formatted CSV file: %s", csv_path)

    except Exception as e:
        logger.error("Error writing CSV file %s: %s", csv_path, e)
        # Don't raise - this is supplementary to Excel saving
        pass


# ------------------------------
# Input helpers
# ------------------------------

def get_stop_loss_percentage() -> float:
    """Get stop loss percentage from user input with validation.

    Returns:
        float: Stop loss percentage (0 uses default from config)
    """
    # Load default from config
    config = load_config()
    default_pct = config.get("settings", {}).get("default_stop_loss_pct", 8)

    try:
        stop_loss_pct = float(input(f"Enter stop loss percentage (e.g., 8 for 8% below buy price, or 0 for default {default_pct}%): "))
        if stop_loss_pct < 0:
            raise ValueError("Stop loss percentage cannot be negative")

        # If user enters 0, use the default from config
        if stop_loss_pct == 0:
            stop_loss_pct = default_pct
            print(f"Using default stop loss: {default_pct}%")

        return stop_loss_pct
    except ValueError:
        raise ValueError("Invalid stop loss percentage")

def calculate_stop_loss_price(buy_price: float, stop_loss_pct: float) -> float:
    """Calculate actual stop loss price from percentage.

    Args:
        buy_price: The buy/execution price
        stop_loss_pct: Stop loss percentage (e.g., 8 for 8%)

    Returns:
        float: Stop loss price (0 if stop_loss_pct is 0)
    """
    if stop_loss_pct <= 0:
        return 0.0
    return round(buy_price * (1 - stop_loss_pct / 100), 2)

def process_buy_order(
    order_type: str,
    ticker: str,
    shares: float,
    order_price: float | None,
    stop_loss_pct: float,
    cash: float,
    portfolio_df: pd.DataFrame
) -> tuple[float, pd.DataFrame] | None:
    """Process a buy order of any type (market, limit, executed).

    Args:
        order_type: 'm' (market), 'l' (limit), or 'e' (executed)
        ticker: Stock ticker symbol
        shares: Number of shares to buy
        order_price: Limit/execution price (None for market orders)
        stop_loss_pct: Stop loss percentage
        cash: Available cash
        portfolio_df: Current portfolio

    Returns:
        Tuple of (updated_cash, updated_portfolio) or None if failed
    """
    today_iso = last_trading_date().date().isoformat()

    # Determine execution price based on order type
    if order_type == "m":
        # Market order - use opening price
        s, e = trading_day_window()
        fetch = download_price_data(ticker, start=s, end=e, auto_adjust=False, progress=False)
        data = fetch.df
        if data.empty:
            print(f"MOO buy for {ticker} failed: no market data available (source={fetch.source}).")
            return None

        o = float(data["Open"].iloc[-1]) if "Open" in data else float(data["Close"].iloc[-1])
        exec_price = round(o, 2)
        source_info = fetch.source
        reason = "MANUAL BUY MOO - Filled"

    elif order_type == "l":
        # Limit order - simulate execution
        s, e = trading_day_window()
        fetch = download_price_data(ticker, start=s, end=e, auto_adjust=False, progress=False)
        data = fetch.df
        if data.empty:
            print(f"Manual buy for {ticker} failed: no market data available (source={fetch.source}).")
            return None

        o = float(data["Open"].iloc[-1]) if "Open" in data else float(data["Close"].iloc[-1])
        h = float(data["High"].iloc[-1])
        l = float(data["Low"].iloc[-1])
        if pd.isna(o):
            o = float(data["Close"].iloc[-1])

        # Simulate limit order execution
        if o <= order_price:
            exec_price = o
        elif l <= order_price:
            exec_price = order_price
        else:
            print(f"Buy limit ${order_price:.2f} for {ticker} not reached today (range {l:.2f}-{h:.2f}). Order not filled.")
            return None

        source_info = fetch.source
        reason = "MANUAL BUY LIMIT - Filled"

    elif order_type == "e":
        # Executed order - use exact price provided
        exec_price = order_price
        source_info = "executed"
        reason = "MANUAL BUY EXECUTED - Already Filled"

    else:
        print(f"Unknown order type: {order_type}")
        return None

    # Calculate stop loss and cost
    stop_loss = calculate_stop_loss_price(exec_price, stop_loss_pct)
    cost_amt = exec_price * shares

    # Check if we have enough cash
    if cost_amt > cash:
        print(f"Buy for {ticker} failed: cost {cost_amt:.2f} exceeds cash {cash:.2f}.")
        return None

    # Show stop loss info
    if stop_loss_pct > 0:
        print(f"Stop loss set at ${stop_loss:.2f} ({stop_loss_pct}% below buy price of ${exec_price:.2f})")

    # Confirmation
    order_desc = {
        "m": "MOO",
        "l": "LIMIT",
        "e": "EXECUTED"
    }[order_type]

    check = input(
        f"You are placing a BUY {order_desc} for {shares} {ticker} at ${exec_price:.2f}.\n"
        f"If this is a mistake, type '1' or, just hit Enter: "
    )
    if check == "1":
        print("Returning...")
        return cash, portfolio_df

    # Log the trade
    log = {
        "Date": today_iso,
        "Ticker": ticker,
        "Shares Bought": round(shares, 4),
        "Buy Price": round(exec_price, 2),
        "Cost Basis": round(cost_amt, 2),
        "PnL": 0.0,
        "Reason": reason,
    }

    if os.path.exists(TRADE_LOG_CSV):
        logger.info("Reading CSV file: %s", TRADE_LOG_CSV)
        df_log = pd.read_csv(TRADE_LOG_CSV)
        logger.info("Successfully read CSV file: %s", TRADE_LOG_CSV)
        if df_log.empty:
            df_log = pd.DataFrame([log])
        else:
            df_log = pd.concat([df_log, pd.DataFrame([log])], ignore_index=True)
    else:
        df_log = pd.DataFrame([log])
    logger.info("Writing CSV file: %s", TRADE_LOG_CSV)
    df_log.to_csv(TRADE_LOG_CSV, index=False)
    logger.info("Successfully wrote CSV file: %s", TRADE_LOG_CSV)

    # Update portfolio
    if not isinstance(portfolio_df, pd.DataFrame) or portfolio_df.empty:
        portfolio_df = pd.DataFrame(
            columns=["ticker", "shares", "stop_loss", "buy_price", "cost_basis"]
        )

    rows = portfolio_df.loc[portfolio_df["ticker"].astype(str).str.upper() == ticker.upper()]
    if rows.empty:
        # New position
        new_trade = {
            "ticker": ticker,
            "shares": round(float(shares), 4),
            "stop_loss": round(float(stop_loss), 2),
            "buy_price": round(float(exec_price), 2),
            "cost_basis": round(float(cost_amt), 2),
        }
        if portfolio_df.empty:
            portfolio_df = pd.DataFrame([new_trade])
        else:
            portfolio_df = pd.concat([portfolio_df, pd.DataFrame([new_trade])], ignore_index=True)
    else:
        # Add to existing position
        idx = rows.index[0]
        cur_shares = float(portfolio_df.at[idx, "shares"])
        cur_cost = float(portfolio_df.at[idx, "cost_basis"])
        new_shares = cur_shares + float(shares)
        new_cost = cur_cost + float(cost_amt)
        avg_price = new_cost / new_shares if new_shares else 0.0
        portfolio_df.at[idx, "shares"] = round(new_shares, 4)
        portfolio_df.at[idx, "cost_basis"] = round(new_cost, 2)
        portfolio_df.at[idx, "buy_price"] = round(avg_price, 2)
        portfolio_df.at[idx, "stop_loss"] = round(float(stop_loss), 2)

    # Update cash
    cash -= cost_amt

    # Success message
    if order_type == "m":
        print(f"Manual BUY MOO for {ticker} filled at ${exec_price:.2f} ({source_info}).")
    elif order_type == "l":
        print(f"Manual BUY LIMIT for {ticker} filled at ${exec_price:.2f} ({source_info}).")
    else:  # executed
        print(f"Executed BUY for {ticker} logged at ${exec_price:.2f}.")

    return cash, portfolio_df

# ------------------------------
# Portfolio operations
# ------------------------------

def _ensure_df(portfolio: pd.DataFrame | dict[str, list[object]] | list[dict[str, object]]) -> pd.DataFrame:
    if isinstance(portfolio, pd.DataFrame):
        return portfolio.copy()
    if isinstance(portfolio, (dict, list)):
        df = pd.DataFrame(portfolio)
        # Ensure proper columns exist even for empty DataFrames
        if df.empty:
            logger.debug("Creating empty portfolio DataFrame with proper column structure")
            df = pd.DataFrame(columns=["ticker", "shares", "stop_loss", "buy_price", "cost_basis"])
        return df
    raise TypeError("portfolio must be a DataFrame, dict, or list[dict]")

def process_portfolio(
    portfolio: pd.DataFrame | dict[str, list[object]] | list[dict[str, object]],
    cash: float,
    interactive: bool = True,
    override_cash_value: float | None = None,
) -> tuple[pd.DataFrame, float]:
    today_iso = last_trading_date().date().isoformat()
    portfolio_df = _ensure_df(portfolio)

    # Check if today's data already exists with actual prices (not NO DATA)
    # If so, skip processing to make script idempotent UNLESS in interactive mode
    if PORTFOLIO_CSV.exists():
        existing = CSVHandler.read_portfolio_csv()
        if not existing.empty:
            today_data = existing[existing["Date"] == today_iso]
            if not today_data.empty:
                # Check if any row has actual price data (not NO DATA)
                has_real_data = today_data[
                    (pd.notna(today_data["Current Price"])) &
                    (today_data["Current Price"] != 0) &
                    (today_data["Action"] != "NO DATA")
                ].shape[0] > 0

                if has_real_data:
                    # In interactive mode, allow re-running to add new trades
                    if interactive:
                        if override_cash_value is not None:
                            logger.info(f"Today's data ({today_iso}) already exists, but --set-cash override is active.")
                            print(f"Note: Portfolio data for {today_iso} already exists. Will update with new cash value.")
                        else:
                            logger.info(f"Today's data ({today_iso}) already exists, but running in interactive mode to allow new trades.")
                            print(f"Note: Portfolio data for {today_iso} already exists. You can add new trades if needed.")
                        # Continue to interactive trade entry, but we'll check later if any trades were actually added
                    else:
                        if override_cash_value is not None:
                            logger.info(f"Today's data ({today_iso}) already exists, but --set-cash override is active.")
                            print(f"Note: Portfolio data for {today_iso} already exists. Will update with new cash value.")
                            # Continue to update with override cash
                        else:
                            logger.info(f"Today's data ({today_iso}) already exists with prices. Skipping portfolio processing.")
                            print(f"Note: Portfolio data for {today_iso} already exists. Skipping re-processing.")
                            return portfolio_df, cash

    results: list[dict[str, object]] = []
    total_value = 0.0
    total_pnl = 0.0

    # Track if any manual trades were added in interactive mode
    manual_trades_added = False

    # ------- Interactive trade entry (supports MOO) -------
    if interactive:
        while True:
            print(portfolio_df)
            action = input(
                f""" You have {cash} in cash.
Would you like to log a manual trade? Enter 'b' for buy, 's' for sell, or press Enter to continue: """
            ).strip().lower()

            if action == "b":
                ticker = input("Enter ticker symbol: ").strip().upper()

                try:
                    shares = float(input("Enter number of shares: "))
                    if shares <= 0:
                        raise ValueError
                except ValueError:
                    print("Invalid share amount. Buy cancelled.")
                    continue

                # Get execution price and stop loss
                try:
                    order_price = float(input("Enter the execution price: "))
                    if order_price <= 0:
                        raise ValueError("Execution price must be positive")
                    stop_loss_pct = get_stop_loss_percentage()

                except ValueError as e:
                    print(f"Invalid input. Buy cancelled.")
                    continue

                # Process the buy order as executed (already filled)
                result = process_buy_order(
                    order_type="e",
                    ticker=ticker,
                    shares=shares,
                    order_price=order_price,
                    stop_loss_pct=stop_loss_pct,
                    cash=cash,
                    portfolio_df=portfolio_df
                )

                if result is not None:
                    cash, portfolio_df = result
                    manual_trades_added = True
                continue

            if action == "s":
                # Show current holdings for selection
                if portfolio_df.empty:
                    print("No positions to sell.")
                    continue

                print("\n📊 Current Holdings:")
                print("=" * 50)
                holdings = []
                for idx, stock in portfolio_df.iterrows():
                    ticker_name = str(stock["ticker"]).upper()
                    shares_held = float(stock["shares"]) if not pd.isna(stock["shares"]) else 0.0
                    buy_price = float(stock["buy_price"]) if not pd.isna(stock["buy_price"]) else 0.0

                    if shares_held > 0:  # Only show positions with shares
                        holdings.append({
                            'ticker': ticker_name,
                            'shares': shares_held,
                            'buy_price': buy_price,
                            'index': len(holdings)
                        })
                        print(f"{len(holdings)}. {ticker_name:<6} | {shares_held:>8.4f} shares @ ${buy_price:.2f}")

                if not holdings:
                    print("No positions available to sell.")
                    continue

                print("=" * 50)

                try:
                    # Get user selection
                    selection = input(f"\nSelect position to sell (1-{len(holdings)}) or 'c' to cancel: ").strip().lower()

                    if selection == 'c':
                        print("Sell cancelled.")
                        continue

                    selection_num = int(selection)
                    if selection_num < 1 or selection_num > len(holdings):
                        print("Invalid selection. Sell cancelled.")
                        continue

                    # Get selected ticker info
                    selected = holdings[selection_num - 1]
                    ticker = selected['ticker']
                    max_shares = selected['shares']

                    print(f"\n📉 Selling {ticker} (Max: {max_shares:.4f} shares)")

                    # Get sell details with default to max shares
                    shares_input = input(f"Enter number of shares to sell (max {max_shares:.4f}, press Enter for all): ").strip()

                    if shares_input == "":
                        # Default to selling all shares
                        shares = max_shares
                        print(f"Defaulting to sell all {shares:.4f} shares")
                    else:
                        try:
                            shares = float(shares_input)
                        except ValueError:
                            print("Invalid number format. Sell cancelled.")
                            continue

                    if shares <= 0 or shares > max_shares:
                        print(f"Invalid share amount. Must be between 0 and {max_shares:.4f}. Sell cancelled.")
                        continue

                    sell_price = float(input("Enter sell LIMIT price: "))
                    if sell_price <= 0:
                        print("Invalid sell price. Sell cancelled.")
                        continue

                except (ValueError, KeyboardInterrupt):
                    print("Invalid input. Manual sell cancelled.")
                    continue

                cash, portfolio_df = log_manual_sell(
                    sell_price, shares, ticker, cash, portfolio_df
                )
                manual_trades_added = True
                continue

            break  # proceed to pricing

    # ------- SIMPLIFIED: Early exit if snapshot already complete -------
    # Since we now load from trade log (single source of truth), we only need to check
    # if today's snapshot exists with prices. No complex reconciliation needed!
    if interactive and not manual_trades_added and override_cash_value is None:
        if PORTFOLIO_CSV.exists():
            existing = CSVHandler.read_portfolio_csv()
            if not existing.empty:
                today_data = existing[existing["Date"] == today_iso]
                if not today_data.empty:
                    # Check if snapshot has actual price data (meaning it's complete)
                    has_real_data = today_data[
                        (pd.notna(today_data["Current Price"])) &
                        (today_data["Current Price"] != 0) &
                        (today_data["Action"] != "NO DATA")
                    ].shape[0] > 0

                    if has_real_data:
                        logger.info(f"Today's snapshot already exists with prices and no new trades. Skipping rewrite.")
                        print("No new trades added. Exiting without updating CSV.")
                        return portfolio_df, cash

    # Get the last portfolio date to determine which trades to process for display
    last_portfolio_date = None
    if PORTFOLIO_CSV.exists():
        existing = CSVHandler.read_portfolio_csv()
        if not existing.empty:
            non_total = existing[existing["Ticker"] != "TOTAL"].copy()
            non_total["Date"] = pd.to_datetime(non_total["Date"], format="mixed", errors="coerce")
            last_portfolio_date = non_total["Date"].max()
            if pd.notna(last_portfolio_date):
                last_portfolio_date = last_portfolio_date.date().isoformat()

    today_buys, today_sells, today_buy_costs = TradeDetector.get_todays_trades(today_iso, last_portfolio_date)
    trade_log = CSVHandler.read_trade_log()  # Need this for sold position details later

    # ------- Daily pricing + stop-loss execution -------
    s, e = trading_day_window()
    for _, stock in portfolio_df.iterrows():
        ticker = str(stock["ticker"]).upper()
        shares = float(stock["shares"]) if not pd.isna(stock["shares"]) else 0.0
        cost = float(stock["buy_price"]) if not pd.isna(stock["buy_price"]) else 0.0
        cost_basis = float(stock["cost_basis"]) if not pd.isna(stock["cost_basis"]) else cost * shares
        stop = float(stock["stop_loss"]) if not pd.isna(stock["stop_loss"]) else 0.0

        fetch = download_price_data(ticker, start=s, end=e, auto_adjust=False, progress=False)
        data = fetch.df

        if data.empty:
            print(f"No data for {ticker} (source={fetch.source}).")
            row = {
                "Date": today_iso, "Ticker": ticker, "Shares": round(shares, 4),
                "Buy Price": round(cost, 2), "Cost Basis": round(cost_basis, 2), "Stop Loss": round(stop, 2),
                "Current Price": "", "Total Value": "", "PnL": "", "PnL %": "",
                "Action": "NO DATA", "Cash Balance": "", "Total Equity": "", "Notes": "",
            }
            results.append(row)
            continue

        o = float(data["Open"].iloc[-1]) if "Open" in data else np.nan
        h = float(data["High"].iloc[-1])
        l = float(data["Low"].iloc[-1])
        c = float(data["Close"].iloc[-1])
        if np.isnan(o):
            o = c

        # Check if stop loss level was breached (for alerting purposes only)
        price = round(c, 2)
        value = round(price * shares, 2)
        pnl = round((price - cost) * shares, 2)

        # Alert if stop loss was triggered but don't auto-execute
        if stop and l <= stop:
            print(f"⚠️  ALERT: {ticker} stop loss triggered! Low: ${l:.2f} vs Stop Loss: ${stop:.2f}")
            print(f"   Current price: ${price:.2f} - Consider manual sale if desired.")
            action_note = "STOP LOSS ALERT"
        else:
            action_note = ""

        # Determine action and cash flow
        ticker_upper = ticker.upper()

        # Determine action (never auto-sell due to stop loss)
        # Check both buys and sells - if both happened, show BUY (current position from rebuy)
        if ticker_upper in today_buys:
            action = "BUY"
            # Show negative cash flow for purchases
            cash_flow = f"-{today_buy_costs.get(ticker_upper, 0):.2f}" if today_buy_costs.get(ticker_upper, 0) > 0 else ""
            # Count in portfolio total (position held)
            total_value += value
            total_pnl += pnl
            position_value = value

            # If there was ALSO a sell today, add a SELL row first (before the BUY/HOLD row)
            if ticker_upper in today_sells:
                # Get sell details from trade log
                if last_portfolio_date is None or last_portfolio_date >= today_iso:
                    date_filter = (trade_log["Date"] == today_iso)
                else:
                    date_filter = ((trade_log["Date"] > last_portfolio_date) & (trade_log["Date"] <= today_iso))

                sell_entries = trade_log[
                    date_filter &
                    (trade_log["Ticker"].astype(str).str.upper() == ticker_upper) &
                    (pd.notna(trade_log["Shares Sold"])) &
                    (trade_log["Shares Sold"] > 0)
                ]

                if not sell_entries.empty:
                    total_shares_sold = sell_entries["Shares Sold"].sum()
                    sell_entry = sell_entries.iloc[0]
                    sell_price = float(sell_entry["Sell Price"]) if pd.notna(sell_entry["Sell Price"]) else 0
                    sell_cost_basis = float(sell_entry["Cost Basis"]) if pd.notna(sell_entry["Cost Basis"]) else 0
                    sell_pnl = float(sell_entry["PnL"]) if pd.notna(sell_entry["PnL"]) else 0
                    sell_pnl_pct = (sell_pnl / sell_cost_basis * 100) if sell_cost_basis > 0 else 0

                    # Get the OLD buy price from the sell entry
                    old_buy_price = sell_cost_basis / total_shares_sold if total_shares_sold > 0 else 0

                    # Create SELL row for the old position
                    sell_row = {
                        "Date": today_iso, "Ticker": ticker, "Shares": round(total_shares_sold, 4),
                        "Buy Price": round(old_buy_price, 2), "Cost Basis": round(sell_cost_basis, 2), "Stop Loss": "",
                        "Current Price": round(sell_price, 2), "Total Value": round(total_shares_sold * sell_price, 2),
                        "PnL": round(sell_pnl, 2), "PnL %": round(sell_pnl_pct, 2),
                        "Action": "SELL - Manual", "Cash Balance": f"+{today_sells.get(ticker_upper, 0):.2f}",
                        "Total Equity": "", "Notes": "Sold and rebought",
                    }
                    results.append(sell_row)

            # Now create the BUY/HOLD row for the current position (after sell if applicable)
            pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
            row = {
                "Date": today_iso, "Ticker": ticker, "Shares": round(shares, 4),
                "Buy Price": round(cost, 2), "Cost Basis": round(cost_basis, 2), "Stop Loss": round(stop, 2),
                "Current Price": price, "Total Value": round(position_value, 2), "PnL": pnl, "PnL %": round(pnl_pct, 2),
                "Action": action, "Cash Balance": cash_flow, "Total Equity": "", "Notes": action_note,
            }
            results.append(row)
        elif ticker_upper in today_sells:
            # This ticker had a sell today - need to create TWO rows:
            # 1. A SELL row showing what was sold
            # 2. A HOLD row showing what remains (current row)

            # First, add the SELL transaction row
            # Get sell details from trade log (search from last portfolio date to today)
            if last_portfolio_date is None or last_portfolio_date >= today_iso:
                # Only search today
                date_filter = (trade_log["Date"] == today_iso)
            else:
                # Search the gap period
                date_filter = ((trade_log["Date"] > last_portfolio_date) & (trade_log["Date"] <= today_iso))

            sell_entries = trade_log[
                date_filter &
                (trade_log["Ticker"].astype(str).str.upper() == ticker_upper) &
                (pd.notna(trade_log["Shares Sold"])) &
                (trade_log["Shares Sold"] > 0)
            ]

            if not sell_entries.empty:
                # Aggregate all sells for this ticker in the gap period
                total_shares_sold = sell_entries["Shares Sold"].sum()
                sell_entry = sell_entries.iloc[0]  # Use first entry for pricing details
                sell_price = float(sell_entry["Sell Price"]) if pd.notna(sell_entry["Sell Price"]) else price
                sell_cost_basis = float(sell_entry["Cost Basis"]) if pd.notna(sell_entry["Cost Basis"]) else 0
                sell_pnl = float(sell_entry["PnL"]) if pd.notna(sell_entry["PnL"]) else 0

                # Calculate PnL % for the sold portion
                sell_pnl_pct = (sell_pnl / sell_cost_basis * 100) if sell_cost_basis > 0 else 0

                # Check if this is a complete sale (all shares sold)
                # Add small tolerance for floating point precision
                # Also check if remaining shares round to 0 (to avoid showing 0.0 shares as HOLD)
                remaining_shares = shares - total_shares_sold
                is_complete_sale = abs(total_shares_sold - shares) < 1e-6 or round(remaining_shares, 4) <= 0

                # Create SELL row (showing what was sold)
                sell_row = {
                    "Date": today_iso, "Ticker": ticker, "Shares": round(total_shares_sold, 4),
                    "Buy Price": round(cost, 2), "Cost Basis": round(sell_cost_basis, 2), "Stop Loss": round(stop, 2),
                    "Current Price": round(sell_price, 2), "Total Value": round(total_shares_sold * sell_price, 2),
                    "PnL": round(sell_pnl, 2), "PnL %": round(sell_pnl_pct, 2),
                    "Action": "SELL - Manual", "Cash Balance": f"+{today_sells.get(ticker_upper, 0):.2f}",
                    "Total Equity": "", "Notes": "Complete sale" if is_complete_sale else "Partial sell",
                }
                results.append(sell_row)

                # Only add HOLD row if shares remain after the sell
                if not is_complete_sale:
                    # Now handle the HOLD row (remaining position)
                    action = "HOLD"
                    cash_flow = ""  # Cash flow already shown in SELL row
                    # Count remaining position value
                    total_value += value
                    total_pnl += pnl
                    position_value = value

                    # Create HOLD row for remaining shares
                    pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
                    row = {
                        "Date": today_iso, "Ticker": ticker, "Shares": round(shares, 4),
                        "Buy Price": round(cost, 2), "Cost Basis": round(cost_basis, 2), "Stop Loss": round(stop, 2),
                        "Current Price": price, "Total Value": round(position_value, 2), "PnL": pnl, "PnL %": round(pnl_pct, 2),
                        "Action": action, "Cash Balance": cash_flow, "Total Equity": "", "Notes": action_note,
                    }
                    results.append(row)
                # else: Complete sale - no HOLD row needed, continue to next ticker
        else:
            action = "HOLD"
            cash_flow = ""
            # Count in portfolio total (position held)
            total_value += value
            total_pnl += pnl
            position_value = value

            pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

            row = {
                "Date": today_iso, "Ticker": ticker, "Shares": round(shares, 4),
                "Buy Price": round(cost, 2), "Cost Basis": round(cost_basis, 2), "Stop Loss": round(stop, 2),
                "Current Price": price, "Total Value": round(position_value, 2), "PnL": pnl, "PnL %": round(pnl_pct, 2),
                "Action": action, "Cash Balance": cash_flow, "Total Equity": "", "Notes": action_note,
            }

            results.append(row)

    # ------- Add completely sold positions to CSV -------
    current_tickers = set(portfolio_df["ticker"].astype(str).str.upper()) if not portfolio_df.empty else set()
    SoldPositionHandler.add_sold_positions_to_results(results, today_iso, today_sells, current_tickers, trade_log, last_portfolio_date)

    total_row = {
        "Date": today_iso, "Ticker": "TOTAL", "Shares": "", "Buy Price": "",
        "Cost Basis": "", "Stop Loss": "", "Current Price": "",
        "Total Value": round(total_value, 2), "PnL": round(total_pnl, 2), "PnL %": "",
        "Action": "", "Cash Balance": round(cash, 2),
        "Total Equity": round(total_value + cash, 2), "Notes": "",
    }
    results.append(total_row)

    df_out = pd.DataFrame(results)

    # Read existing CSV data and remove today's entries to avoid duplicates
    if PORTFOLIO_CSV.exists():
        existing = _read_csv_with_encoding_fallback(PORTFOLIO_CSV)
        # Remove blank lines for processing
        existing = existing.dropna(how='all')
        existing = existing[existing["Date"] != str(today_iso)]

        # Recalculate starting cash from the last TOTAL row after removing today's entries
        # This ensures we use yesterday's cash balance even if script runs twice on same day
        if override_cash_value is None:
            # However, skip this if --set-cash override is active
            existing_total = existing[existing["Ticker"] == "TOTAL"].copy()
            if not existing_total.empty:
                valid_cash = existing_total[pd.notna(existing_total["Cash Balance"])]
                if not valid_cash.empty:
                    # Get the last valid cash balance
                    last_cash = float(valid_cash.iloc[-1]["Cash Balance"])

                    # Recalculate today's cash based on the corrected starting balance
                    cash_correction = last_cash - (cash - sum(today_sells.values()) + sum(today_buy_costs.values()))
                    if cash_correction != 0:
                        logger.info(f"Correcting cash balance: was using {cash:.2f}, should start from {last_cash:.2f}")
                        cash = last_cash
                        # Recalculate cash with today's trades
                        for ticker, proceeds in today_sells.items():
                            cash += proceeds
                        for ticker, cost in today_buy_costs.items():
                            cash -= cost

                        # Update the TOTAL row with corrected cash
                        for row in results:
                            if row["Ticker"] == "TOTAL":
                                row["Cash Balance"] = round(cash, 2)
                                row["Total Equity"] = round(total_value + cash, 2)

                        # Recreate df_out with corrected data
                        df_out = pd.DataFrame(results)

        print("Saving results to CSV...")
        df_out = pd.concat([existing, df_out], ignore_index=True)
    else:
        print("Creating new CSV file...")

    # Save to CSV with blank lines after TOTAL rows for better readability
    save_portfolio_to_csv_with_formatting(df_out)

    # Reload portfolio state from CSV to get the updated positions after all trades
    # This ensures the returned portfolio reflects any complete sales that were processed
    updated_portfolio, updated_cash = load_latest_portfolio_state()

    return updated_portfolio, updated_cash



# ------------------------------
# Trade logging
# ------------------------------

def log_sell(
    ticker: str,
    shares: float,
    price: float,
    cost: float,
    pnl: float,
    portfolio: pd.DataFrame,
) -> pd.DataFrame:
    today = check_weekend()
    log = {
        "Date": today,
        "Ticker": ticker,
        "Shares Sold": shares,
        "Sell Price": price,
        "Cost Basis": cost,
        "PnL": pnl,
        "Reason": "AUTOMATED SELL - STOPLOSS TRIGGERED",
    }
    print(f"{ticker} stop loss was met. Selling all shares.")
    portfolio = portfolio[portfolio["ticker"] != ticker]

    if TRADE_LOG_CSV.exists():
        logger.info("Reading CSV file: %s", TRADE_LOG_CSV)
        df = pd.read_csv(TRADE_LOG_CSV)
        logger.info("Successfully read CSV file: %s", TRADE_LOG_CSV)
        if df.empty:
            df = pd.DataFrame([log])
        else:
            df = pd.concat([df, pd.DataFrame([log])], ignore_index=True)
    else:
        df = pd.DataFrame([log])
    logger.info("Writing CSV file: %s", TRADE_LOG_CSV)
    df.to_csv(TRADE_LOG_CSV, index=False)
    logger.info("Successfully wrote CSV file: %s", TRADE_LOG_CSV)
    return portfolio

def log_manual_buy(
    buy_price: float,
    shares: float,
    ticker: str,
    stoploss: float,
    cash: float,
    chatgpt_portfolio: pd.DataFrame,
    interactive: bool = True,
) -> tuple[float, pd.DataFrame]:
    """Delegate to process_buy_order() for a limit buy simulation."""
    stop_loss_pct = (1 - stoploss / buy_price) * 100 if buy_price > 0 and stoploss > 0 else 0.0
    result = process_buy_order(
        order_type="l",
        ticker=ticker,
        shares=shares,
        order_price=buy_price,
        stop_loss_pct=stop_loss_pct,
        cash=cash,
        portfolio_df=chatgpt_portfolio,
    )
    if result is None:
        return cash, chatgpt_portfolio
    return result

def log_executed_buy(
    exec_price: float,
    shares: float,
    ticker: str,
    stoploss: float,
    cash: float,
    chatgpt_portfolio: pd.DataFrame,
    interactive: bool = True,
) -> tuple[float, pd.DataFrame]:
    """Log a buy transaction that was already executed at a specific price.
    Delegates to process_buy_order() — no market data simulation.
    """
    stop_loss_pct = (1 - stoploss / exec_price) * 100 if exec_price > 0 and stoploss > 0 else 0.0
    result = process_buy_order(
        order_type="e",
        ticker=ticker,
        shares=shares,
        order_price=exec_price,
        stop_loss_pct=stop_loss_pct,
        cash=cash,
        portfolio_df=chatgpt_portfolio,
    )
    if result is None:
        return cash, chatgpt_portfolio
    return result

def log_manual_sell(
    sell_price: float,
    shares_sold: float,
    ticker: str,
    cash: float,
    chatgpt_portfolio: pd.DataFrame,
    reason: str | None = None,
    interactive: bool = True,
) -> tuple[float, pd.DataFrame]:
    today = check_weekend()
    if interactive:
        reason = input(
            f"""You are placing a SELL LIMIT for {shares_sold} {ticker} at ${sell_price:.2f}.
If this is a mistake, enter 1, or hit Enter."""
        )
    if reason == "1":
        print("Returning...")
        return cash, chatgpt_portfolio
    elif reason is None:
        reason = ""

    if ticker not in chatgpt_portfolio["ticker"].values:
        print(f"Manual sell for {ticker} failed: ticker not in portfolio.")
        return cash, chatgpt_portfolio

    ticker_row = chatgpt_portfolio[chatgpt_portfolio["ticker"] == ticker]
    total_shares = float(ticker_row["shares"].item())
    # Add small tolerance for floating point precision issues
    if shares_sold > (total_shares + 1e-6):
        print(f"Manual sell for {ticker} failed: trying to sell {shares_sold} shares but only own {total_shares}.")
        return cash, chatgpt_portfolio

    # For manual sells, just execute at the specified price
    # (user has already confirmed they want to sell)
    exec_price = sell_price

    # Fetch data for logging purposes only
    s, e = trading_day_window()
    fetch = download_price_data(ticker, start=s, end=e, auto_adjust=False, progress=False)
    if fetch.df.empty:
        fetch.source = "manual"

    buy_price = float(ticker_row["buy_price"].item())
    cost_basis = buy_price * shares_sold
    pnl = exec_price * shares_sold - cost_basis

    log = {
        "Date": today, "Ticker": ticker,
        "Shares Bought": "", "Buy Price": "",
        "Cost Basis": cost_basis, "PnL": pnl,
        "Reason": f"MANUAL SELL LIMIT - {reason}", "Shares Sold": shares_sold,
        "Sell Price": exec_price,
    }
    if os.path.exists(TRADE_LOG_CSV):
        logger.info("Reading CSV file: %s", TRADE_LOG_CSV)
        df = pd.read_csv(TRADE_LOG_CSV)
        logger.info("Successfully read CSV file: %s", TRADE_LOG_CSV)
        if df.empty:
            df = pd.DataFrame([log])
        else:
            df = pd.concat([df, pd.DataFrame([log])], ignore_index=True)
    else:
        df = pd.DataFrame([log])
    logger.info("Writing CSV file: %s", TRADE_LOG_CSV)
    df.to_csv(TRADE_LOG_CSV, index=False)
    logger.info("Successfully wrote CSV file: %s", TRADE_LOG_CSV)


    if total_shares == shares_sold:
        chatgpt_portfolio = chatgpt_portfolio[chatgpt_portfolio["ticker"] != ticker]
    else:
        row_index = ticker_row.index[0]
        chatgpt_portfolio.at[row_index, "shares"] = total_shares - shares_sold
        chatgpt_portfolio.at[row_index, "cost_basis"] = (
            chatgpt_portfolio.at[row_index, "shares"] * chatgpt_portfolio.at[row_index, "buy_price"]
        )

    cash += shares_sold * exec_price
    print(f"Manual SELL LIMIT for {ticker} filled at ${exec_price:.2f} ({fetch.source}).")
    return cash, chatgpt_portfolio


# ------------------------------
# Reporting / Metrics
# ------------------------------
def _print_market_data_section(portfolio_dict: list[dict[str, Any]]) -> None:
    """Print fundamentals + news sentiment for current holdings.

    Uses Alpha Vantage's OVERVIEW/NEWS_SENTIMENT endpoints only (their own
    server-side NLP) -- no AI/LLM call is made. Silently skipped if
    ALPHA_VANTAGE_API_KEY isn't set in the environment/.env file.
    """
    tickers = sorted({
        str(s["ticker"]).upper() for s in portfolio_dict
        if float(s.get("shares", 0) or 0) > 0
    })
    if not tickers:
        return
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        return
    est_seconds = len(tickers) * 2 * 12.5
    print(f"\nFetching market data for {len(tickers)} ticker(s) from Alpha Vantage (~{est_seconds:.0f}s)...")
    block = build_market_enrichment(tickers, api_key)
    if block:
        print("\n" + block)


def set_prompt(weekly=False):
    if (weekly==True):
        print_weekly_instructions()
    else:
        print_daily_instructions()

WEEKLY_PROMPT_FILE = Path("Start Your Own") / "weekly prompt.md"

_FALLBACK_WEEKLY_PROMPT = """
Use equity_portfolio_strategy.md and weekly_deep_research.md as governing instructions.
The legacy momentum/rotation mandate is retired. Do not place trades without authorization.
If the weekly prompt file is unavailable, report the data and request the current research policy.
""".strip()


def print_weekly_instructions() -> None:
    """Print the LLM instructions section, read from 'Start Your Own/weekly prompt.md'."""
    print("\n[ Your Instructions ]")
    path = SCRIPT_DIR / WEEKLY_PROMPT_FILE
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"\n(Could not load weekly prompt from {path}: {exc})")
        prompt = _FALLBACK_WEEKLY_PROMPT
    print("\n" + prompt)
    print("\n    *Paste everything above into ChatGPT*\n")

DAILY_PROMPT_FILE = Path("Start Your Own") / "daily prompt.md"


def print_daily_instructions() -> None:
    """Print the LLM instructions section, read from 'Start Your Own/daily prompt.md'."""
    print("\n[ Your Instructions ]")
    path = SCRIPT_DIR / DAILY_PROMPT_FILE
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"\n(Could not load daily prompt from {path}: {exc})")
        return
    print("\n" + prompt)
    print("\n    *Paste everything above into ChatGPT*\n")


def print_weekly_transactions_table(weeks: int = 4, as_of: pd.Timestamp | None = None) -> None:
    """Print a table of all buy/sell transactions from the last `weeks` weeks, read from the trade log CSV.

    `as_of` lets callers reuse an already-computed reference date (e.g. daily_results'
    `end_d`) instead of calling last_trading_date() again, which would log a duplicate
    "before/after market hours" message.
    """
    print(f"\n[ Last {weeks} Weeks Transactions ]")
    if not TRADE_LOG_CSV.exists():
        print("No trade log found.")
        return
    try:
        trade_log = pd.read_csv(TRADE_LOG_CSV)
    except Exception as e:
        print(f"Could not read trade log: {e}")
        return

    trade_log["Date"] = pd.to_datetime(trade_log["Date"], errors="coerce")
    reference_date = as_of if as_of is not None else last_trading_date()
    if reference_date.tzinfo is not None:
        reference_date = reference_date.tz_localize(None)  # trade log dates are naive
    cutoff = reference_date - pd.Timedelta(weeks=weeks)
    recent = trade_log[trade_log["Date"] >= cutoff].sort_values("Date")

    if recent.empty:
        print(f"No transactions in the last {weeks} weeks.")
        return

    header = ["Date", "Ticker", "Action", "Shares", "Price", "Amount", "PnL"]
    colw = [10, 8, 6, 10, 10, 12, 10]
    print(" ".join(f"{h:<{w}}" for h, w in zip(header, colw)))
    print("-" * (sum(colw) + len(colw) - 1))

    for _, trade in recent.iterrows():
        reason = str(trade.get("Reason", ""))
        date_str = trade["Date"].strftime("%Y-%m-%d") if pd.notna(trade["Date"]) else "—"
        ticker = str(trade.get("Ticker", ""))

        if "BUY" in reason:
            action = "BUY"
            shares = trade["Shares Bought"] if pd.notna(trade.get("Shares Bought")) else 0.0
            price = trade["Buy Price"] if pd.notna(trade.get("Buy Price")) else 0.0
            amount = trade["Cost Basis"] if pd.notna(trade.get("Cost Basis")) else 0.0
            pnl_str = "—"
        elif "SELL" in reason:
            action = "SELL"
            shares = trade["Shares Sold"] if pd.notna(trade.get("Shares Sold")) else 0.0
            price = trade["Sell Price"] if pd.notna(trade.get("Sell Price")) else 0.0
            amount = shares * price
            pnl = trade["PnL"] if pd.notna(trade.get("PnL")) else 0.0
            pnl_str = f"{'+' if pnl >= 0 else ''}{pnl:.2f}"
        else:
            continue

        row = [date_str, ticker, action, f"{shares:.4f}", f"${price:.2f}", f"${amount:.2f}", pnl_str]
        print(" ".join(f"{str(c):<{w}}" for c, w in zip(row, colw)))


_C_GREEN = "\033[92m"
_C_YELLOW = "\033[93m"
_C_RESET = "\033[0m"


def _detect_deposits(
    cash_series: pd.Series,
    trade_log_path: Path,
    threshold: float = 50.0,
) -> dict[pd.Timestamp, float]:
    """Return a mapping of date → deposit amount for cash injections detected in the portfolio.

    For each date, compares the actual cash change to what's explainable by trades
    (sells add cash, buys subtract cash). Any unexplained increase above `threshold`
    is treated as a deposit.
    """
    deposits: dict[pd.Timestamp, float] = {}
    if not trade_log_path.exists() or cash_series.empty:
        return deposits

    try:
        tlog = pd.read_csv(trade_log_path)
    except Exception:
        return deposits

    tlog["Date"] = pd.to_datetime(tlog["Date"], errors="coerce")
    tlog = tlog.dropna(subset=["Date"])

    net_trade_cash: dict[pd.Timestamp, float] = {}
    for _, row in tlog.iterrows():
        d = row["Date"].normalize()
        reason = str(row.get("Reason", ""))
        if "SELL" in reason.upper():
            shares = float(row["Shares Sold"]) if pd.notna(row.get("Shares Sold")) else 0.0
            price = float(row["Sell Price"]) if pd.notna(row.get("Sell Price")) else 0.0
            net_trade_cash[d] = net_trade_cash.get(d, 0.0) + shares * price
        elif "BUY" in reason.upper():
            cost = float(row["Cost Basis"]) if pd.notna(row.get("Cost Basis")) else 0.0
            net_trade_cash[d] = net_trade_cash.get(d, 0.0) - cost

    dates = cash_series.index.tolist()
    for i in range(1, len(dates)):
        d = dates[i]
        d_norm = pd.Timestamp(d).normalize()
        actual_diff = float(cash_series.iloc[i]) - float(cash_series.iloc[i - 1])
        trade_impact = net_trade_cash.get(d_norm, 0.0)
        unexplained = actual_diff - trade_impact
        if unexplained > threshold:
            deposits[d_norm] = unexplained

    return deposits


def _twr_equity(equity_series: pd.Series, deposits: dict[pd.Timestamp, float]) -> pd.Series:
    """Return a Time-Weighted Return equity series that removes the effect of cash deposits.

    Deposits are subtracted from the equity at the end of each day they occurred,
    so the chain of returns reflects only investment performance, not capital injections.
    Starting value is normalized to match the first equity value.
    """
    if equity_series.empty:
        return equity_series.copy()

    values = equity_series.values.astype(float)
    dates = equity_series.index.tolist()
    adjusted = np.empty(len(values))
    adjusted[0] = values[0]

    for i in range(1, len(values)):
        d_norm = pd.Timestamp(dates[i]).normalize()
        deposit = deposits.get(d_norm, 0.0)
        organic_end = values[i] - deposit
        daily_return = (organic_end / values[i - 1]) - 1.0 if values[i - 1] != 0 else 0.0
        adjusted[i] = adjusted[i - 1] * (1.0 + daily_return)

    return pd.Series(adjusted, index=equity_series.index)


def _slope_char(prev_r: int | None, cur_r: int) -> str:
    if prev_r is None or cur_r == prev_r:
        return "-"
    return "/" if cur_r < prev_r else "\\"


def _text_performance_chart(
    equity_series: pd.Series,
    spx_df: pd.DataFrame,
    chart_width: int = 55,
    height: int = 16,
) -> list[str]:
    """Return lines for a text-based portfolio vs S&P 500 chart (last 6 months, indexed to 100)."""
    end = equity_series.index.max()
    start = end - pd.DateOffset(months=6)

    port = equity_series[equity_series.index >= start].copy()
    if len(port) < 2:
        return []

    port_norm = (port / port.iloc[0]) * 100.0

    spx_norm: pd.Series | None = None
    if spx_df is not None and not spx_df.empty and "Close" in spx_df.columns:
        spx_close = spx_df["Close"].astype(float)
        spx_filtered = spx_close[spx_close.index >= start]
        if not spx_filtered.empty:
            combined_idx = port_norm.index.union(spx_filtered.index)
            spx_aligned = spx_filtered.reindex(combined_idx).ffill().reindex(port_norm.index)
            first_val = float(spx_aligned.iloc[0]) if pd.notna(spx_aligned.iloc[0]) else 0.0
            if first_val != 0.0:
                spx_norm = (spx_aligned / first_val) * 100.0

    all_vals: list[float] = list(port_norm.values)
    if spx_norm is not None:
        all_vals += [float(v) for v in spx_norm.values if pd.notna(v)]
    y_min = min(all_vals)
    y_max = max(all_vals)
    pad = max((y_max - y_min) * 0.08, 1.0)
    y_min -= pad
    y_max += pad
    y_range = y_max - y_min

    dates = port_norm.index.tolist()
    n = len(dates)

    def to_col(i: int) -> int:
        return int(round(i / max(n - 1, 1) * (chart_width - 1)))

    def to_row(val: float) -> int:
        return height - 1 - int(round((val - y_min) / y_range * (height - 1)))

    grid = [[" "] * chart_width for _ in range(height)]
    occupied = [[False] * chart_width for _ in range(height)]

    prev_r: int | None = None
    for i, val in enumerate(port_norm.values):
        c = to_col(i)
        r = max(0, min(height - 1, to_row(float(val))))
        ch = _slope_char(prev_r, r)
        grid[r][c] = f"{_C_GREEN}{ch}{_C_RESET}"
        occupied[r][c] = True
        prev_r = r

    if spx_norm is not None:
        for i, val in enumerate(spx_norm.values):
            if pd.isna(val):
                continue
            c = to_col(i)
            r = max(0, min(height - 1, to_row(float(val))))
            if not occupied[r][c]:
                grid[r][c] = f"{_C_YELLOW}.{_C_RESET}"

    y_label_w = 7
    lines: list[str] = []
    for ri in range(height):
        val_at = y_max - ri / max(height - 1, 1) * y_range
        label = f"{val_at:.0f}%"
        lines.append(f"{label:>{y_label_w}} │{''.join(grid[ri])}")

    lines.append(" " * y_label_w + " └" + "─" * chart_width)

    x_chars = [" "] * chart_width
    prev_month: tuple[int, int] | None = None
    for i, d in enumerate(dates):
        mk = (d.year, d.month)
        if mk != prev_month:
            c = to_col(i)
            label = d.strftime("%b")
            for j, ch in enumerate(label):
                if c + j < chart_width:
                    x_chars[c + j] = ch
            prev_month = mk
    lines.append(" " * (y_label_w + 2) + "".join(x_chars))

    port_ret = float(port_norm.iloc[-1]) - 100.0
    psign = "+" if port_ret >= 0 else ""
    legend = f"  {_C_GREEN}-/\\ Portfolio ({psign}{port_ret:.1f}%){_C_RESET}"
    if spx_norm is not None:
        spx_ret = float(spx_norm.iloc[-1]) - 100.0
        ssign = "+" if spx_ret >= 0 else ""
        legend += f"   {_C_YELLOW}. S&P 500 ({ssign}{spx_ret:.1f}%){_C_RESET}"
    lines.append("")
    lines.append(legend)

    return lines


def daily_results(
    chatgpt_portfolio: pd.DataFrame | list[dict[str, Any]],
    cash: float,
    show_weekly_prompt: bool = False,
    add_market_data: bool = False,
) -> None:
    """Print daily price updates and performance metrics (incl. CAPM)."""
    # Handle both DataFrame and list[dict] input
    if isinstance(chatgpt_portfolio, pd.DataFrame):
        portfolio_dict: list[dict[Any, Any]] = chatgpt_portfolio.to_dict(orient="records")
    else:
        portfolio_dict = chatgpt_portfolio
    today = check_weekend()

    rows: list[list[str]] = []
    header = ["Ticker", "Close", "% Chg", "Volume"]

    end_d = last_trading_date()                           # Fri on weekends
    start_d = (end_d - pd.Timedelta(days=4)).normalize()  # go back enough to capture 2 sessions even around holidays

    benchmarks = load_benchmarks()  # reads tickers.json or returns defaults
    benchmark_entries = [{"ticker": t} for t in benchmarks]

    for stock in portfolio_dict + benchmark_entries:
        ticker = str(stock["ticker"]).upper()
        try:
            fetch = download_price_data(ticker, start=start_d, end=(end_d + pd.Timedelta(days=1)), progress=False)
            data = fetch.df
            if data.empty or len(data) < 2:
                rows.append([ticker, "—", "—", "—"])
                continue

            price = float(data["Close"].iloc[-1])
            last_price = float(data["Close"].iloc[-2])
            volume = float(data["Volume"].iloc[-1])

            percent_change = ((price - last_price) / last_price) * 100
            rows.append([ticker, f"{price:,.2f}", f"{percent_change:+.2f}%", f"{int(volume):,}"])
        except Exception as e:
            raise Exception(f"Download for {ticker} failed. {e} Try checking internet connection.")

    # Read portfolio history from CSV
    chatgpt_df = load_full_portfolio_history()

    # Use only TOTAL rows, sorted by date
    totals = chatgpt_df[chatgpt_df["Ticker"] == "TOTAL"].copy()
    if totals.empty:
        print("\n" + "=" * 64)
        print(f"Daily Results — {today}")
        print("=" * 64)
        print("\n[ Price & Volume ]")
        colw = [10, 12, 9, 15]
        print(f"{header[0]:<{colw[0]}} {header[1]:>{colw[1]}} {header[2]:>{colw[2]}} {header[3]:>{colw[3]}}")
        print("-" * sum(colw) + "-" * 3)
        for r in rows:
            print(f"{str(r[0]):<{colw[0]}} {str(r[1]):>{colw[1]}} {str(r[2]):>{colw[2]}} {str(r[3]):>{colw[3]}}")
        print("\n[ Portfolio Snapshot ]")
        print(chatgpt_portfolio)
        print(f"Cash balance: ${cash:,.2f}")
        if show_weekly_prompt:
            print_weekly_transactions_table(weeks=4, as_of=end_d)
        return

    totals["Date"] = pd.to_datetime(totals["Date"], format="mixed", errors="coerce")  # tolerate ISO strings
    totals = totals.sort_values("Date")

    # Calculate CURRENT total equity from actual portfolio instead of using stale CSV
    current_portfolio_value = 0.0
    if portfolio_dict:  # Check if portfolio_dict is not empty
        s, e = trading_day_window()
        for stock in portfolio_dict:
            ticker = str(stock["ticker"]).upper()
            shares = float(stock["shares"]) if stock.get("shares") is not None else 0.0
            if shares > 0:
                try:
                    fetch = download_price_data(ticker, start=s, end=e, auto_adjust=False, progress=False)
                    if not fetch.df.empty:
                        current_price = float(fetch.df["Close"].iloc[-1])
                        position_value = shares * current_price
                        current_portfolio_value += position_value
                except Exception:
                    pass  # Skip if we can't get price data

    final_equity = current_portfolio_value + cash  # Use CALCULATED equity, not CSV
    equity_series = totals.set_index("Date")["Total Equity"].astype(float).sort_index()

    # External transfers must be explicitly recorded; cash-balance heuristics are not reliable.
    from portfolio_reporting import load_external_flows, time_weighted_returns
    flow_path = DATA_DIR / "external_cash_flows.csv"
    try:
        verified_flows = load_external_flows(flow_path)
    except (ValueError, OSError) as exc:
        logger.warning("External cash flow file invalid: %s", exc)
        verified_flows = None
    verified_returns = time_weighted_returns(equity_series, verified_flows)
    risk_verified = verified_returns is not None
    risk_equity = equity_series
    if risk_verified:
        risk_equity = pd.concat([
            pd.Series([1.0], index=[equity_series.index[0]]),
            (1.0 + verified_returns).cumprod(),
        ])

    # --- Max Drawdown ---
    running_max = risk_equity.cummax()
    drawdowns = (risk_equity / running_max) - 1.0
    max_drawdown = float(drawdowns.min())  # most negative value
    mdd_date = drawdowns.idxmin()

    # Daily simple returns (portfolio)
    r = verified_returns if risk_verified else equity_series.pct_change().dropna()
    n_days = len(r)
    if n_days < 2:
        print("\n" + "=" * 64)
        print(f"Daily Results — {today}")
        print("=" * 64)
        print("\n[ Price & Volume ]")
        colw = [10, 12, 9, 15]
        print(f"{header[0]:<{colw[0]}} {header[1]:>{colw[1]}} {header[2]:>{colw[2]}} {header[3]:>{colw[3]}}")
        print("-" * sum(colw) + "-" * 3)
        for rrow in rows:
            print(f"{str(rrow[0]):<{colw[0]}} {str(rrow[1]):>{colw[1]}} {str(rrow[2]):>{colw[2]}} {str(rrow[3]):>{colw[3]}}")
        print("\n[ Portfolio Snapshot ]")
        print(chatgpt_portfolio)
        print(f"Cash balance: ${cash:,.2f}")
        print(f"Latest ChatGPT Equity: ${final_equity:,.2f}")
        if hasattr(mdd_date, "date") and not isinstance(mdd_date, (str, int)):
            mdd_date_str = mdd_date.date()
        elif hasattr(mdd_date, "strftime") and not isinstance(mdd_date, (str, int)):
            mdd_date_str = mdd_date.strftime("%Y-%m-%d")
        else:
            mdd_date_str = str(mdd_date)
        print(f"Maximum Drawdown: {max_drawdown:.2%} (on {mdd_date_str})")
        if add_market_data:
            _print_market_data_section(portfolio_dict)
        if show_weekly_prompt:
            print_weekly_transactions_table(weeks=4, as_of=end_d)
        set_prompt(show_weekly_prompt)
        return

    # Risk-free config
    rf_annual = 0.045
    rf_daily = (1 + rf_annual) ** (1 / 252) - 1
    rf_period = (1 + rf_daily) ** n_days - 1

    # Stats
    mean_daily = float(r.mean())
    std_daily = float(r.std(ddof=1))

    # Downside deviation (MAR = rf_daily)
    downside = (r - rf_daily).clip(upper=0)
    downside_std = float((downside.pow(2).mean()) ** 0.5) if not downside.empty else np.nan

    # Total return over the window
    r_numeric = pd.to_numeric(r, errors="coerce")
    r_numeric = r_numeric[~r_numeric.isna()].astype(float)
    # Filter out any non-finite values to ensure only valid floats are used
    r_numeric = r_numeric[np.isfinite(r_numeric)]
    # Only use numeric values for the calculation
    if len(r_numeric) > 0:
        arr = np.asarray(r_numeric.values, dtype=float)
        period_return = float(np.prod(1 + arr) - 1) if arr.size > 0 else float('nan')
    else:
        period_return = float('nan')

    # Sharpe / Sortino
    sharpe_period = (period_return - rf_period) / (std_daily * np.sqrt(n_days)) if std_daily > 0 else np.nan
    sharpe_annual = ((mean_daily - rf_daily) / std_daily) * np.sqrt(252) if std_daily > 0 else np.nan
    sortino_period = (period_return - rf_period) / (downside_std * np.sqrt(n_days)) if downside_std and downside_std > 0 else np.nan
    sortino_annual = ((mean_daily - rf_daily) / downside_std) * np.sqrt(252) if downside_std and downside_std > 0 else np.nan

    # -------- CAPM: Beta & Alpha (vs ^GSPC) --------
    start_date = equity_series.index.min() - pd.Timedelta(days=1)
    end_date = equity_series.index.max() + pd.Timedelta(days=1)

    spx_fetch = download_price_data("^GSPC", start=start_date, end=end_date, progress=False)
    spx = spx_fetch.df

    beta = np.nan
    alpha_annual = np.nan
    r2 = np.nan
    n_obs = 0

    if not spx.empty and len(spx) >= 2:
        spx = spx.reset_index()
        if "Date" not in spx.columns:
            candidate_date_cols = [
                c for c in spx.columns
                if str(c).lower() in {"index", "datetime", "date", "timestamp"}
            ]
            if candidate_date_cols:
                spx = spx.rename(columns={candidate_date_cols[0]: "Date"})
            else:
                datetime_cols = [
                    c for c in spx.columns if pd.api.types.is_datetime64_any_dtype(spx[c])
                ]
                if datetime_cols:
                    spx = spx.rename(columns={datetime_cols[0]: "Date"})
                else:
                    spx["Date"] = pd.to_datetime(spx.index, errors="coerce")

        spx["Date"] = pd.to_datetime(spx["Date"], errors="coerce")
        spx = spx[spx["Date"].notna()].set_index("Date").sort_index()
        mkt_ret = spx["Close"].astype(float).pct_change().dropna()

        # Align portfolio & market returns
        common_idx = r.index.intersection(list(mkt_ret.index))
        if len(common_idx) >= 2:
            rp = (r.reindex(common_idx).astype(float) - rf_daily)   # portfolio excess
            rm = (mkt_ret.reindex(common_idx).astype(float) - rf_daily)  # market excess

            x = np.asarray(rm.values, dtype=float).ravel()
            y = np.asarray(rp.values, dtype=float).ravel()

            n_obs = x.size
            rm_std = float(np.std(x, ddof=1)) if n_obs > 1 else 0.0
            if rm_std > 0:
                beta, alpha_daily = np.polyfit(x, y, 1)
                # Arithmetic annualization avoids absurd compounding of a noisy daily intercept.
                alpha_annual = float(alpha_daily) * 252

                corr = np.corrcoef(x, y)[0, 1]
                r2 = float(corr ** 2)

    # $X normalized S&P 500 over same window (uses remembered starting equity)
    spx_norm_fetch = download_price_data(
        "^GSPC",
        start=equity_series.index.min(),
        end=equity_series.index.max() + pd.Timedelta(days=1),
        progress=False,
    )
    spx_norm = spx_norm_fetch.df
    spx_value = np.nan

    # Use the first Total Equity value from the series as starting equity
    # This eliminates the need to ask every time
    starting_equity = equity_series.iloc[0] if not equity_series.empty else np.nan

    if not spx_norm.empty and not np.isnan(starting_equity):
        initial_price = float(spx_norm["Close"].iloc[0])
        price_now = float(spx_norm["Close"].iloc[-1])
        spx_value = (starting_equity / initial_price) * price_now

    # -------- Pretty Printing --------
    print("\n" + "=" * 64)
    print(f"Daily Results — {today}")
    print("=" * 64)

    # Price & Volume table
    print("\n[ Price & Volume ]")
    colw = [10, 12, 9, 15]
    print(f"{header[0]:<{colw[0]}} {header[1]:>{colw[1]}} {header[2]:>{colw[2]}} {header[3]:>{colw[3]}}")
    print("-" * sum(colw) + "-" * 3)
    for rrow in rows:
        print(f"{str(rrow[0]):<{colw[0]}} {str(rrow[1]):>{colw[1]}} {str(rrow[2]):>{colw[2]}} {str(rrow[3]):>{colw[3]}}")

    # Performance metrics
    def fmt_or_na(x: float | int | None, fmt: str) -> str:
        return (fmt.format(x) if not (x is None or (isinstance(x, float) and np.isnan(x))) else "N/A")

    print("\n[ Risk & Return ]")
    if hasattr(mdd_date, "date") and not isinstance(mdd_date, (str, int)):
        mdd_date_str = mdd_date.date()
    elif hasattr(mdd_date, "strftime") and not isinstance(mdd_date, (str, int)):
        mdd_date_str = mdd_date.strftime("%Y-%m-%d")
    else:
        mdd_date_str = str(mdd_date)
    print(f"{'Max Drawdown:':32} {fmt_or_na(max_drawdown if risk_verified else None, '{:.2%}'):>15}"
          f"   on {mdd_date_str if risk_verified else 'N/A'}")
    print(f"{'Sharpe Ratio (period):':32} {fmt_or_na(sharpe_period if risk_verified else None, '{:.4f}'):>15}")
    print(f"{'Sharpe Ratio (annualized):':32} {fmt_or_na(sharpe_annual if risk_verified else None, '{:.4f}'):>15}")
    print(f"{'Sortino Ratio (period):':32} {fmt_or_na(sortino_period if risk_verified else None, '{:.4f}'):>15}")
    print(f"{'Sortino Ratio (annualized):':32} {fmt_or_na(sortino_annual if risk_verified else None, '{:.4f}'):>15}")

    print("\n[ CAPM vs Benchmarks ]")
    if risk_verified and not np.isnan(beta):
        print(f"{'Beta (daily) vs ^GSPC:':32} {beta:>15.4f}")
        print(f"{'Alpha (annualized) vs ^GSPC:':32} {alpha_annual:>15.2%}")
        print(f"{'R² (fit quality):':32} {r2:>15.3f}   {'Obs:':>6} {n_obs}")
        if n_obs < 60 or (not np.isnan(r2) and r2 < 0.20):
            print("  Note: Short sample and/or low R² — alpha/beta may be unstable.")
    else:
        print("Beta/Alpha: N/A (verified flows or sufficient aligned data required).")

    if not risk_verified:
        print("  Risk and return statistics: N/A until external_cash_flows.csv is supplied.")
        print("  An empty Date,Amount CSV is valid only if you verified there were no transfers.")

    print("\n[ Snapshot ]")
    print(f"{'Latest ChatGPT Equity:':32} ${final_equity:>14,.2f}")
    if not np.isnan(spx_value):
        try:
            print(f"{f'${starting_equity:.0f} in S&P 500 (same window):':32} ${spx_value:>14,.2f}")
        except Exception:
            pass
    print(f"{'Cash Balance:':32} ${cash:>14,.2f}")

    print("\n[ 6-Month Performance ]")
    cash_series = totals.set_index("Date")["Cash Balance"].astype(float).sort_index()
    adj_equity = risk_equity if risk_verified else equity_series
    if not risk_verified:
        print("  Warning: chart uses raw equity, not cash-flow-adjusted performance.")
    for line in _text_performance_chart(adj_equity, spx_norm):
        print(line)

    # Show today's transactions to explain cash changes
    print("\n[ Today's Transactions ]")
    if TRADE_LOG_CSV.exists():
        try:
            trade_log = pd.read_csv(TRADE_LOG_CSV)
            today_trades = trade_log[trade_log["Date"] == today]

            if not today_trades.empty:
                print("Transaction history explaining cash flow:")
                for _, trade in today_trades.iterrows():
                    ticker = trade["Ticker"]
                    reason = trade["Reason"]

                    if "BUY" in reason:
                        shares = trade["Shares Bought"] if pd.notna(trade["Shares Bought"]) else 0
                        buy_price = trade["Buy Price"] if pd.notna(trade["Buy Price"]) else 0
                        cost = trade["Cost Basis"] if pd.notna(trade["Cost Basis"]) else 0
                        print(f"  📤 BUY:  {ticker} | {shares:.4f} shares @ ${buy_price:.2f} = -${cost:.2f}")
                    elif "SELL" in reason:
                        shares = trade["Shares Sold"] if pd.notna(trade["Shares Sold"]) else 0
                        sell_price = trade["Sell Price"] if pd.notna(trade["Sell Price"]) else 0
                        proceeds = shares * sell_price
                        pnl = trade["PnL"] if pd.notna(trade["PnL"]) else 0
                        pnl_sign = "+" if pnl >= 0 else ""
                        print(f"  📥 SELL: {ticker} | {shares:.4f} shares @ ${sell_price:.2f} = +${proceeds:.2f} (PnL: {pnl_sign}${pnl:.2f})")

                # Calculate net cash impact
                total_inflow = 0
                total_outflow = 0
                for _, trade in today_trades.iterrows():
                    if "BUY" in trade["Reason"]:
                        cost = trade["Cost Basis"] if pd.notna(trade["Cost Basis"]) else 0
                        total_outflow += cost
                    elif "SELL" in trade["Reason"]:
                        shares = trade["Shares Sold"] if pd.notna(trade["Shares Sold"]) else 0
                        sell_price = trade["Sell Price"] if pd.notna(trade["Sell Price"]) else 0
                        total_inflow += shares * sell_price

                net_cash_flow = total_inflow - total_outflow
                flow_direction = "+" if net_cash_flow >= 0 else ""
                print(f"  💰 NET:  Cash flow today = {flow_direction}${net_cash_flow:.2f} (${total_inflow:.2f} in, ${total_outflow:.2f} out)")
            else:
                print("No transactions today.")

        except Exception as e:
            print(f"Could not read trade log: {e}")
    else:
        print("No trade log found.")

    print("\n[ Holdings ]")
    # Ensure we print as a DataFrame for proper formatting
    if isinstance(chatgpt_portfolio, list):
        holdings_df = pd.DataFrame(chatgpt_portfolio)
    else:
        holdings_df = chatgpt_portfolio
    print(holdings_df)

    # New section: Performance of each held ticker since purchase
    print("\n[ Performance Since Purchase ]")
    if portfolio_dict and len(portfolio_dict) > 0:
        print("Ticker    Buy Price  Current Price  Total Change    % Change")
        print("-" * 62)

        # Use a wider date range to ensure we get recent price data
        # (today's data may not be available yet)
        e = pd.Timestamp.now()
        s = e - pd.Timedelta(days=5)

        for stock in portfolio_dict:
            ticker = str(stock["ticker"]).upper()
            shares = float(stock["shares"]) if stock.get("shares") is not None else 0.0
            buy_price = float(stock["buy_price"]) if stock.get("buy_price") is not None else 0.0

            # Only show positions with shares (skip sold positions)
            if shares > 0:
                # Get current price
                fetch = download_price_data(ticker, start=s, end=e, auto_adjust=False, progress=False)
                data = fetch.df

                if not data.empty:
                    current_price = float(data["Close"].iloc[-1])
                    total_change = current_price - buy_price
                    pct_change = (total_change / buy_price * 100) if buy_price > 0 else 0

                    # Format the output with proper alignment
                    change_sign = "+" if total_change >= 0 else ""
                    pct_sign = "+" if pct_change >= 0 else ""

                    print(f"{ticker:<8}  ${buy_price:>8.2f}  ${current_price:>12.2f}  {change_sign}${total_change:>9.2f}    {pct_sign}{pct_change:>6.2f}%")
                else:
                    print(f"{ticker:<8}  ${buy_price:>8.2f}  {'N/A':>12}  {'N/A':>10}    {'N/A':>7}")
    else:
        print("No current holdings to display.")

    if add_market_data:
        _print_market_data_section(portfolio_dict)
    if show_weekly_prompt:
        from portfolio_reporting import weekly_research_input
        price_lookup = {}
        for ticker, close, *_ in rows:
            try:
                price_lookup[str(ticker).upper()] = float(str(close).replace(",", ""))
            except (TypeError, ValueError):
                continue
        try:
            audit_log = pd.read_csv(TRADE_LOG_CSV) if TRADE_LOG_CSV.exists() else pd.DataFrame()
            report = weekly_research_input(
                portfolio_dict, price_lookup, cash, final_equity, audit_log, end_d,
                equity_history=equity_series, external_flows=verified_flows,
                benchmark_prices=spx["Close"] if not spx.empty else None,
            )
            print("\n" + report)
        except (ValueError, OSError, KeyError) as exc:
            logger.warning("Could not generate weekly research input: %s", exc)
            print(f"Weekly research data unavailable: {exc}")
        print_weekly_transactions_table(weeks=4, as_of=end_d)
    set_prompt(show_weekly_prompt)


# ------------------------------
# Configuration Management
# ------------------------------

def load_config() -> dict:
    """Load configuration from config.json file."""
    config_path = DATA_DIR / "config.json"

    default_config = {
        "initial_cash": 612.00,
        "last_updated": "2025-10-03",
        "settings": {
            "default_benchmarks": ["IWO", "XBI", "SPY", "IWM"],
            "portfolio_csv": "Start Your Own/chatgpt_portfolio_update.csv",
            "trade_log_csv": "chatgpt_trade_log.csv",
            "default_stop_loss_pct": 8
        }
    }

    try:
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = json.load(f)
                # Merge with defaults to ensure all keys exist
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                return config
        else:
            # Create default config file
            with open(config_path, 'w') as f:
                json.dump(default_config, f, indent=2)
            print(f"Created default configuration file: {config_path}")
            return default_config
    except Exception as e:
        print(f"Error loading config file, using defaults: {e}")
        return default_config

def update_config_cash(new_amount: float) -> None:
    """Update the initial cash amount in the configuration file."""
    config_path = DATA_DIR / "config.json"
    config = load_config()
    config["initial_cash"] = new_amount
    config["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d")

    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        print(f"Updated initial cash amount to ${new_amount:.2f} in config file.")
    except Exception as e:
        print(f"Error updating config file: {e}")

# ------------------------------
# Orchestration
# ------------------------------

class SoldPositionHandler:
    """Handle completely sold positions for CSV recording."""

    @staticmethod
    def add_sold_positions_to_results(results: list[dict], today_iso: str, today_sells: dict,
                                     current_tickers: set, trade_log: pd.DataFrame, last_portfolio_date: str | None = None) -> None:
        """Add completely sold positions to CSV results."""
        for ticker_upper, proceeds in today_sells.items():
            if ticker_upper not in current_tickers:
                # This ticker was completely sold (not in current portfolio)
                # Search the gap period if there is one
                if last_portfolio_date is None or last_portfolio_date >= today_iso:
                    date_filter = (trade_log["Date"] == today_iso)
                else:
                    date_filter = ((trade_log["Date"] > last_portfolio_date) & (trade_log["Date"] <= today_iso))

                sell_entries = trade_log[
                    date_filter &
                    (trade_log["Ticker"].astype(str).str.upper() == ticker_upper) &
                    (pd.notna(trade_log["Shares Sold"])) &
                    (trade_log["Shares Sold"] > 0)
                ]

                if not sell_entries.empty:
                    # Use the first sell entry for this ticker
                    sell_entry = sell_entries.iloc[0]
                    shares_sold = float(sell_entry["Shares Sold"])
                    sell_price = float(sell_entry["Sell Price"]) if pd.notna(sell_entry["Sell Price"]) else 0
                    cost_basis = float(sell_entry["Cost Basis"]) if pd.notna(sell_entry["Cost Basis"]) else 0
                    pnl = float(sell_entry["PnL"]) if pd.notna(sell_entry["PnL"]) else 0

                    # Calculate average buy price from cost basis
                    avg_buy_price = cost_basis / shares_sold if shares_sold > 0 else 0

                    proceeds = shares_sold * sell_price
                    row = {
                        "Date": today_iso, "Ticker": ticker_upper, "Shares": 0,
                        "Buy Price": round(avg_buy_price, 2), "Cost Basis": 0, "Stop Loss": 0,
                        "Current Price": round(sell_price, 2), "Total Value": 0, "PnL": round(pnl, 2), "PnL %": "",
                        "Action": "SELL - Manual", "Cash Balance": f"+{proceeds:.2f}", "Total Equity": "", "Notes": "Complete sale",
                    }
                    results.append(row)

class TradeDetector:
    """Detect today's trades from trade log for portfolio processing."""

    @staticmethod
    def get_todays_trades(today_iso: str, last_portfolio_date: str | None = None) -> tuple[set[str], dict[str, float], dict[str, float]]:
        """Parse trade log and return trades since last portfolio date (or just today if caught up).

        Args:
            today_iso: Today's date in ISO format
            last_portfolio_date: Last date in portfolio CSV (None if portfolio is empty)

        Returns:
            - today_buys: Set of tickers bought since last portfolio date
            - today_sells: Dict of {ticker: total_proceeds} since last portfolio date
            - today_buy_costs: Dict of {ticker: total_cost} since last portfolio date
        """
        today_buys = set()
        today_sells = {}
        today_buy_costs = {}

        if not TRADE_LOG_CSV.exists():
            return today_buys, today_sells, today_buy_costs

        try:
            trade_log = pd.read_csv(TRADE_LOG_CSV)

            # Determine which trades to process
            if last_portfolio_date is None:
                # Portfolio is empty, process all trades up to today
                trades_to_process = trade_log[trade_log["Date"] <= today_iso]
                logger.info(f"Portfolio is empty. Processing all trades up to {today_iso}")
            elif last_portfolio_date < today_iso:
                # There's a gap - process all trades after last portfolio date up to today
                trades_to_process = trade_log[
                    (trade_log["Date"] > last_portfolio_date) &
                    (trade_log["Date"] <= today_iso)
                ]
                logger.info(f"Processing trades from {last_portfolio_date} to {today_iso} (gap detected)")
            else:
                # Portfolio is current or today's data already exists - only process today
                trades_to_process = trade_log[trade_log["Date"] == today_iso]
                logger.info(f"Processing trades for {today_iso} only")

            # Find buys in the date range
            today_buy_entries = trades_to_process[
                (trades_to_process["Reason"].astype(str).str.contains("BUY", case=False, na=False))
            ]
            today_buys = set(today_buy_entries["Ticker"].astype(str).str.upper())

            # Calculate buy costs for cash flow
            for _, buy_entry in today_buy_entries.iterrows():
                ticker = str(buy_entry["Ticker"]).upper()
                cost = float(buy_entry["Cost Basis"]) if pd.notna(buy_entry["Cost Basis"]) else 0
                today_buy_costs[ticker] = today_buy_costs.get(ticker, 0) + cost

            # Find sells in the date range and calculate proceeds
            today_sell_entries = trades_to_process[
                (pd.notna(trades_to_process["Shares Sold"])) &
                (trades_to_process["Shares Sold"] > 0)
            ]

            for _, sell_entry in today_sell_entries.iterrows():
                ticker = str(sell_entry["Ticker"]).upper()
                shares_sold = float(sell_entry["Shares Sold"])
                sell_price = float(sell_entry["Sell Price"]) if pd.notna(sell_entry["Sell Price"]) else 0
                proceeds = shares_sold * sell_price
                today_sells[ticker] = today_sells.get(ticker, 0) + proceeds

        except Exception as e:
            logger.warning("Could not read trade log for trade detection: %s", e)

        return today_buys, today_sells, today_buy_costs

class CSVHandler:
    """Unified CSV operations with robust encoding handling."""

    @staticmethod
    def read_csv(csv_path: Path) -> pd.DataFrame:
        """Robust CSV reader with encoding fallback logic."""
        logger.info("Reading CSV file: %s", csv_path)
        try:
            df = pd.read_csv(csv_path, encoding='utf-8')
        except UnicodeDecodeError:
            logger.warning("UTF-8 encoding failed, trying latin-1 encoding")
            try:
                df = pd.read_csv(csv_path, encoding='latin-1')
            except UnicodeDecodeError:
                logger.warning("latin-1 encoding failed, trying cp1252 encoding")
                df = pd.read_csv(csv_path, encoding='cp1252')
        logger.info("Successfully read CSV file: %s", csv_path)
        return df

    @staticmethod
    def read_portfolio_csv() -> pd.DataFrame:
        """Read portfolio CSV with proper handling."""
        if PORTFOLIO_CSV.exists():
            df = CSVHandler.read_csv(PORTFOLIO_CSV)
            return df.dropna(how='all')
        return pd.DataFrame()

    @staticmethod
    def read_trade_log() -> pd.DataFrame:
        """Read trade log CSV."""
        if TRADE_LOG_CSV.exists():
            return pd.read_csv(TRADE_LOG_CSV)
        return pd.DataFrame()

# Backward compatibility
def _read_csv_with_encoding_fallback(csv_path: Path) -> pd.DataFrame:
    """Legacy function for backward compatibility."""
    return CSVHandler.read_csv(csv_path)

def load_full_portfolio_history() -> pd.DataFrame:
    """Load the complete portfolio history from CSV file for performance analysis."""
    return CSVHandler.read_portfolio_csv()

def rebuild_portfolio_from_trades() -> dict[str, dict[str, float]]:
    """
    Rebuild complete portfolio state from trade log (single source of truth).

    This function focuses ONLY on determining the current holdings (shares, cost, etc.)
    by replaying the trade log. It does NOT calculate the cash balance.

    Returns:
        - portfolio: Dict of {ticker: {shares, total_cost, stop_loss}}
    """
    trade_log = CSVHandler.read_trade_log()
    portfolio = {}  # Portfolio state: {ticker: {shares, total_cost, stop_loss}}

    if trade_log.empty:
        logger.info("Trade log is empty. No portfolio to rebuild.")
        return portfolio

    logger.info("Rebuilding portfolio holdings from %d trades in log", len(trade_log))

    for _, trade in trade_log.iterrows():
        ticker = str(trade["Ticker"]).upper()

        # Process BUY trades
        if pd.notna(trade.get("Shares Bought")) and float(trade["Shares Bought"]) > 0:
            shares_bought = float(trade["Shares Bought"])
            buy_price = float(trade["Buy Price"]) if pd.notna(trade["Buy Price"]) else 0.0
            cost_basis = float(trade["Cost Basis"]) if pd.notna(trade["Cost Basis"]) else shares_bought * buy_price
            stop_loss_pct = 8.0
            stop_loss = buy_price * (1 - stop_loss_pct / 100)

            if ticker not in portfolio:
                portfolio[ticker] = {
                    'shares': shares_bought,
                    'total_cost': cost_basis,
                    'stop_loss': stop_loss
                }
            else:
                portfolio[ticker]['shares'] += shares_bought
                portfolio[ticker]['total_cost'] += cost_basis
                portfolio[ticker]['stop_loss'] = stop_loss

        # Process SELL trades
        if pd.notna(trade.get("Shares Sold")) and float(trade["Shares Sold"]) > 0:
            shares_sold = float(trade["Shares Sold"])
            if ticker in portfolio:
                # Reduce shares and proportional cost
                if portfolio[ticker]['shares'] > shares_sold:
                    cost_per_share = portfolio[ticker]['total_cost'] / portfolio[ticker]['shares']
                    portfolio[ticker]['shares'] -= shares_sold
                    portfolio[ticker]['total_cost'] -= cost_per_share * shares_sold
                    # Clean up floating point precision errors (if shares round to 0, remove position)
                    if round(portfolio[ticker]['shares'], 4) <= 0:
                        del portfolio[ticker]
                        logger.debug("SELL: %s complete sale (after rounding), removed from portfolio", ticker)
                else:
                    del portfolio[ticker]
                    logger.debug("SELL: %s complete sale, removed from portfolio", ticker)

    # Calculate final average buy price for each position
    for ticker, pos in portfolio.items():
        if pos['shares'] > 0:
            pos['buy_price'] = pos['total_cost'] / pos['shares']
        else:
            pos['buy_price'] = 0.0

    logger.info("Portfolio holdings rebuilt: %d positions", len(portfolio))
    return portfolio

def get_latest_cash_balance() -> float:
    """
    Get the latest cash balance, respecting the --set-cash override.
    Source priority:
    1. --set-cash override
    2. Last 'TOTAL' row in portfolio CSV
    3. 'initial_cash' from config.json
    """
    if OVERRIDE_CASH is not None:
        logger.info("Using --set-cash override value: $%.2f", OVERRIDE_CASH)
        return OVERRIDE_CASH

    if PORTFOLIO_CSV.exists():
        df = CSVHandler.read_portfolio_csv()
        if not df.empty:
            df_total = df[df["Ticker"] == "TOTAL"].copy()
            df_total["Date"] = pd.to_datetime(df_total["Date"], format="mixed", errors="coerce")
            valid_total = df_total[pd.notna(df_total["Cash Balance"])].sort_values("Date")
            if not valid_total.empty:
                cash = float(valid_total.iloc[-1]["Cash Balance"])
                logger.info("Using last snapshot cash checkpoint: $%.2f", cash)
                return cash

    config = load_config()
    cash = config.get("initial_cash", 0.0)
    logger.info("No snapshot found. Using config initial cash: $%.2f", cash)
    return cash


def load_latest_portfolio_state() -> tuple[pd.DataFrame | list[dict[str, Any]], float]:
    """
    Load portfolio state using trade log as single source of truth for holdings
    and the new cash balance function for cash.
    """
    logger.info("Loading latest portfolio state...")

    # Get cash balance from the centralized function
    cash = get_latest_cash_balance()

    # Rebuild portfolio holdings from the trade log
    portfolio_dict = rebuild_portfolio_from_trades()

    # Convert dict format to list format for compatibility
    current_positions = []
    for ticker, pos in portfolio_dict.items():
        # Use rounded value to avoid floating point precision issues
        if round(pos.get('shares', 0), 4) > 0:
            current_positions.append({
                'ticker': ticker,
                'shares': round(pos['shares'], 4),
                'cost_basis': round(pos.get('total_cost', 0), 2),
                'buy_price': round(pos.get('buy_price', 0), 2),
                'stop_loss': round(pos.get('stop_loss', 0), 2)
            })

    logger.info(f"Portfolio loaded: {len(current_positions)} positions, cash ${cash:.2f}")
    return current_positions, cash


def main(
    data_dir: Path | None = None,
    show_weekly_prompt: bool = False,
    add_market_data: bool = False,
) -> None:
    """Check versions, then run the trading script."""
    if data_dir is not None:
        set_data_dir(data_dir)

    chatgpt_portfolio, cash = load_latest_portfolio_state()
    chatgpt_portfolio, cash = process_portfolio(chatgpt_portfolio, cash, override_cash_value=OVERRIDE_CASH)
    daily_results(chatgpt_portfolio, cash, show_weekly_prompt=show_weekly_prompt, add_market_data=add_market_data)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None, help="Optional data directory")
    parser.add_argument("--asof", default=None, help="Treat this YYYY-MM-DD as 'today' (e.g., 2025-08-27)")
    parser.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                       help="Set the logging level (default: INFO)")
    parser.add_argument("--update-cash", type=float, default=None,
                       help="Update the initial cash amount in config file (e.g., --update-cash 612.00)")
    parser.add_argument("--set-cash", type=float, default=None,
                       help="Override the calculated cash balance with a specific amount (e.g., --set-cash 273.00)")
    parser.add_argument("--weekly", action="store_true", default=False, help="Present weekly prompt")
    parser.add_argument("--add-market-data", action="store_true", default=False,
                       help="Fetch Alpha Vantage fundamentals/news section (slower, more API usage)")
    args = parser.parse_args()


    # Configure logging level
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format=' %(asctime)s - %(filename)s:%(lineno)d - %(levelname)s - %(message)s'
    )

    # Log initial global state and command-line arguments
    _log_initial_state()
    logger.info("Script started with arguments: %s", vars(args))

    # Handle --update-cash option first
    if args.update_cash is not None:
        if args.data_dir:
            set_data_dir(Path(args.data_dir))
        update_config_cash(args.update_cash)
        exit(0)  # Exit after updating config

    if args.asof:
        set_asof(args.asof)

    if args.set_cash is not None:
        set_override_cash(args.set_cash)

    main(
        Path(args.data_dir) if args.data_dir else None,
        show_weekly_prompt=args.weekly,
        add_market_data=args.add_market_data,
    )
