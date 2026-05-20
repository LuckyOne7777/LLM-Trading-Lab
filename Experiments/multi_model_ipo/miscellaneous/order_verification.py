from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from libb.execution.get_market_data import download_data_on_given_date

TODAY = _dt.date.today()

from dotenv import load_dotenv
load_dotenv()

MINIMUM_MARKET_CAP = 200_000_000
IPO_LOCKOUT_YEARS = 3
MINIMUM_AVG_VOLUME = 1_000_000

POLYGON_API_KEY = (
    os.getenv("POLYGON_API_KEY")
    or os.getenv("MASSIVE_API_KEY")
)
FMP_API_KEY = os.getenv("FMP_API_KEY") or os.getenv("MASSIVE_API_KEY")

POLYGON_BASE_URL = "https://api.polygon.io"
FMP_BASE_URL = "https://financialmodelingprep.com"

REQUEST_TIMEOUT = 15

SPAC_PATTERNS = (
    r"\bspac\b",
    r"\bblank check\b",
    r"\bacquisition corp\b",
    r"\bacquisition corporation\b",
    r"\bcapital acquisition\b",
    r"\bmerger corp\b",
)

# =========================================================
# CACHE
# =========================================================

_TICKER_CACHE: dict[str, dict] = {}
_CACHE_LOCK = Lock()
session = requests.Session()

retry = Retry(
    total=3,
    backoff_factor=0.4,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET"]),
)

session.mount("https://", HTTPAdapter(max_retries=retry))
session.mount("http://", HTTPAdapter(max_retries=retry))

CACHE_PATH = Path(
    os.getenv(
        "ORDER_FILTER_CACHE_PATH",
        str(Path(__file__).with_suffix(".cache.sqlite3")),
    )
)

DB_LOCK = Lock()


def _init_db() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CACHE_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshot_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                fetched_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()


_init_db()


# =========================================================
# HELPERS
# =========================================================

def _normalize_ticker(ticker: str) -> str:
    return (ticker or "").upper().strip()


def _safe_float(x: Any) -> float | None:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _safe_int(x: Any) -> int | None:
    try:
        if x is None or x == "":
            return None
        return int(float(x))
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> _dt.date | None:
    if value is None or value == "":
        return None

    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return value

    if isinstance(value, _dt.datetime):
        return value.date()

    if isinstance(value, (int, float)):
        try:
            # Assume UNIX seconds.
            return _dt.date.fromtimestamp(float(value))
        except Exception:
            return None

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None

        # Handle ISO-ish strings and common datetime formats.
        candidates = (
            raw,
            raw.split("T", 1)[0],
            raw.split(" ", 1)[0],
        )
        for candidate in candidates:
            try:
                return _dt.date.fromisoformat(candidate)
            except Exception:
                continue

        try:
            return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except Exception:
            return None

    return None


def _days_since(value: Any, today: _dt.date) -> int | None:
    d = _parse_date(value)
    if d is None:
        return None
    return (today - d).days


def _today() -> _dt.date:
    return _dt.date.today()


def _has_spac_signal(name: str = "", description: str = "", industry: str = "") -> bool:
    text = " ".join([name or "", description or "", industry or ""]).lower()
    return any(re.search(pattern, text) for pattern in SPAC_PATTERNS)


def _truncate(text: str, limit: int = 220) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def _cache_get(ticker: str):
    with _CACHE_LOCK:
        return _TICKER_CACHE.get(ticker)


def _cache_set(ticker: str, value: dict):
    with _CACHE_LOCK:
        _TICKER_CACHE[ticker] = value


def _db_get(cache_key: str, ttl_seconds: int | None = None) -> dict | None:
    now = int(_dt.datetime.utcnow().timestamp())
    with DB_LOCK, sqlite3.connect(CACHE_PATH) as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM snapshot_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()

    if not row:
        return None

    payload, fetched_at = row
    if ttl_seconds is not None and (now - int(fetched_at)) > ttl_seconds:
        return None

    try:
        return json.loads(payload)
    except Exception:
        return None


def _db_set(cache_key: str, payload: dict) -> None:
    now = int(_dt.datetime.utcnow().timestamp())
    with DB_LOCK, sqlite3.connect(CACHE_PATH) as conn:
        conn.execute(
            """
            INSERT INTO snapshot_cache (cache_key, payload, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload = excluded.payload,
                fetched_at = excluded.fetched_at
            """,
            (cache_key, json.dumps(payload, separators=(",", ":"), default=str), now),
        )
        conn.commit()


