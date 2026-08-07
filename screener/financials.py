"""
Fundamentals for the dashboard's expandable rows: balance sheet / income statement /
cash flow line items sourced from each company's latest SEC 10-K (via EDGAR's XBRL
company-facts API), plus derived ratios.

Three-tier, per-FIELD waterfall (not per-company): SEC 10-K first (authoritative,
audited, as-reported), then yfinance for whatever SEC didn't tag, then `openbb` (if
installed -- optional, see requirements-optional.txt) for whatever's still missing
after both. Every field is filled independently, so a company with a 10-K that's
missing just SG&A (common -- many filers don't break it out as its own XBRL tag)
gets everything else from SEC and only SG&A backfilled, rather than falling back to
an entirely different source wholesale.

Why SEC XBRL as primary: it's the filer's own audited figures -- free, no API key,
and we already have the User-Agent infrastructure EDGAR requires
(screener/edgar_client.py). Why yfinance next: no key, broad coverage. Why openbb
last and optional: tested live against its free "yfinance" provider (no key needed)
-- it turns out to be the *same underlying Yahoo data*, just with a far more
reliable normalized column schema than yfinance's raw, inconsistently-labeled
DataFrames, so it's genuinely useful for filling gaps yfinance's row-name matching
misses, but it is not an independent data source under the free tier, and it's a
heavy dependency (pulled in ~30 provider extensions on install). Not a hard
requirement -- see fetch_fundamentals's lazy import.

Scale reconciliation (the "different units" bug): every field pulled from SEC's XBRL
is already guaranteed to be in absolute dollars -- XBRL's `val` is never scaled to
the filing's display precision, regardless of how the printed 10-K rounds numbers for
readability. yfinance/openbb are not guaranteed the same for every ticker. Once a
non-primary source is used to fill ANY gap, we first check its own revenue figure
against the primary source's revenue (the one line item essentially guaranteed
present and correctly scaled everywhere) and rescale the *entire* source by whatever
power-of-1000 factor reconciles them, so a derived figure like FCF = CFO - CapEx can
never silently combine two same-named fields at different scales. See
_reconcile_scale.

Data model: data.sec.gov/api/xbrl/companyfacts/CIK##########.json returns every
us-gaap-tagged fact a filer has ever reported, as a flat list of (period, value, form,
fy, fp) records per concept. Two kinds of fact:
  - "instant" (balance sheet: cash, debt) -- has only an 'end' date. We want the
    single most recent one, from *any* form (10-K or 10-Q) -- a balance sheet is a
    snapshot, not an annual aggregate, so the latest 10-Q is more current than a
    stale fiscal-year-end 10-K figure.
  - "duration" (income statement, cash flow: revenue, EBIT, capex, ...) -- has
    'start'+'end'. "Last completed FY" = the most recent form=="10-K", fp=="FY"
    entry. LTM (trailing twelve months) is a roll-forward: last FY total minus the
    prior-year year-to-date figure covering the same interim period, plus the
    current year-to-date figure -- the standard technique when discrete last-4-
    quarters data isn't separately available. This is a best-effort approximation:
    it can be off for companies with unusual fiscal calendars, restatements, or
    recent M&A -- the same tradeoff every free-data-source fundamentals tool makes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Concept -> ordered list of us-gaap tag names to try (first match wins). XBRL's
# taxonomy is flexible enough that filers in different industries commonly use
# different (still-standard) tags for conceptually the same line item.
DURATION_TAGS: dict[str, list[str]] = {
    # Revenue candidates are evaluated for the LARGEST value, not first-available --
    # see _extract_revenue. REITs/BDCs/banks commonly tag a minor, near-immaterial
    # RevenueFromContractWithCustomerExcludingAssessedTax (ASC 606 only covers
    # contract-with-customer revenue, which for these filers is a small fee-income
    # sliver, not their real top line of interest/lease income) *alongside* a much
    # larger Revenues or InterestAndDividendIncomeOperating tag -- first-available
    # would silently pick the tiny one and misrepresent the company's real scale.
    "revenue_fy": [
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
        "InterestAndDividendIncomeOperating",
    ],
    "cost_of_revenue_fy": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "gross_profit_fy": ["GrossProfit"],
    "sga_fy": ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"],
    "ebit_fy": ["OperatingIncomeLoss"],
    "da_fy": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "net_income_fy": ["NetIncomeLoss", "ProfitLoss"],
    "cfo_fy": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "cfi_fy": ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"],
    "capex_fy": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForCapitalImprovements"],
    "change_in_nwc_fy": ["IncreaseDecreaseInOperatingCapital"],  # frequently absent -- yfinance/openbb often fill it
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
    "debt_issued_fy": ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromNotesPayable"],
    "debt_repaid_fy": ["RepaymentsOfLongTermDebt", "RepaymentsOfNotesPayable"],
}

INSTANT_TAGS: dict[str, list[str]] = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
}

# Every leaf field a source can supply, in the order they're merged. "revenue_ltm" is
# handled separately (SEC computes a real LTM roll-forward; other sources just reuse
# their FY figure as the best available proxy -- see each _fetch_*_raw).
DOLLAR_FIELDS = [
    "revenue_fy", "gross_profit_fy", "sga_fy", "ebit_fy", "da_fy", "net_income_fy",
    "cfo_fy", "cfi_fy", "capex_fy", "change_in_nwc_fy", "debt_issued_fy", "debt_repaid_fy",
    "cash", "gross_debt",
]
ALL_RAW_FIELDS = DOLLAR_FIELDS + ["revenue_ltm", "diluted_shares"]


@dataclass
class Fundamentals:
    source: str  # "sec_10k", "yfinance", or "none" -- whichever supplied revenue (the primary anchor)
    fiscal_year_end: str | None = None

    revenue_fy: float | None = None
    revenue_ltm: float | None = None
    gross_profit_fy: float | None = None
    sga_fy: float | None = None
    ebit_fy: float | None = None
    ebitda_fy: float | None = None
    capex_fy: float | None = None
    change_in_nwc_fy: float | None = None
    fcf_fy: float | None = None
    cfo_fy: float | None = None
    cfi_fy: float | None = None
    fcfe_fy: float | None = None
    net_income_fy: float | None = None

    cash: float | None = None
    gross_debt: float | None = None

    # market-cap-dependent -- always from yfinance regardless of source, since XBRL
    # has no share price
    market_cap: float | None = None
    diluted_shares: float | None = None

    notes: list[str] = field(default_factory=list)

    # --- derived ratios, computed from whatever ended up in the fields above ---
    @property
    def gross_margin(self) -> float | None:
        return _safe_div(self.gross_profit_fy, self.revenue_fy)

    @property
    def ebit_margin(self) -> float | None:
        return _safe_div(self.ebit_fy, self.revenue_fy)

    @property
    def fcf_margin(self) -> float | None:
        return _safe_div(self.fcf_fy, self.revenue_fy)

    @property
    def fcfe_margin(self) -> float | None:
        return _safe_div(self.fcfe_fy, self.revenue_fy)

    @property
    def net_margin(self) -> float | None:
        return _safe_div(self.net_income_fy, self.revenue_fy)

    @property
    def eps(self) -> float | None:
        return _safe_div(self.net_income_fy, self.diluted_shares)

    @property
    def pe_ratio(self) -> float | None:
        return _safe_div(self.market_cap, self.net_income_fy)

    @property
    def gross_leverage(self) -> float | None:
        return _safe_div(self.gross_debt, self.ebitda_fy)

    @property
    def net_debt(self) -> float | None:
        if self.gross_debt is None or self.cash is None:
            return None
        return self.gross_debt - self.cash

    @property
    def net_cash(self) -> float | None:
        """cash - debt, i.e. -net_debt. Positive means more cash than debt."""
        net_debt = self.net_debt
        return None if net_debt is None else -net_debt

    @property
    def net_leverage(self) -> float | None:
        return _safe_div(self.net_debt, self.ebitda_fy)

    @property
    def cash_to_market_cap(self) -> float | None:
        return _safe_div(self.cash, self.market_cap)

    @property
    def net_cash_to_market_cap(self) -> float | None:
        return _safe_div(self.net_cash, self.market_cap)

    @property
    def fcfe_to_market_cap(self) -> float | None:
        return _safe_div(self.fcfe_fy, self.market_cap)


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


# --- SEC XBRL extraction ------------------------------------------------------

def _first_available(facts: dict, tags: list[str]) -> list[dict] | None:
    for tag in tags:
        concept = facts.get(tag)
        if concept and "USD" in concept.get("units", {}):
            return concept["units"]["USD"]
    return None


def _latest_instant(entries: list[dict]) -> float | None:
    if not entries:
        return None
    return max(entries, key=lambda e: e["end"])["val"]


def _latest_fy_entry(entries: list[dict]) -> dict | None:
    fy_entries = [e for e in entries if e.get("form") == "10-K" and e.get("fp") == "FY" and "start" in e]
    return max(fy_entries, key=lambda e: e["end"]) if fy_entries else None


def _dates_close(a: str, b: str, tolerance_days: int = 20) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= tolerance_days


def _latest_ytd_entry(entries: list[dict], fy_start: str) -> dict | None:
    """Most recent duration entry whose period starts at (approximately) fy_start -- i.e. the
    current in-progress fiscal year's cumulative year-to-date figure, from a 10-Q."""
    candidates = [
        e for e in entries
        if e.get("form") == "10-Q" and "start" in e and _dates_close(e["start"], fy_start)
    ]
    return max(candidates, key=lambda e: e["end"]) if candidates else None


