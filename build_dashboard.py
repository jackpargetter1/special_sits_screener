"""
Builds the interactive HTML dashboard: reuses main.run()'s situation-gathering
pipeline (same taxonomy, dedup, and classification as the email digest -- see
main.py/README.md), then enriches every public-company situation with financial
fundamentals (screener/financials.py: latest SEC 10-K, falling back to yfinance) and
renders an expandable-row dashboard (screener/dashboard.py) to a static HTML file.

Local, on-demand use:

    RUN_DATE=2026-08-06 python build_dashboard.py [output.html]

Hosted, auto-refreshing use (see README.md "Hosting the dashboard" for full Cloud Run
+ Cloud Scheduler setup): set DASHBOARD_GCS_BUCKET and this also uploads the rendered
file to gs://$DASHBOARD_GCS_BUCKET/dashboard.html with no-cache headers, so a Cloud
Scheduler job running this on a cron re-publishes the same public URL each time --
nothing to redeploy, the object just gets overwritten in place.

Fetching fundamentals for every public company is the slow part (one SEC XBRL
companyfacts request + one yfinance lookup per ticker, sequential to stay well under
SEC's rate limit) -- expect ~1-2 minutes for a normal day's situation count.
"""

from __future__ import annotations

import logging
import os
import sys

import main
from screener import dashboard, market_data as market_data_mod
from screener.financials import fetch_fundamentals

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("special_sits_dashboard")


def _upload_to_gcs(local_path: str, bucket_name: str, blob_name: str = "dashboard.html") -> None:
    from google.cloud import storage  # imported lazily -- optional dependency, only needed for hosting

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    # no-cache: browsers/CDNs must not serve a stale copy between 15-minute refreshes
    blob.cache_control = "no-cache, max-age=0"
    blob.upload_from_filename(local_path, content_type="text/html; charset=utf-8")
    logger.info("Uploaded to gs://%s/%s (public URL: https://storage.googleapis.com/%s/%s)",
                bucket_name, blob_name, bucket_name, blob_name)


def build(run_date: str | None = None, output_path: str = "dashboard.html") -> str:
    run_date = run_date or main.get_run_date()
    situations_by_category = main.run(run_date=run_date, send=False)

    all_situations = [s for situations in situations_by_category.values() for s in situations]
    public_situations = [s for s in all_situations if s.is_public]
    tickers = sorted({s.ticker for s in public_situations if s.ticker})

    logger.info("Fetching market data for %d tickers", len(tickers))
    market_data = market_data_mod.fetch_market_data(tickers)

    logger.info("Fetching fundamentals (SEC 10-K, yfinance fallback) for %d tickers", len(tickers))
    fundamentals = {}
    for i, s in enumerate(public_situations, 1):
        if s.ticker in fundamentals:
            continue
        logger.info("  [%d/%d] %s", i, len(public_situations), s.ticker)
        fundamentals[s.ticker] = fetch_fundamentals(s.ticker, s.cik)

    # auto_refresh_seconds is only meaningful once the page is actually hosted
    # somewhere that re-publishes on a cron (see DASHBOARD_GCS_BUCKET below) -- for a
    # one-off local/Artifact snapshot it just harmlessly re-renders the same content.
    html = dashboard.build_html_dashboard(
        situations_by_category, run_date, market_data, fundamentals, auto_refresh_seconds=900
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    total = sum(len(v) for v in situations_by_category.values())
    logger.info("Wrote %s (%d situations, %d with fundamentals)", output_path, total, len(fundamentals))

    bucket_name = os.environ.get("DASHBOARD_GCS_BUCKET")
    if bucket_name:
        _upload_to_gcs(output_path, bucket_name)

    return output_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "dashboard.html"
    run_date_arg = sys.argv[2] if len(sys.argv) > 2 else None
    build(run_date=run_date_arg, output_path=out)
