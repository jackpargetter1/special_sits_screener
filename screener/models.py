"""Data model + helpers for turning a raw EDGAR full-text search hit into a Situation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# display_names look like: "AstroNova, Inc.  (ALOT)  (CIK 0000008146)". A filer with
# more than one listed security (common stock + warrants/rights/preferred) lists all
# of them comma-separated instead, e.g.
# "Core Scientific, Inc./tx  (CORZ, CORZR, CORZW, CORZZ)  (CIK 0001839341)" — the
# pattern below must match that whole list so we don't mistake a public company that
# happens to have >1 ticker for a private one (extract_company_and_ticker takes the
# first, which is consistently the common-stock listing).
# Ticker is optional (some filers, e.g. foreign private issuers or shell CIKs, have none).
_TICKER_RE = re.compile(
    r"\(([A-Z0-9]{1,8}(?:[.\-][A-Z0-9]{1,4})?(?:,\s*[A-Z0-9]{1,8}(?:[.\-][A-Z0-9]{1,4})?)*)\)\s*\(CIK"
)


@dataclass
class Situation:
    category: str
    form: str
    company: str
    ticker: str | None
    cik: str
    filed_date: str
    accession: str
    read_form_link: str | None  # direct link to the human-readable primary document
    more_info_link: str | None  # link to the filing's index page (all documents + metadata)
    items: list[str] = field(default_factory=list)
    # True if this category was assigned by text_search.py's keyword matching (6-K
    # filings only) rather than exact form-type/item-code classification -- a
    # meaningfully fuzzier signal; digest.py flags these visibly.
    via_text_search: bool = False

    @property
    def is_public(self) -> bool:
        """A filer counts as public here if EDGAR's full-text search resolved a ticker for it."""
        return bool(self.ticker)


def extract_company_and_ticker(display_names: list[str]) -> tuple[str, str | None]:
    if not display_names:
        return "Unknown filer", None
    raw = display_names[0]
    ticker_match = _TICKER_RE.search(raw)
    # First entry in a comma-separated list is consistently the common-stock ticker;
    # the rest (if any) are the filer's other listed securities (warrants, rights, etc.).
    ticker = ticker_match.group(1).split(",")[0].strip() if ticker_match else None
    # Strip the "(TICKER[, TICKER, ...]) (CIK ...)" suffix to get a clean company name.
    company = re.sub(r"\s*\(CIK\s*\d+\)\s*$", "", raw)
    company = re.sub(r"\s*\([A-Z0-9.\-]{1,8}(?:,\s*[A-Z0-9.\-]{1,8})*\)\s*$", "", company).strip()
    return company or raw, ticker


def _filing_base_url(hit: dict) -> tuple[str, str, str] | None:
    """Shared groundwork for both link builders: (base_dir_url, accession, filename), or None."""
    ciks = hit.get("ciks") or []
    hit_id = hit.get("_id", "")
    if not ciks or ":" not in hit_id:
        return None
    accession, filename = hit_id.split(":", 1)
    accession_nodash = accession.replace("-", "")
    try:
        cik_int = str(int(ciks[0]))
    except (TypeError, ValueError):
        return None
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}"
    return base, accession, filename


def build_read_form_link(hit: dict) -> str | None:
    """
    Direct link to the primary document -- readable as-is for the vast majority of
    forms (plain HTML). A handful of forms (Schedule 13D, 13F-HR) use a structured
    XML primary document instead, which renders as an unreadable wall of tags; for
    those, EDGAR's full-text search hit itself tells us the name of the auto-generated
    XSLT-rendered folder via hit["xsl"] (e.g. "xslSCHEDULE_13D_X02"), so we just need
    to nest the same filename one level deeper under it -- no extra request needed,
    verified live against SEC's own index pages for both SCHEDULE 13D and 13F-HR.
    """
    parts = _filing_base_url(hit)
    if parts is None:
        return None
    base, _accession, filename = parts
    xsl_folder = hit.get("xsl")
    if xsl_folder:
        return f"{base}/{xsl_folder}/{filename}"
    return f"{base}/{filename}"