def _request_json(url: str, params: dict, api_key: str | None, timeout: int = REQUEST_TIMEOUT) -> dict | list | None:
    if not api_key:
        return None

    params = dict(params)
    params.setdefault("apikey", api_key)
    params.setdefault("apiKey", api_key)

    try:
        resp = session.get(url, params=params, timeout=timeout)
        if resp.status_code in (404, 429):
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None
    except ValueError:
        return None


def _extract_from_statement(row: dict, *keys: str) -> float | None:
    for key in keys:
        if key in row:
            value = row.get(key)
            if isinstance(value, dict) and value.get("value") is not None:
                num = _safe_float(value.get("value"))
                if num is not None:
                    return num
            num = _safe_float(value)
            if num is not None:
                return num
    return None


def _extract_value(d: dict, *keys: str) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict) and v.get("value") is not None:
            return _safe_float(v.get("value"))
        if v is not None and not isinstance(v, dict):
            num = _safe_float(v)
            if num is not None:
                return num
    return None


def _unique_tickers(items) -> list[str]:
    seen = set()
    out = []
    for t in items:
        t = _normalize_ticker(t)
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


# =========================================================
# API LAYERS
# =========================================================

def _get_polygon_ticker_details(ticker: str) -> dict | None:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return None

    cache_key = f"polygon:{ticker}"
    cached = _db_get(cache_key, ttl_seconds=7 * 24 * 3600)
    if isinstance(cached, dict):
        return cached

    data = _request_json(
        f"{POLYGON_BASE_URL}/v3/reference/tickers/{ticker}",
        {},
        POLYGON_API_KEY,
    )
    if not isinstance(data, dict):
        return None

    results = data.get("results")
    if isinstance(results, dict):
        _db_set(cache_key, results)
        return results

    return None


def _get_fmp_profile(ticker: str) -> dict | None:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return None

    cache_key = f"fmp_profile:{ticker}"
    cached = _db_get(cache_key, ttl_seconds=14 * 24 * 3600)
    if isinstance(cached, dict):
        return cached

    data = _request_json(
        f"{FMP_BASE_URL}/stable/profile",
        {"symbol": ticker},
        FMP_API_KEY,
    )

    if isinstance(data, list) and data:
        data = data[0]

    if isinstance(data, dict):
        _db_set(cache_key, data)
        return data

    return None


def _get_fmp_quote(ticker: str) -> dict | None:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return None

    cache_key = f"fmp_quote:{ticker}"
    cached = _db_get(cache_key, ttl_seconds=15 * 60)
    if isinstance(cached, dict):
        return cached

    data = _request_json(
        f"{FMP_BASE_URL}/stable/quote",
        {"symbol": ticker},
        FMP_API_KEY,
    )

    if isinstance(data, list) and data:
        data = data[0]

    if isinstance(data, dict):
        _db_set(cache_key, data)
        return data

    return None


def _get_fmp_key_metrics(ticker: str) -> dict | None:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return None

    cache_key = f"fmp_key_metrics:{ticker}"
    cached = _db_get(cache_key, ttl_seconds=14 * 24 * 3600)
    if isinstance(cached, dict):
        return cached

    data = _request_json(
        f"{FMP_BASE_URL}/stable/key-metrics-ttm",
        {"symbol": ticker},
        FMP_API_KEY,
    )

    if isinstance(data, list) and data:
        data = data[0]

    if isinstance(data, dict):
        _db_set(cache_key, data)
        return data

    return None


def _get_fmp_ratios(ticker: str) -> dict | None:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return None

    cache_key = f"fmp_ratios:{ticker}"
    cached = _db_get(cache_key, ttl_seconds=14 * 24 * 3600)
    if isinstance(cached, dict):
        return cached

    data = _request_json(
        f"{FMP_BASE_URL}/stable/ratios-ttm",
        {"symbol": ticker},
        FMP_API_KEY,
    )

    if isinstance(data, list) and data:
        data = data[0]

    if isinstance(data, dict):
        _db_set(cache_key, data)
        return data

    return None


