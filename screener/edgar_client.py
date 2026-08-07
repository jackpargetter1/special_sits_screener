"""
Thin client for SEC EDGAR's full-text search API (efts.sec.gov).

Why full-text search instead of the daily master.idx file: the search API returns
structured JSON (form type, CIK, ticker, filing date, and — critically for 8-Ks —
the disclosed item numbers) in one call, instead of a fixed-format index file that
would need a second fetch per filing just to find out which 8-K item was disclosed.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from . import config

logger = logging.getLogger(__name__)


class EdgarClientError(RuntimeError):
    pass


class EdgarClient:
    def __init__(self, user_agent: str | None = None, session: requests.Session | None = None):
        self.user_agent = user_agent or os.environ.get(config.SEC_USER_AGENT_ENV)
        if not self.user_agent:
            raise EdgarClientError(
                f"Set the {config.SEC_USER_AGENT_ENV} env var to something like "
                '"YourOrg/SpecialSitsScreener you@example.com" — SEC blocks requests '
                "without an identifying User-Agent header."
            )
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )

    def _get(self, url: str, params: dict[str, Any]) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise EdgarClientError(f"HTTP {resp.status_code} from {url}")
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, EdgarClientError) as exc:
                last_error = exc
                wait = config.RETRY_BACKOFF_SECONDS * attempt
                logger.warning("Request failed (attempt %d/%d): %s — retrying in %.1fs", attempt, config.MAX_RETRIES, exc, wait)
                time.sleep(wait)
        raise EdgarClientError(f"Giving up on {url} after {config.MAX_RETRIES} attempts") from last_error

    def search_filings(
        self, forms: list[str], start_date: str, end_date: str, query: str = '""'
    ) -> list[dict]:
        """
        Return raw hit dicts (the `_source` payload plus `_id`) for every filing
        matching the given root forms and date range (inclusive, YYYY-MM-DD).
        Paginates automatically up to config.MAX_PAGES_PER_QUERY pages.

        query: EFTS "q" phrase param. Defaults to an empty phrase match ("no text
        constraint, just form/date filters" -- the normal case for every form we
        classify by exact form type or item code). Pass a quoted phrase (e.g.
        '"Chapter 11"') to additionally require that exact text in the filing --
        used by text_search.py for forms like 6-K that carry no item-code metadata
        to classify by, so keyword matching is the only available signal.
        """
        hits: list[dict] = []
        from_offset = 0
        forms_param = ",".join(forms)

        for page in range(config.MAX_PAGES_PER_QUERY):
            params = {
                "q": query,
                "forms": forms_param,
                "dateRange": "custom",
                "startdt": start_date,
                "enddt": end_date,
                "from": from_offset,
            }
            data = self._get(config.EFTS_SEARCH_URL, params)
            page_hits = data.get("hits", {}).get("hits", [])
            for h in page_hits:
                source = dict(h.get("_source", {}))
                source["_id"] = h.get("_id", "")
                hits.append(source)

            total = data.get("hits", {}).get("total", {}).get("value", 0)
            from_offset += config.PAGE_SIZE
            time.sleep(config.REQUEST_DELAY_SECONDS)

            if not page_hits or from_offset >= total:
                break
        else:
            logger.warning(
                "Hit MAX_PAGES_PER_QUERY (%d) for forms=%s %s..%s — results may be truncated",
                config.MAX_PAGES_PER_QUERY, forms_param, start_date, end_date,
            )

        return hits