def _ltm_value(entries: list[dict], fy_entry: dict) -> float | None:
    """last FY total - prior-year YTD-to-same-point + current YTD-to-latest-quarter."""
    latest_ytd = _latest_ytd_entry(entries, fy_entry["end"])
    if latest_ytd is None:
        return fy_entry["val"]  # no interim data yet -- FY figure is our best estimate

    target_start = (date.fromisoformat(latest_ytd["start"]) - timedelta(days=365)).isoformat()
    target_end = (date.fromisoformat(latest_ytd["end"]) - timedelta(days=365)).isoformat()
    prior_year_ytd = next(
        (
            e for e in entries
            if "start" in e and _dates_close(e["start"], target_start) and _dates_close(e["end"], target_end)
        ),
        None,
    )
    if prior_year_ytd is None:
        return fy_entry["val"]  # can't find the matching prior-year interim -- fall back to FY

    return fy_entry["val"] - prior_year_ytd["val"] + latest_ytd["val"]


def _extract_duration(facts: dict, tags: list[str]) -> tuple[float | None, float | None]:
    """Returns (fy_value, ltm_value) for a duration concept, from the first candidate
    tag (in priority order) that's actually present."""
    entries = _first_available(facts, tags)
    if not entries:
        return None, None
    fy_entry = _latest_fy_entry(entries)
    if fy_entry is None:
        return None, None
    return fy_entry["val"], _ltm_value(entries, fy_entry)