def _get_fmp_statements(ticker: str) -> dict:
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return {}

    cache_key = f"fmp_statements:{ticker}"
    cached = _db_get(cache_key, ttl_seconds=14 * 24 * 3600)
    if isinstance(cached, dict):
        return cached

    income = _request_json(
        f"{FMP_BASE_URL}/stable/income-statement",
        {"symbol": ticker, "period": "quarter", "limit": 4},
        FMP_API_KEY,
    )
    balance = _request_json(
        f"{FMP_BASE_URL}/stable/balance-sheet-statement",
        {"symbol": ticker, "period": "quarter", "limit": 4},
        FMP_API_KEY,
    )
    cashflow = _request_json(
        f"{FMP_BASE_URL}/stable/cash-flow-statement",
        {"symbol": ticker, "period": "quarter", "limit": 4},
        FMP_API_KEY,
    )

    out = {
        "income": income[0] if isinstance(income, list) and income else None,
        "balance": balance[0] if isinstance(balance, list) and balance else None,
        "cashflow": cashflow[0] if isinstance(cashflow, list) and cashflow else None,
    }
    _db_set(cache_key, out)
    return out


def _get_yfinance_fallback(ticker: str) -> dict:
    """
    Last-resort fallback, used only when reference/fundamental data is missing.
    Kept intentionally narrow to avoid extra API calls.
    """
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return {}

    out: dict[str, Any] = {}

    try:
        fast = yf.Ticker(ticker).fast_info
        out["market_cap"] = _safe_float(fast.get("market_cap"))
        out["volume"] = _safe_int(fast.get("lastVolume") or fast.get("volume"))
    except Exception:
        pass

    try:
        info = yf.Ticker(ticker).info
        if "shares_outstanding" not in out:
            out["shares_outstanding"] = _safe_float(info.get("sharesOutstanding"))
        if "ipo_date" not in out:
            out["ipo_date"] = _parse_date(
                info.get("ipoDate") or info.get("firstTradeDateEpochUtc")
            )
    except Exception:
        pass

    return out


# =========================================================
# SINGLE SNAPSHOT FETCH
# =========================================================

