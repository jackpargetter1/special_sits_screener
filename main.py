"""
Entrypoint for the daily special-situations screener.

Flow: fetch every EDGAR filing from the target date matching our tracked form types
(plus all 8-Ks, filtered locally by disclosed item code) -> classify each into a
category -> email an HTML digest. Designed to run as a Cloud Run Job on a daily
Cloud Scheduler trigger; see README.md for deploy steps.

Env vars:
  SEC_USER_AGENT   required — e.g. "SpecialSitsScreener jack@example.com"
  SMTP_USER, SMTP_PASSWORD   required — mailbox to send from
  EMAIL_TO         required — comma-separated recipient list
  EMAIL_FROM       optional — defaults to SMTP_USER
  SMTP_HOST/PORT   optional — default smtp.gmail.com:587
  RUN_DATE         optional — override the target date (YYYY-MM-DD), for backfills/testing
"""

from __future__ import annotations

import datetime
import logging
import os
import sys

from screener import classify, config, digest, mailer, market_data as market_data_mod, text_search
from screener.edgar_client import EdgarClient
from screener.models import build_situation, collapse_duplicate_filers, dedupe_hits, dedupe_situations

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("special_sits_screener")


def get_run_date() -> str:
    override = os.environ.get("RUN_DATE")
    if override:
        return override
    d = datetime.date.today() - datetime.timedelta(days=1)
    while d.weekday() >= 5:  # skip Sat/Sun; doesn't yet account for market holidays
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def run(run_date: str | None = None, send: bool = True) -> dict[str, list]:
    run_date = run_date or get_run_date()
    client = EdgarClient()

    logger.info("Fetching filings for %s across %d form types", run_date, len(config.ALL_QUERY_FORMS))
    hits = client.search_filings(config.ALL_QUERY_FORMS, run_date, run_date)
    logger.info("Fetched %d raw filings", len(hits))

    hits = dedupe_hits(hits)
    logger.info("%d raw filings after de-duping", len(hits))

    situations_by_category: dict[str, list] = {}
    for hit in hits:
        category = classify.classify_hit(hit)
        if not category:
            continue
        situations_by_category.setdefault(category, []).append(build_situation(hit, category))

    logger.info("Running keyword search: categories with no exact form/item-code signal (8-K + 6-K)")
    primary_matches = text_search.search_primary_matches(client, run_date)
    logger.info("Found %d keyword match(es) across primary text-search categories", len(primary_matches))
    for hit, category in primary_matches:
        situations_by_category.setdefault(category, []).append(
            build_situation(hit, category, via_text_search=True)
        )

    logger.info("Running keyword search: 6-K gap-fill for categories with an exact domestic 8-K signal")
    supplementary_matches = text_search.search_supplementary_6k_matches(client, run_date)
    logger.info("Found %d 6-K keyword match(es)", len(supplementary_matches))
    for hit, category in supplementary_matches:
        situations_by_category.setdefault(category, []).append(
            build_situation(hit, category, via_text_search=True)
        )

    # Item 2.01 (and the 6-K "acquisition" phrases above) cover both completed
    # acquisitions and dispositions with no way to tell which from metadata alone --
    # reclassify any "acquisition" situation whose accession also matches a
    # divestiture keyword into "divestiture" instead.
    divestiture_ids = text_search.find_divestiture_hit_ids(client, run_date, text_search.PRIMARY_FORMS)
    divestiture_accessions = {hid.split(":", 1)[0] for hid in divestiture_ids if ":" in hid}
    if divestiture_accessions and situations_by_category.get("acquisition"):
        still_acquisition = []
        moved = 0
        for s in situations_by_category["acquisition"]:
            if s.accession in divestiture_accessions:
                s.category = "divestiture"
                s.via_text_search = True
                situations_by_category.setdefault("divestiture", []).append(s)
                moved += 1
            else:
                still_acquisition.append(s)
        situations_by_category["acquisition"] = still_acquisition
        if moved:
            logger.info("Reclassified %d acquisition situation(s) as divestiture by keyword match", moved)

    for category, situations in situations_by_category.items():
        situations_by_category[category] = dedupe_situations(situations)

    pre_collapse_total = sum(len(v) for v in situations_by_category.values())
    all_situations = [s for situations in situations_by_category.values() for s in situations]
    collapsed = collapse_duplicate_filers(all_situations, config.CATEGORY_ORDER)
    if len(collapsed) != pre_collapse_total:
        logger.info(
            "Collapsed %d duplicate filer row(s) (same ticker/company across filings)",
            pre_collapse_total - len(collapsed),
        )
    situations_by_category = {}
    for s in collapsed:
        situations_by_category.setdefault(s.category, []).append(s)

    total = sum(len(v) for v in situations_by_category.values())
    logger.info("Classified %d special situations across %d categories", total, len(situations_by_category))

    if total == 0:
        logger.info("Nothing to report for %s — skipping email.", run_date)
        return situations_by_category

    if send:
        tickers = [
            s.ticker
            for situations in situations_by_category.values()
            for s in situations
            if s.ticker
        ]
        logger.info("Fetching yfinance market data for %d unique tickers", len(set(tickers)))
        market_data = market_data_mod.fetch_market_data(tickers)

        html = digest.build_html_digest(situations_by_category, run_date, market_data)
        subject = f"SpecialSits Screener — {run_date} ({total} situations)"
        mailer.send_email(subject, html)
        logger.info("Email sent for %s.", run_date)

    return situations_by_category


def main() -> None:
    try:
        run()
    except Exception:
        logger.exception("Screener run failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