def _extract_revenue(facts: dict, tags: list[str]) -> tuple[float | None, float | None]:
    """
    Like _extract_duration, but considers EVERY candidate tag that's present rather
    than stopping at the first-available. Revenue specifically needs this:
    REITs/BDCs/banks/insurers routinely tag a near-immaterial
    RevenueFromContractWithCustomerExcludingAssessedTax (ASC 606 only covers
    contract-with-customer revenue -- a minor fee-income sliver for these filers,
    not their real interest/lease-income top line) *alongside* a much larger
    Revenues or InterestAndDividendIncomeOperating tag. First-available would
    silently pick the tiny one -- observed live for a real REIT (Acres Commercial
    Realty: $101K vs. the $79.9M Revenues tag sitting right below it in priority
    order) -- and since revenue is the anchor _reconcile_scale cross-checks every
    other source against, getting it wrong here would cascade into wrongly
    "correcting" perfectly good figures from other sources.

    Only compares tags reporting the SAME (most recent) fiscal period, not each
    tag's own latest entry -- a filer that switched tags over time (e.g. dropped
    "Revenues" for "RevenueFromContractWithCustomerExcludingAssessedTax" after
    adopting ASC 606) leaves stale, older data under the abandoned tag, and
    comparing that against the current tag's up-to-date figure by raw magnitude
    would wrongly resurrect a years-old number just because it happened to be
    bigger (observed live: a small filer's real 2025 revenue of $54,821 was nearly
    overridden by a stale, unrelated 2018 "Revenues" entry of $342,049).
    """
    candidates: list[tuple[dict, list[dict]]] = []
    for tag in tags:
        concept = facts.get(tag)
        if not concept or "USD" not in concept.get("units", {}):
            continue
        entries = concept["units"]["USD"]
        fy_entry = _latest_fy_entry(entries)
        if fy_entry is not None:
            candidates.append((fy_entry, entries))

    if not candidates:
        return None, None

    most_recent_end = max(fy_entry["end"] for fy_entry, _ in candidates)
    same_period = [
        (fy_entry, entries) for fy_entry, entries in candidates
        if _dates_close(fy_entry["end"], most_recent_end, tolerance_days=45)
    ]
    fy_entry, entries = max(same_period, key=lambda pair: pair[0]["val"])
    return fy_entry["val"], _ltm_value(entries, fy_entry)