def _get_ticker_snapshot(ticker: str) -> dict:
    """
    One unified metadata fetch.

    Returns:
    {
        "shares_outstanding": float | None,
        "ipo_date": date | None,
        "market_cap": float | None,
        "price": float | None,
        "avg_volume": float | None,
        "sector": str,
        "industry": str,
        "is_spac": bool,
        "fundamentals": dict,
    }
    """

    ticker = _normalize_ticker(ticker)
    if not ticker:
        return {
            "shares_outstanding": None,
            "ipo_date": None,
            "market_cap": None,
            "price": None,
            "avg_volume": None,
            "sector": "UNKNOWN",
            "industry": "UNKNOWN",
            "is_spac": False,
            "fundamentals": {},
        }

    cached = _cache_get(ticker)
    if cached:
        return cached

    snapshot = {
        "shares_outstanding": None,
        "ipo_date": None,
        "market_cap": None,
        "price": None,
        "avg_volume": None,
        "sector": "UNKNOWN",
        "industry": "UNKNOWN",
        "exchange": "UNKNOWN",
        "is_spac": False,
        "fundamentals": {},
    }

    # =====================================================
    # PRIMARY: Polygon reference data
    # =====================================================

    polygon = _get_polygon_ticker_details(ticker) or {}
    if polygon:
        snapshot["market_cap"] = _safe_float(
            polygon.get("market_cap")
            or polygon.get("market_capitalization")
        )
        snapshot["shares_outstanding"] = _safe_float(
            polygon.get("share_class_shares_outstanding")
            or polygon.get("weighted_shares_outstanding")
            or polygon.get("shares_outstanding")
        )
        snapshot["ipo_date"] = _parse_date(
            polygon.get("list_date")
            or polygon.get("listing_date")
            or polygon.get("ipo_date")
        )
        snapshot["sector"] = (
            polygon.get("sic_description")
            or polygon.get("market")
            or polygon.get("primary_exchange")
            or "UNKNOWN"
        )
        snapshot["industry"] = (
            polygon.get("market")
            or polygon.get("type")
            or "UNKNOWN"
        )
        snapshot["exchange"] = (
            polygon.get("primary_exchange")
            or "UNKNOWN"
        )

    # =====================================================
    # FMP enrichment / fallback
    # =====================================================

    profile = _get_fmp_profile(ticker) or {}
    quote = _get_fmp_quote(ticker) or {}
    key_metrics = _get_fmp_key_metrics(ticker) or {}
    ratios = _get_fmp_ratios(ticker) or {}
    statements = _get_fmp_statements(ticker) or {}

    if snapshot["market_cap"] is None:
        snapshot["market_cap"] = _safe_float(
            profile.get("mktCap")
            or profile.get("marketCap")
            or quote.get("marketCap")
        )

    if snapshot["shares_outstanding"] is None:
        snapshot["shares_outstanding"] = _safe_float(
            profile.get("sharesOutstanding")
            or quote.get("sharesOutstanding")
        )

    if snapshot["ipo_date"] is None:
        snapshot["ipo_date"] = _parse_date(
            profile.get("ipoDate")
            or profile.get("ipo_date")
            or quote.get("ipoDate")
        )

    if snapshot["price"] is None:
        snapshot["price"] = _safe_float(
            quote.get("price")
            or quote.get("previousClose")
            or profile.get("price")
        )

    snapshot["avg_volume"] = _safe_float(
        quote.get("avgVolume")
        or quote.get("averageVolume")
        or quote.get("volumeAvg")
    )
    if snapshot["avg_volume"] is None:
        snapshot["avg_volume"] = _safe_float(
            profile.get("volAvg")
            or profile.get("avgVolume")
        )

    snapshot["sector"] = (
        profile.get("sector")
        or snapshot["sector"]
        or "UNKNOWN"
    )
    snapshot["industry"] = (
        profile.get("industry")
        or snapshot["industry"]
        or "UNKNOWN"
    )

    snapshot["is_spac"] = _has_spac_signal(
        name=profile.get("companyName") or profile.get("name") or polygon.get("name") or ticker,
        description=profile.get("description") or polygon.get("description") or "",
        industry=profile.get("industry") or "",
    )

    # Keep lightweight fundamentals available for downstream consumers.
    revenue = None
    net_income = None
    cash = None
    debt = None
    ocf = None
    fcf = None
    current_ratio = None
    debt_to_equity = None
    shares_float = None

    income = statements.get("income") or {}
    balance = statements.get("balance") or {}
    cashflow = statements.get("cashflow") or {}

    if isinstance(income, dict):
        revenue = _extract_from_statement(
            income,
            "revenue",
            "revenueTTM",
            "totalRevenue",
            "total_revenue",
            "salesRevenueNet",
        )
        net_income = _extract_from_statement(
            income,
            "netIncome",
            "netIncomeTTM",
            "netIncomeLoss",
            "netIncome_loss",
        )

    if isinstance(balance, dict):
        cash = _extract_from_statement(
            balance,
            "cashAndCashEquivalents",
            "cashAndCashEquivalentsAtCarryingValue",
            "cash",
            "cashAndShortTermInvestments",
        )
        debt = _extract_from_statement(
            balance,
            "totalDebt",
            "longTermDebt",
            "shortTermDebt",
            "longTermDebtAndCapitalLeaseObligations",
        )

    if isinstance(cashflow, dict):
        ocf = _extract_from_statement(
            cashflow,
            "operatingCashFlow",
            "netCashProvidedByOperatingActivities",
            "netCashFlowProvidedByUsedInOperatingActivities",
        )
        capex = _extract_from_statement(
            cashflow,
            "capitalExpenditure",
            "capitalExpenditures",
        )
        if ocf is not None and capex is not None:
            fcf = ocf - abs(capex)

    if isinstance(ratios, dict):
        current_ratio = _extract_from_statement(
            ratios,
            "currentRatio",
            "currentRatioTTM",
        )
        debt_to_equity = _extract_from_statement(
            ratios,
            "debtToEquity",
            "debtToEquityTTM",
        )

    if isinstance(key_metrics, dict):
        shares_float = _safe_float(
            key_metrics.get("sharesOutstanding")
            or key_metrics.get("weightedAverageShsOut")
            or key_metrics.get("sharesFloat")
        )

    snapshot["fundamentals"] = {
        "revenue": revenue,
        "net_income": net_income,
        "cash": cash,
        "total_debt": debt,
        "operating_cash_flow": ocf,
        "free_cash_flow": fcf,
        "current_ratio": current_ratio,
        "debt_to_equity": debt_to_equity,
        "shares_float": shares_float,
        "data_completeness": round(
            sum(x is not None for x in [revenue, net_income, cash, debt, ocf]) / 5.0,
            2,
        ),
    }

    # =====================================================
    # LAST RESORT FALLBACK
    # =====================================================

    missing_core = snapshot["shares_outstanding"] is None or snapshot["ipo_date"] is None
    if missing_core:
        fallback = _get_yfinance_fallback(ticker)
        if snapshot["market_cap"] is None:
            snapshot["market_cap"] = fallback.get("market_cap")
        if snapshot["shares_outstanding"] is None:
            snapshot["shares_outstanding"] = fallback.get("shares_outstanding")
        if snapshot["ipo_date"] is None:
            snapshot["ipo_date"] = fallback.get("ipo_date")
        if snapshot["price"] is None:
            snapshot["price"] = _safe_float(fallback.get("price"))
        if snapshot["avg_volume"] is None:
            snapshot["avg_volume"] = _safe_float(fallback.get("volume"))

    _cache_set(ticker, snapshot)
    return snapshot


