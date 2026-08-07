"""
Static configuration for the special situations screener.

Form-type labels below are the exact `root_forms` values returned by SEC EDGAR's
full-text search API (efts.sec.gov). These were verified by querying the live API
directly (see README for how to re-verify / extend). A few notes on quirks:

- Schedule 13D shows up as "SCHEDULE 13D" (not "SC 13D"). Schedule 13G ("SCHEDULE 13G")
  is the passive-holder equivalent and is intentionally excluded — we only care about
  filers signaling intent to influence management.
- "root_forms" already groups amendments under the base form (e.g. a SC 13D/A shows up
  under root form "SCHEDULE 13D"), so we don't need to list /A variants separately.
- 15-12B exists per the EDGAR filer manual but didn't show up in a spot-check window;
  15-12G and 15-15D (the other two Form 15 variants) did. Kept in the map in case it's
  just rare in the sample window — harmless if it never matches.
"""

import os

# --- SEC API -----------------------------------------------------------------

EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
SEC_USER_AGENT_ENV = "SEC_USER_AGENT"  # e.g. "SpecialSitsScreener jack@example.com"

# Aggregate rate limit across all SEC domains is 10 req/s. Stay well under it.
REQUEST_DELAY_SECONDS = 0.15
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0
PAGE_SIZE = 100
MAX_PAGES_PER_QUERY = 50  # safety cap (5,000 filings/day) so a bug can't loop forever

# --- Categorization ------------------------------------------------------------
#
# Taxonomy is the user-specified 20-category list (plus tracked_fund_13f, kept on
# as a 21st bucket by request even though it's not part of that list -- see
# tracked_funds.py). Each category is backed by one of three signal types, in
# descending order of precision:
#
#   1. Exact form type (FORM_CATEGORY_MAP) or 8-K item code (EIGHT_K_ITEM_CATEGORY_MAP)
#      -- structured metadata, no false positives.
#   2. Exact signal that's then keyword-split -- currently only "acquisition" vs
#      "divestiture" from 8-K item 2.01 (which covers both and doesn't say which).
#   3. Pure keyword search (text_search.py) -- for categories with no dedicated SEC
#      form/item at all. Fuzzier: a phrase match can be a false positive (e.g. a
#      filing mentioning "Chapter 11" only in passing about a *different* company).
#      Every such Situation carries via_text_search=True and gets a visible "TEXT
#      MATCH" badge in the digest rather than being presented at the same
#      confidence as an exact match.
#
# See text_search.py for which categories fall into type 3, and why some (Insolvency,
# Delisting, Busted M&A, Acquisition/Divestiture) only run keyword search against 6-K
# as a supplement (they already have an exact domestic 8-K signal; adding text search
# on top of that for 8-K too would just duplicate it with noisier false positives),
# while categories with no exact signal at all run against both 8-K and 6-K.

# Category -> list of EDGAR root_forms that map to it directly (no text analysis needed).
FORM_CATEGORY_MAP: dict[str, list[str]] = {
    "ma_merger": ["DEFM14A", "PREM14A"],
    "activist_proxy": ["DFAN14A", "PREC14A", "DEFC14A"],
    # Covers both the original SC 13D and its SC 13D/A amendments (root_forms groups
    # them) -- "Initial" is a slight simplification since amendments land here too,
    # there's no separate bucket for amendment-only activity in this taxonomy.
    "activist_initial": ["SCHEDULE 13D"],
    "tender_offer": ["SC TO-T", "SC TO-C", "SC 14D9"],
    "issuer_tender": ["SC TO-I"],
    "going_private": ["SC 13E3"],
    "going_dark": ["15-12B", "15-12G", "15-15D"],
    "spin_off": ["10-12B", "10-12G"],
    # Cross-border rights offerings / exchange offers by foreign private issuers,
    # exempt from full US registration under Rule 801/802. Low volume (~3/week
    # spot-checked) and precise. text_search.py supplements this with a "rights
    # offering" keyword pass (8-K + 6-K) to also catch domestic rights issues, which
    # have no dedicated form of their own.
    "rights_issue": ["CB"],
    "delisting": ["25-NSE"],  # 8-K item 3.01 also maps here, see EIGHT_K_ITEM_CATEGORY_MAP
}