def _extract_instant(facts: dict, tags: list[str]) -> float | None:
    entries = _first_available(facts, tags)
    return _latest_instant(entries) if entries else None


def _fetch_sec_raw(cik: int, user_agent: str) -> dict:
    """Flat dict of raw leaf fields from the filer's latest 10-K. {} if no 10-K at all
    (recent IPO, or foreign private issuer filing 20-F instead)."""
    url = COMPANYFACTS_URL.format(cik=cik)
    try:
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("SEC companyfacts fetch failed for CIK %s: %s", cik, exc)
        return {}

    facts = resp.json().get("facts", {}).get("us-gaap", {})
    if not facts:
        return {}

    revenue_fy, revenue_ltm = _extract_revenue(facts, DURATION_TAGS["revenue_fy"])
    if revenue_fy is None:
        return {}  # no 10-K income-statement data at all

    raw: dict = {"revenue_fy": revenue_fy, "revenue_ltm": revenue_ltm}
    for field_name in DOLLAR_FIELDS:
        if field_name in ("revenue_fy",):
            continue
        if field_name in DURATION_TAGS:
            raw[field_name], _ = _extract_duration(facts, DURATION_TAGS[field_name])
    raw["diluted_shares"], _ = _extract_duration(facts, DURATION_TAGS["diluted_shares"])

    if raw.get("gross_profit_fy") is None and raw.get("cost_of_revenue_fy") is not None:
        raw["gross_profit_fy"] = revenue_fy - raw["cost_of_revenue_fy"]
    raw.pop("cost_of_revenue_fy", None)  # intermediate only, not a field on Fundamentals

    cash = _extract_instant(facts, INSTANT_TAGS["cash"])
    debt_noncurrent = _extract_instant(facts, INSTANT_TAGS["debt_noncurrent"])
    debt_current = _extract_instant(facts, INSTANT_TAGS["debt_current"])
    raw["cash"] = cash
    raw["gross_debt"] = (debt_noncurrent or 0) + (debt_current or 0) if (debt_noncurrent or debt_current) else None

    return raw


# --- yfinance extraction -------------------------------------------------------

def _yf_row(df, *names) -> float | None:
    for name in names:
        if df is not None and name in df.index:
            series = df.loc[name].dropna()
            if len(series):
                return float(series.iloc[0])
    return None