# =========================================================
# VALIDATION
# =========================================================

def _calculate_market_cap(
    order: dict,
    snapshot: dict,
) -> float:
    direct_market_cap = snapshot.get("market_cap")
    if isinstance(direct_market_cap, (int, float)):
        return float(direct_market_cap)

    shares = snapshot.get("shares_outstanding")
    shares_f = _safe_float(shares) or 0.0
    if shares_f <= 0:
        return 0.0

    order_type = (order.get("order_type", "MARKET") or "MARKET").upper()
    limit_price = _safe_float(order.get("limit_price")) or 0.0

    if order_type == "LIMIT":
        return shares_f * limit_price

    price = _safe_float(snapshot.get("price"))
    if price is None or price <= 0:
        ticker = order.get("ticker")
        try:
            ticker_data = download_data_on_given_date(ticker, TODAY)
            price = _safe_float(ticker_data.get("Open"))
        except Exception:
            price = 0.0

    return shares_f * float(price or 0.0)


def _get_rejection_reasons(
    order: dict,
    snapshot: dict,
) -> list[str]:
    reasons: list[str] = []

    # =====================================================
    # IPO CHECK
    # =====================================================

    ipo_date = snapshot.get("ipo_date")
    if ipo_date:
        age_days = _days_since(ipo_date, TODAY)
        if age_days is not None:
            age_years = age_days / 365.25
            if age_years < IPO_LOCKOUT_YEARS:
                reasons.append(
                    f"IPO too recent ({age_years:.1f} yrs < {IPO_LOCKOUT_YEARS})"
                )
    else:
        reasons.append("IPO date unknown — cannot verify age")

    # =====================================================
    # MARKET CAP CHECK
    # =====================================================

    market_cap = _calculate_market_cap(order, snapshot)
    if market_cap < MINIMUM_MARKET_CAP:
        reasons.append(
            f"market cap too low (${market_cap:,.0f} < ${MINIMUM_MARKET_CAP:,.0f})"
        )

    return reasons


# =========================================================
# MAIN FILTER
# =========================================================

def filter_orders(
    orders: dict,
    max_workers: int = 8,
) -> tuple[dict, list[dict] | None]:
    order_list = orders.get("orders", [])

    filtered_orders = []
    rejected_orders = []

    # =====================================================
    # PRELOAD UNIQUE TICKERS DETERMINISTICALLY
    # =====================================================

    unique_tickers = sorted(
        {
            _normalize_ticker(o.get("ticker"))
            for o in order_list
            if o.get("ticker")
        }
    )

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_get_ticker_snapshot, unique_tickers))

    # =====================================================
    # FILTER LOOP
    # =====================================================

    for order in order_list:
        action = order.get("action")

        # only reject BUY orders
        if action != "b":
            filtered_orders.append(order)
            continue

        ticker = _normalize_ticker(order.get("ticker"))
        snapshot = _get_ticker_snapshot(ticker)

        # Exclude SPACs at the order layer.
        if snapshot.get("is_spac"):
            rejected_orders.append(
                {
                    **order,
                    "rejection_reasons": ["SPAC / blank-check company excluded"],
                }
            )
            continue

        reasons = _get_rejection_reasons(order, snapshot)

        if reasons:
            rejected_orders.append(
                {
                    **order,
                    "rejection_reasons": reasons,
                }
            )
        else:
            filtered_orders.append(order)

    return (
        {"orders": filtered_orders},
        rejected_orders or None,
    )


# =========================================================
# OPTIONAL DIAGNOSTICS
# =========================================================

def build_snapshot_summary(ticker: str) -> dict:
    """
    Convenience helper for debugging.
    Not used by filter_orders, but handy in notebooks and local checks.
    """
    return _get_ticker_snapshot(ticker)


if __name__ == "__main__":
    sample = {
        "orders": [
            {"ticker": "SNOW", "action": "b", "order_type": "MARKET"},
            {"ticker": "MDB", "action": "b", "order_type": "LIMIT", "limit_price": 300},
        ]
    }

    filtered, rejected = filter_orders(sample)
    print(filtered)
    print(rejected)
