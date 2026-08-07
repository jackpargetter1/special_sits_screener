"""Rules-based classification: raw EDGAR hit -> special-situation category (or None)."""

from __future__ import annotations

from . import config, tracked_funds


def _build_form_to_category() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for category, forms in config.FORM_CATEGORY_MAP.items():
        for f in forms:
            mapping[f] = category
    return mapping


FORM_TO_CATEGORY = _build_form_to_category()


def classify_8k(hit: dict) -> str | None:
    items = set(hit.get("items") or [])
    for code in config.EIGHT_K_ITEM_PRIORITY:
        if code in items:
            return config.EIGHT_K_ITEM_CATEGORY_MAP[code]
    return None


def classify_13f(hit: dict) -> str | None:
    """
    Unlike every other tracked form, a 13F-HR is only a "situation" worth reporting
    if it's from one of our specifically tracked funds (config comment on
    THIRTEEN_F_FORM explains why: unfiltered, ~400+ managers file one on a given day).
    """
    for raw_cik in hit.get("ciks") or []:
        try:
            cik = str(int(raw_cik))
        except (TypeError, ValueError):
            continue
        if cik in tracked_funds.TRACKED_FUND_CIKS:
            return "tracked_fund_13f"
    return None


def classify_hit(hit: dict) -> str | None:
    """Return a category string for a raw EFTS hit, or None if it's not a situation we track."""
    root_forms = hit.get("root_forms") or []
    for root_form in root_forms:
        if root_form == "8-K":
            return classify_8k(hit)
        if root_form == config.THIRTEEN_F_FORM:
            return classify_13f(hit)
        if root_form in FORM_TO_CATEGORY:
            return FORM_TO_CATEGORY[root_form]
    return None
