"""
Best-effort yfinance enrichment for public-company situations: day % change,
latest market cap, and industry. Looked up once per unique ticker per run.

Failures (delisted ticker, rate limit, network blip) are swallowed per-ticker —
a bad lookup should never take down the whole digest, it just leaves that row's
market-data columns blank.

Ticker notation mismatch: EDGAR full-text search returns tickers in SEC's own
notation, which uses a dot for share classes (e.g. "BRK.A", "BF.B"). Yahoo Finance
uses a dash for the same thing ("BRK-A", "BF-B"). Querying yfinance with the raw
SEC ticker for one of these hits Yahoo's API with a symbol it's never heard of —
it 404s, yfinance logs a scary "HTTP Error 404" and falls back to a near-empty
payload, and the digest row silently shows blank market data for what's actually
a normal large-cap stock. We convert dot -> dash before every lookup to avoid it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yfinance as yf

logger = logging.getLogger(__name__)

# yfinance's own logger prints "response code=404" / "HTTP Error 404" at ERROR level
# for any symbol Yahoo doesn't recognize. That's expected and routine for us — e.g. a
# going-private target that's already been delisted, or a ticker we couldn't resolve —
# and we log our own concise warning for real failures, so silence yfinance's noise.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def _to_yahoo_symbol(ticker: str) -> str:
    """SEC's share-class notation uses a dot (BRK.A); Yahoo Finance uses a dash (BRK-A)."""
    return ticker.replace(".", "-")


@dataclass
class MarketData:
    day_change_pct: float | None
    market_cap: float | None
    industry: str | None


_EMPTY = MarketData(None, None, None)


def _fetch_one(ticker: str) -> MarketData:
    symbol = _to_yahoo_symbol(ticker)
    try:
        info = yf.Ticker(symbol).info
    except Exception as exc:
        logger.warning("yfinance lookup failed for %s (%s): %s", ticker, symbol, exc)
        return _EMPTY

    # yfinance doesn't raise on an unrecognized symbol — it falls back to a near-empty
    # dict (no marketCap/regularMarketChangePercent). Treat "no market cap" as a miss
    # worth a log line, since it usually means a delisted/private/mistyped ticker.
    if not info or info.get("marketCap") is None:
        logger.warning("No market data from yfinance for %s (queried as %s)", ticker, symbol)
        return _EMPTY

    day_change_pct = info.get("regularMarketChangePercent")
    market_cap = info.get("marketCap")
    industry = info.get("industry")
    return MarketData(day_change_pct, market_cap, industry)


def fetch_market_data(tickers: list[str]) -> dict[str, MarketData]:
    """Fetch market data for each unique, non-empty ticker. Missing/failed lookups are omitted."""
    unique = sorted({t for t in tickers if t})
    result: dict[str, MarketData] = {}
    for ticker in unique:
        result[ticker] = _fetch_one(ticker)
    return result
