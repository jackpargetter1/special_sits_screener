"""
Full-text keyword search for special-situations signals that EDGAR's structured
metadata (form type, 8-K item code) can't classify exactly on its own.

Two distinct uses:

1. PRIMARY_PHRASE_CATEGORY_MAP -- categories with NO dedicated SEC form or item code
   at all (Strategic Review, Capital Return, Restructuring, Liquidation, Litigation,
   Domicile Change, plus supplementing M&A/Merger and Rights Issue, which do have an
   exact-but-narrow signal that only catches the *late* stage of a deal). Run against
   BOTH 8-K (domestic) and 6-K (foreign private issuers, which have no item codes to
   begin with) -- this is the only way to catch these at all.

2. SUPPLEMENTARY_PHRASE_CATEGORY_MAP -- categories that already have an exact,
   reliable domestic 8-K item-code signal (Insolvency=1.03, Delisting=3.01,
   Busted M&A=1.02, Acquisition=2.01). Run against 6-K ONLY, to close the FPI gap --
   6-K filers have no item code, so they'd otherwise be invisible to these categories
   entirely. Deliberately NOT run against 8-K too: that would just duplicate the exact
   signal with a noisier, keyword-based one for filers we already classify precisely.

Important: this is a fundamentally fuzzier signal than form-type or item-code
classification. A keyword match can be a false positive (e.g. a filing mentioning
"Chapter 11" only about a *past* bankruptcy already emerged from, or a risk-factor
disclaimer). Every Situation built this way carries via_text_search=True, and
digest.py renders it with a visible "TEXT MATCH" badge rather than presenting it at
the same confidence as an exact classification -- verify before acting on one.

Within each map, category order is priority order (same convention as
config.EIGHT_K_ITEM_PRIORITY): if a hit's text matches phrases from more than one
category, the first (highest-priority) category wins.
"""

from __future__ import annotations

PRIMARY_FORMS = ["8-K", "6-K"]
SUPPLEMENTARY_FORMS = ["6-K"]

# No exact form/item-code signal at all (or the exact signal only catches a late
# stage of the situation) -- keyword search is the primary/only way to catch these.
PRIMARY_PHRASE_CATEGORY_MAP: dict[str, list[str]] = {
    # DEFM14A/PREM14A (exact, config.py) only appear once a merger proxy is filed --
    # weeks/months after a deal is first announced. This catches the announcement
    # itself (definitive agreement or LOI), which is most of what "M&A / Merger" is
    # about in practice.
    "ma_merger": [
        '"definitive merger agreement"',
        '"agreement and plan of merger"',
        '"agreed to acquire"',
        '"letter of intent to acquire"',
    ],
    "strategic_review": [
        '"explore strategic alternatives"',
        '"exploring strategic alternatives"',
        '"strategic review"',
    ],
    "capital_return": [
        '"special dividend"',
        '"return of capital"',
        # Deliberately not the bare phrases "share repurchase program"/"buyback
        # program" -- spot-checked and found dominated by routine earnings-release
        # boilerplate restating an *existing* program, not a new capital-return
        # announcement. These require "new"/"authorizes"/"increase" framing instead.
        '"authorizes a new share repurchase"',
        '"approved a new share repurchase"',
        '"increases its share repurchase"',
        '"announced a new share buyback"',
    ],
    # Form CB (exact, config.py) only covers foreign-issuer cross-border offers
    # exempt under Rule 801/802 -- domestic rights offerings have no dedicated form.
    "rights_issue": [
        '"rights offering"',
        '"rights issue"',
    ],
    "restructuring": [
        '"restructuring support agreement"',
        '"debt restructuring"',
        '"covenant waiver"',
        '"amended and restated credit agreement"',
    ],
    "liquidation": [
        '"plan of liquidation"',
        '"voluntary liquidation"',
        '"plan of dissolution"',
    ],
    "litigation": [
        '"class action complaint"',
        '"material litigation"',
        '"filed a lawsuit against"',
    ],
    "domicile_change": [
        '"change its state of incorporation"',
        '"reincorporate in"',
        '"redomicile"',
    ],
}
PRIMARY_CATEGORY_PRIORITY = list(PRIMARY_PHRASE_CATEGORY_MAP)