def build_more_info_link(hit: dict) -> str | None:
    """
    Link to the filing's index page -- SEC's standard "filing detail" page listing
    every document in the filing plus filer/date metadata. Verified against SEC's own
    filing-href output for the equivalent lookup via the browse-edgar company-search
    endpoint.
    """
    parts = _filing_base_url(hit)
    if parts is None:
        return None
    base, accession, _filename = parts
    return f"{base}/{accession}-index.htm"


def build_situation(hit: dict, category: str, via_text_search: bool = False) -> Situation:
    company, ticker = extract_company_and_ticker(hit.get("display_names") or [])
    ciks = hit.get("ciks") or [""]
    return Situation(
        category=category,
        form=hit.get("form", ""),
        company=company,
        ticker=ticker,
        cik=ciks[0],
        via_text_search=via_text_search,
        filed_date=hit.get("file_date", ""),
        accession=hit.get("adsh", ""),
        read_form_link=build_read_form_link(hit),
        more_info_link=build_more_info_link(hit),
        items=hit.get("items") or [],
    )


def dedupe_hits(hits: list[dict]) -> list[dict]:
    """
    Drop raw EFTS hits we've already seen. Keyed on hit["_id"] (accession:filename),
    the one field guaranteed to identify a specific filing document — cheap insurance
    against duplicate rows if a paginated search ever returns overlapping pages.
    """
    seen: set[str] = set()
    deduped: list[dict] = []
    for hit in hits:
        hit_id = hit.get("_id")
        if hit_id:
            if hit_id in seen:
                continue
            seen.add(hit_id)
        deduped.append(hit)
    return deduped


def dedupe_situations(situations: list[Situation]) -> list[Situation]:
    """
    Drop duplicate Situations, keyed on (cik, accession, category). Same idea as
    dedupe_hits but applied post-classification, in case a filing legitimately
    surfaces as more than one hit for the same category (e.g. a combined filing
    that matches two of our tracked form labels).
    """
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Situation] = []
    for s in situations:
        key = (s.cik, s.accession, s.category)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped


# Legal-entity suffixes stripped when comparing company names, so "Foo Corp",
# "Foo Corporation" and "Foo, Inc." all normalize to the same identity key.
_LEGAL_SUFFIX_RE = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|co|ltd|limited|llc|lp|plc|holdings?|group)\b\.?",
    re.IGNORECASE,
)


def _identity_key(s: Situation) -> str:
    if s.ticker:
        return f"ticker:{s.ticker.strip().upper()}"
    name = s.company.lower()
    name = re.sub(r"[.,]", " ", name)
    name = _LEGAL_SUFFIX_RE.sub("", name)
    name = re.sub(r"[^a-z0-9]+", "", name)
    return f"name:{name}" if name else f"cik:{s.cik}"


def collapse_duplicate_filers(situations: list[Situation], category_priority: list[str]) -> list[Situation]:
    """
    Collapse to one row per unique filer, matched on ticker (or normalized company
    name when there's no ticker, so "Foo Corp" and "Foo, Inc." still collide).

    dedupe_situations only catches an exact repeat of the same accession — it won't
    catch the same company legitimately producing more than one *different* filing/
    accession on one day (e.g. a combined tender-offer + going-private filing, or a
    13D followed same-day by a 13D/A), which is what shows up as "duplicate" rows in
    the digest even though each is technically a distinct filing. When a filer has
    more than one situation, keep the one in the highest-priority category (per
    category_priority, most-actionable-first), breaking ties by most-recently-filed.
    """
    rank = {category: i for i, category in enumerate(category_priority)}
    best: dict[str, Situation] = {}
    for s in situations:
        key = _identity_key(s)
        current = best.get(key)
        if current is None:
            best[key] = s
            continue
        current_rank = rank.get(current.category, len(rank))
        new_rank = rank.get(s.category, len(rank))
        if new_rank < current_rank or (new_rank == current_rank and s.filed_date > current.filed_date):
            best[key] = s
    return list(best.values())