def _fetch_yfinance_raw(ticker: str) -> dict:
    """Flat dict of raw leaf fields from yfinance's financial statements. {} on total failure."""
    try:
        t = yf.Ticker(ticker)
        fin, cf, bs = t.financials, t.cashflow, t.balance_sheet

        capex_fy = _yf_row(cf, "Capital Expenditure")
        if capex_fy is not None:
            capex_fy = abs(capex_fy)  # yfinance reports this negative; we treat capex as positive
        debt_repaid_fy = _yf_row(cf, "Repayment Of Debt", "Long Term Debt Payments")
        if debt_repaid_fy is not None:
            debt_repaid_fy = abs(debt_repaid_fy)

        revenue_fy = _yf_row(fin, "Total Revenue", "Operating Revenue")
        return {
            "revenue_fy": revenue_fy,
            "revenue_ltm": revenue_fy,  # best available proxy -- yfinance doesn't expose a clean LTM roll-forward
            "gross_profit_fy": _yf_row(fin, "Gross Profit"),
            "sga_fy": _yf_row(fin, "Selling General And Administration", "SG&A Expense", "General And Administrative Expense"),
            "ebit_fy": _yf_row(fin, "EBIT", "Operating Income"),
            "da_fy": _yf_row(cf, "Depreciation And Amortization", "Depreciation Amortization Depletion"),
            "net_income_fy": _yf_row(fin, "Net Income", "Net Income Common Stockholders"),
            "cfo_fy": _yf_row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
            "cfi_fy": _yf_row(cf, "Investing Cash Flow", "Cash Flow From Continuing Investing Activities"),
            "capex_fy": capex_fy,
            "change_in_nwc_fy": _yf_row(cf, "Change In Working Capital"),
            "diluted_shares": _yf_row(fin, "Diluted Average Shares"),
            "debt_issued_fy": _yf_row(cf, "Long Term Debt Issuance", "Issuance Of Long Term Debt"),
            "debt_repaid_fy": debt_repaid_fy,
            "cash": _yf_row(bs, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
            "gross_debt": _yf_row(bs, "Total Debt"),
        }
    except Exception as exc:
        logger.warning("yfinance fundamentals fetch failed for %s: %s", ticker, exc)
        return {}


# --- openbb extraction (optional third tier) ------------------------------------

_openbb_checked = False
_openbb_module = None


def _get_openbb():
    """Lazy, cached import -- openbb is an optional dependency (see module docstring:
    its free tier is Yahoo-backed, same as our yfinance tier, just more reliably
    normalized). Never raises; returns None if not installed."""
    global _openbb_checked, _openbb_module
    if not _openbb_checked:
        _openbb_checked = True
        try:
            from openbb import obb
            _openbb_module = obb
        except ImportError:
            _openbb_module = None
    return _openbb_module


def _ob_col(df, *names) -> float | None:
    for name in names:
        if df is not None and name in df.columns:
            series = df[name].dropna()
            if len(series):
                return float(series.iloc[0])
    return None


def _fetch_openbb_raw(ticker: str) -> dict:
    """Third-tier fallback for whatever SEC + yfinance both leave blank. Explicitly
    pinned to provider="yfinance" (openbb's only free, no-API-key option) -- verified
    live that its capex/CFO figures exactly match SEC's own for a known ticker, and
    that its column schema is far more reliable to parse than yfinance's raw
    DataFrames (see module docstring). Returns {} on any failure, including "not
    installed" -- this tier is opt-in, not required."""
    obb = _get_openbb()
    if obb is None:
        return {}
    try:
        income = obb.equity.fundamental.income(symbol=ticker, period="annual", limit=1, provider="yfinance").to_df()
        balance = obb.equity.fundamental.balance(symbol=ticker, period="annual", limit=1, provider="yfinance").to_df()
        cash = obb.equity.fundamental.cash(symbol=ticker, period="annual", limit=1, provider="yfinance").to_df()

        capex_fy = _ob_col(cash, "capital_expenditure")
        if capex_fy is not None:
            capex_fy = abs(capex_fy)
        debt_repaid_fy = _ob_col(cash, "long_term_debt_payments", "repayment_of_debt")
        if debt_repaid_fy is not None:
            debt_repaid_fy = abs(debt_repaid_fy)

        revenue_fy = _ob_col(income, "total_revenue", "operating_revenue")
        return {
            "revenue_fy": revenue_fy,
            "revenue_ltm": revenue_fy,
            "gross_profit_fy": _ob_col(income, "gross_profit"),
            "sga_fy": _ob_col(income, "selling_general_and_admin_expense"),
            "ebit_fy": _ob_col(income, "ebit", "operating_income"),
            "da_fy": _ob_col(cash, "depreciation_and_amortization", "depreciation_amortization_depletion"),
            "net_income_fy": _ob_col(income, "net_income"),
            "cfo_fy": _ob_col(cash, "operating_cash_flow", "cash_flow_from_continuing_operating_activities"),
            "cfi_fy": _ob_col(cash, "investing_cash_flow", "cash_flow_from_continuing_investing_activities"),
            "capex_fy": capex_fy,
            "change_in_nwc_fy": _ob_col(cash, "change_in_working_capital"),
            "diluted_shares": _ob_col(income, "weighted_average_diluted_shares_outstanding"),
            "debt_issued_fy": _ob_col(cash, "long_term_debt_issuance", "net_long_term_debt_issuance"),
            "debt_repaid_fy": debt_repaid_fy,
            "cash": _ob_col(balance, "cash_and_cash_equivalents", "cash_cash_equivalents_and_short_term_investments"),
            # openbb/Yahoo's "total_debt" can include lease liabilities that our narrower
            # SEC LongTermDebt tags don't -- only used when SEC has no debt figure at all
            # (see fetch_fundamentals's merge), never blended with a partial SEC figure.
            "gross_debt": _ob_col(balance, "total_debt"),
        }
    except Exception as exc:
        logger.warning("openbb fundamentals fetch failed for %s: %s", ticker, exc)
        return {}


# --- scale reconciliation + merge ------------------------------------------------

_SCALE_FACTORS = [1000, 1 / 1000, 1_000_000, 1 / 1_000_000, 1_000_000_000, 1 / 1_000_000_000]


def _reconcile_scale(raw: dict, primary_revenue: float | None, source_label: str) -> tuple[dict, str | None]:
    """
    Detects a systematic scale mismatch between this source and the primary source
    (e.g. this source reporting in thousands where the primary is in absolute
    dollars) by comparing revenue -- the one line item essentially guaranteed to be
    present and correctly scaled in any statement, so it's the most reliable
    cross-source anchor available. If the two revenues differ by something close to
    a clean power-of-1000 ratio (the near-universal financial-reporting scaling
    convention), every dollar-denominated field from this source is rescaled to
    match before it can be used to fill a gap -- so a merged result is never built
    by combining a same-named field from two different scales (e.g. an income-
    statement line reported in millions with a cash-flow line reported in thousands).
    """
    src_revenue = raw.get("revenue_fy")
    if not primary_revenue or not src_revenue:
        return raw, None

    ratio = primary_revenue / src_revenue
    if 0.5 <= ratio <= 2:
        return raw, None  # same scale -- ordinary revenue variance (period misalignment, etc.)

    for factor in _SCALE_FACTORS:
        if 0.7 <= ratio / factor <= 1.4:
            corrected = dict(raw)
            for f in DOLLAR_FIELDS:
                if corrected.get(f) is not None:
                    corrected[f] = corrected[f] * factor
            if corrected.get("revenue_ltm") is not None:
                corrected["revenue_ltm"] = corrected["revenue_ltm"] * factor
            note = (
                f"{source_label} figures were reported at a different scale than the primary "
                f"source (revenue ratio ~{ratio:.0f}x) -- auto-corrected {factor}x before use. "
                "Verify against the source filing if a figure on this row looks off."
            )
            return corrected, note

    return raw, (
        f"{source_label} revenue differs from the primary source by {ratio:.1f}x -- not a clean "
        f"unit-scale pattern, so left uncorrected; treat {source_label}-sourced figures on this "
        "row with caution."
    )


def _merge(*raw_dicts: dict) -> dict:
    """First non-None value per field wins, in the order the dicts are given."""
    merged: dict = {}
    for field_name in ALL_RAW_FIELDS:
        merged[field_name] = next(
            (raw[field_name] for raw in raw_dicts if raw.get(field_name) is not None), None
        )
    return merged


def fetch_fundamentals(ticker: str, cik: str, user_agent: str | None = None) -> Fundamentals:
    """
    Per-field waterfall: SEC 10-K -> yfinance -> openbb (if installed), each only
    filling gaps the previous source left. Market cap always comes from yfinance
    (XBRL has no share price) regardless of which source(s) supplied everything else.
    """
    user_agent = user_agent or os.environ.get("SEC_USER_AGENT", "")

    sec_raw: dict = {}
    try:
        if cik:
            sec_raw = _fetch_sec_raw(int(cik), user_agent)
    except (TypeError, ValueError):
        sec_raw = {}

    yf_raw = _fetch_yfinance_raw(ticker)
    primary_revenue = sec_raw.get("revenue_fy") or yf_raw.get("revenue_fy")
    source = "sec_10k" if sec_raw.get("revenue_fy") is not None else ("yfinance" if yf_raw.get("revenue_fy") is not None else "none")

    notes: list[str] = []
    if sec_raw.get("revenue_fy") is not None:
        yf_raw, note = _reconcile_scale(yf_raw, primary_revenue, "yfinance")
        if note:
            notes.append(note)

    ob_raw = _fetch_openbb_raw(ticker)
    if ob_raw:
        ob_raw, note = _reconcile_scale(ob_raw, primary_revenue, "openbb")
        if note:
            notes.append(note)

    merged = _merge(sec_raw, yf_raw, ob_raw)

    if not sec_raw:
        filled_from = "yfinance" + (" / openbb" if ob_raw else "")
        notes.append(
            f"No 10-K on file with SEC for this filer (recent IPO, or foreign issuer filing "
            f"20-F) -- figures sourced from {filled_from} instead."
        )
    else:
        gap_fields = [f for f in ("sga_fy", "ebit_fy", "cfo_fy", "capex_fy", "cash", "gross_debt") if merged.get(f) is None]
        filled_fields = [
            f for f in ("sga_fy", "ebit_fy", "cfo_fy", "capex_fy", "cash", "gross_debt")
            if sec_raw.get(f) is None and merged.get(f) is not None
        ]
        if filled_fields:
            notes.append(
                f"Not tagged in this filer's 10-K, filled in from yfinance/openbb: {', '.join(filled_fields)}."
            )
        if gap_fields:
            notes.append(f"Unavailable from any source: {', '.join(gap_fields)}.")

    if merged.get("change_in_nwc_fy") is None:
        notes.append("Change in NWC: not available from any source for this filer, left blank rather than estimated.")

    if merged.get("revenue_fy") is not None and merged["revenue_fy"] < 0:
        notes.append(
            "Revenue is negative -- likely a BDC/REIT/bank/insurer whose statements don't fit "
            "the standard commercial template used here. Treat revenue-based figures (margins, "
            "FCF margin) on this row as unreliable."
        )

    ebitda_fy = (
        merged["ebit_fy"] + merged["da_fy"] if merged["ebit_fy"] is not None and merged["da_fy"] is not None else None
    )
    fcf_fy = (
        merged["cfo_fy"] - merged["capex_fy"] if merged["cfo_fy"] is not None and merged["capex_fy"] is not None else None
    )
    net_borrowing_fy = (
        merged["debt_issued_fy"] - merged["debt_repaid_fy"]
        if merged["debt_issued_fy"] is not None and merged["debt_repaid_fy"] is not None else None
    )
    fcfe_fy = fcf_fy + net_borrowing_fy if fcf_fy is not None and net_borrowing_fy is not None else None
    if fcfe_fy is None and fcf_fy is not None:
        notes.append("FCFE: needs debt issuance/repayment cash-flow figures no source reported separately for this filer.")

    result = Fundamentals(
        source=source,
        revenue_fy=merged["revenue_fy"],
        revenue_ltm=merged["revenue_ltm"],
        gross_profit_fy=merged["gross_profit_fy"],
        sga_fy=merged["sga_fy"],
        ebit_fy=merged["ebit_fy"],
        ebitda_fy=ebitda_fy,
        capex_fy=merged["capex_fy"],
        change_in_nwc_fy=merged["change_in_nwc_fy"],
        fcf_fy=fcf_fy,
        cfo_fy=merged["cfo_fy"],
        cfi_fy=merged["cfi_fy"],
        fcfe_fy=fcfe_fy,
        net_income_fy=merged["net_income_fy"],
        cash=merged["cash"],
        gross_debt=merged["gross_debt"],
        diluted_shares=merged["diluted_shares"],
        notes=notes,
    )

    try:
        info = yf.Ticker(ticker).info
        result.market_cap = info.get("marketCap")
        if result.diluted_shares is None:
            result.diluted_shares = info.get("sharesOutstanding")
    except Exception as exc:
        logger.warning("yfinance market-cap lookup failed for %s: %s", ticker, exc)

    return result