# 8-K item code -> category. 8-Ks aren't classified by form type (there's only one
# form type, "8-K") — instead SEC's full-text search API returns an "items" array
# per hit listing the disclosed item numbers, so we classify off that directly.
# Ordered by priority: if a filing discloses multiple items, the first match wins.
#
# Deliberately NOT tracked: item 5.03 ("Amendments to Articles of Incorporation/
# Bylaws...") as a proxy for "domicile change" -- spot-checked at 56 hits/day, the
# highest volume of any item we looked at, and dominated by routine bylaw
# housekeeping (advance-notice bylaws, board-size changes, etc.) that has nothing to
# do with reincorporation. Domicile Change is instead covered purely by
# text_search.py's keyword pass (8-K + 6-K) -- lower recall, but far less noise.
EIGHT_K_ITEM_CATEGORY_MAP: dict[str, str] = {
    "1.03": "insolvency",
    "3.01": "delisting",
    # Item 2.01 covers BOTH completed acquisitions and dispositions; EDGAR's metadata
    # doesn't say which. Everything lands here by default; main.py then reclassifies
    # any hit whose text matches a divestiture keyword (text_search.py) into
    # "divestiture" instead, tagged via_text_search=True since that reclassification
    # is a keyword match, not exact.
    "2.01": "acquisition",
    "1.02": "busted_ma",
}

EIGHT_K_ITEM_PRIORITY = ["1.03", "3.01", "2.01", "1.02"]

# 13F-HR (quarterly institutional-manager holdings report) isn't in FORM_CATEGORY_MAP
# because, unlike every other tracked form, it shouldn't fire for *every* filer — over
# 400 managers file one on a given day. It only becomes a "special situation" worth
# reporting when the filer is one of the funds we specifically track (see
# tracked_funds.py); classify.py handles that filtering explicitly. 13F-NT ("notice",
# filed by managers who report through someone else) carries no holdings data, so it's
# deliberately excluded — only 13F-HR (and its /A amendments, grouped under it by
# EDGAR's root_forms) is queried.
THIRTEEN_F_FORM = "13F-HR"

# All EDGAR form labels we query for in one pass (form-based categories + 8-K + 13F-HR).
# Deliberately excludes 6-K (~250/day, no item-code metadata) -- that's queried
# separately and only via text_search.py's keyword-scoped passes.
ALL_QUERY_FORMS = sorted(
    {f for forms in FORM_CATEGORY_MAP.values() for f in forms} | {"8-K", THIRTEEN_F_FORM}
)

# Display labels + section ordering for the email digest, matching the order given.
CATEGORY_LABELS: dict[str, str] = {
    "ma_merger": "M&A / Merger",
    "acquisition": "Acquisition",
    "divestiture": "Divestiture",
    "activist_proxy": "Activist Proxy",
    "activist_initial": "Activist Initial",
    "strategic_review": "Strategic Review",
    "tender_offer": "Tender Offer",
    "issuer_tender": "Issuer Tender",
    "going_private": "Going-Private",
    "going_dark": "Going Dark",
    "spin_off": "Spin-Off",
    "capital_return": "Capital Return",
    "rights_issue": "Rights Issue",
    "restructuring": "Restructuring",
    "insolvency": "Insolvency",
    "liquidation": "Liquidation",
    "delisting": "Delisting",
    "busted_ma": "Busted M&A",
    "litigation": "Litigation",
    "domicile_change": "Domicile Change",
    "tracked_fund_13f": "Tracked Fund 13F Holdings Reports",
}

CATEGORY_ORDER = [
    "ma_merger",
    "acquisition",
    "divestiture",
    "activist_proxy",
    "activist_initial",
    "strategic_review",
    "tender_offer",
    "issuer_tender",
    "going_private",
    "going_dark",
    "spin_off",
    "capital_return",
    "rights_issue",
    "restructuring",
    "insolvency",
    "liquidation",
    "delisting",
    "busted_ma",
    "litigation",
    "domicile_change",
    "tracked_fund_13f",
]

# --- Email -----------------------------------------------------------------

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = [addr.strip() for addr in os.environ.get("EMAIL_TO", "").split(",") if addr.strip()]