# Already have an exact domestic 8-K signal (see config.EIGHT_K_ITEM_CATEGORY_MAP);
# this only closes the 6-K (FPI) gap for the same categories. "divestiture" is
# handled separately (see find_divestiture_hit_ids below) since it's a keyword split
# *within* the acquisition/disposition signal, not a standalone phrase category.
SUPPLEMENTARY_PHRASE_CATEGORY_MAP: dict[str, list[str]] = {
    "insolvency": [
        '"Chapter 11"',
        '"insolvency proceedings"',
        '"bankruptcy protection"',
        '"provisional liquidation"',
    ],
    "busted_ma": [
        '"termination of the merger agreement"',
        '"terminated the merger agreement"',
        '"termination of the business combination agreement"',
    ],
    "delisting": [
        '"notice of delisting"',
        '"delisting notice"',
        '"removal from listing"',
    ],
    "acquisition": [
        '"completion of the acquisition"',
        '"completed the acquisition"',
    ],
}
SUPPLEMENTARY_CATEGORY_PRIORITY = list(SUPPLEMENTARY_PHRASE_CATEGORY_MAP)

# Splits "acquisition" (8-K item 2.01, or the 6-K supplementary phrases above) into
# "divestiture" when the filing's own text indicates the *filer* sold/disposed of
# something, rather than acquired it. Run against both 8-K and 6-K.
DIVESTITURE_PHRASES = [
    '"completion of the disposition"',
    '"completed the disposition"',
    '"divested"',
    '"divestiture of"',
    '"sale of its"',
    '"disposed of its"',
]


def _run_phrase_map(client, run_date: str, forms: list[str], phrase_map: dict[str, list[str]]) -> list[tuple[dict, str]]:
    """Shared engine: one EFTS query per phrase, first-priority-match wins per hit."""
    best_category_by_id: dict[str, str] = {}
    hit_by_id: dict[str, dict] = {}

    for category, phrases in phrase_map.items():
        for phrase in phrases:
            hits = client.search_filings(forms, run_date, run_date, query=phrase)
            for hit in hits:
                hit_id = hit.get("_id")
                if not hit_id:
                    continue
                hit_by_id.setdefault(hit_id, hit)
                best_category_by_id.setdefault(hit_id, category)

    return [(hit_by_id[hid], cat) for hid, cat in best_category_by_id.items()]


def search_primary_matches(client, run_date: str) -> list[tuple[dict, str]]:
    """Categories with no exact signal at all -- run against 8-K and 6-K."""
    return _run_phrase_map(client, run_date, PRIMARY_FORMS, PRIMARY_PHRASE_CATEGORY_MAP)


def search_supplementary_6k_matches(client, run_date: str) -> list[tuple[dict, str]]:
    """Categories with an exact domestic 8-K signal already -- 6-K gap-fill only."""
    return _run_phrase_map(client, run_date, SUPPLEMENTARY_FORMS, SUPPLEMENTARY_PHRASE_CATEGORY_MAP)


def find_divestiture_hit_ids(client, run_date: str, forms: list[str]) -> set[str]:
    """
    Hit IDs (accession:filename) whose text matches a divestiture phrase, within the
    given forms/date. Used to reclassify existing "acquisition" Situations (from
    exact 8-K item 2.01 hits, or from the 6-K supplementary "acquisition" phrases
    above) into "divestiture" when the filer's own text says which direction it is.
    """
    ids: set[str] = set()
    for phrase in DIVESTITURE_PHRASES:
        for hit in client.search_filings(forms, run_date, run_date, query=phrase):
            hit_id = hit.get("_id")
            if hit_id:
                ids.add(hit_id)
    return ids
