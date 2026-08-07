"""
Fundamentals for the dashboard's expandable rows: balance sheet / income statement /
cash flow line items sourced from each company's latest SEC 10-K (via EDGAR's XBRL
company-facts API), plus derived ratios, with a yfinance fallback for anything SEC
data doesn't have.

Why SEC XBRL as primary source, not yfinance/openbb: it's the filer's own audited,
as-reported figures -- free, no API key, and we already have the User-Agent
infrastructure EDGAR requires (screener/edgar_client.py). yfinance's fundamentals
are frequently incomplete or delayed for smaller-cap names, which is exactly the
population a special-situations screener cares most about. We didn't add `openbb`:
its free-tier fundamentals ultimately come from the same public sources (SEC/Yahoo)
this module already covers directly, and it's a heavy dependency (pandas, numpy, many
provider SDKs) for no net new coverage here.

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
    recent M&A: it's the same tradeoff every free-data-source fundamentals tool
    makes, not a bug specific to this implementation.

Companies with no 10-K at all (recent IPOs, foreign private issuers filing 20-F
instead) get every SEC-sourced field as None; the dashboard falls back to yfinance
for those (see fetch_fundamentals's fallback branch).
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
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "cost_of_revenue": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "sga": ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"],
    "ebit": ["OperatingIncomeLoss"],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "cfi": ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForCapitalImprovements"],
    "change_in_nwc": ["IncreaseDecreaseInOperatingCapital"],  # frequently absent -- see module docstring
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
    "debt_issued": ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromNotesPayable"],
    "debt_repaid": ["RepaymentsOfLongTermDebt", "RepaymentsOfNotesPayable"],
}

INSTANT_TAGS: dict[str, list[str]] = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_current": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
}


@dataclass
class Fundamentals:
    source: str  # "sec_10k" or "yfinance" (whichever actually supplied most fields)
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

    # --- derived ratios, computed once the raw fields above are populated ---
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


def _first_available(facts: dict, tags: list[str]) -> dict | None:
    for tag in tags:
        concept = facts.get(tag)
        if concept and "USD" in concept.get("units", {}):
            return concept["units"]["USD"]
    return None


def _latest_instant(entries: list[dict]) -> float | None:
    if not entries:
        return None
    best = max(entries, key=lambda e: e["end"])
    return best["val"]


def _latest_fy_entry(entries: list[dict]) -> dict | None:
    fy_entries = [e for e in entries if e.get("form") == "10-K" and e.get("fp") == "FY" and "start" in e]
    if not fy_entries:
        return None
    return max(fy_entries, key=lambda e: e["end"])


def _dates_close(a: str, b: str, tolerance_days: int = 20) -> bool:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= tolerance_days


def _latest_ytd_entry(entries: list[dict], fy_start: str) -> dict | None:
    """Most recent duration entry whose period starts at (approximately) fy_start -- i.e. the
    current in-progress fiscal year's cumulative year-to-date figure, from a 10-Q."""
    candidates = [
        e for e in entries
        if e.get("form") == "10-Q" and "start" in e and _dates_close(e["start"], fy_start)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e["end"])


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


def _extract_duration(facts: dict, concept: str) -> tuple[float | None, float | None]:
    """Returns (fy_value, ltm_value) for a duration concept."""
    entries = _first_available(facts, DURATION_TAGS[concept])
    if not entries:
        return None, None
    fy_entry = _latest_fy_entry(entries)
    if fy_entry is None:
        return None, None
    return fy_entry["val"], _ltm_value(entries, fy_entry)


def _extract_instant(facts: dict, concept: str) -> float | None:
    entries = _first_available(facts, INSTANT_TAGS[concept])
    return _latest_instant(entries) if entries else None


def _fetch_sec_fundamentals(cik: int, user_agent: str) -> Fundamentals | None:
    url = COMPANYFACTS_URL.format(cik=cik)
    try:
        resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
        if resp.status_code == 404:
            return None  # no XBRL facts at all for this CIK (e.g. FPI filing 20-F, not 10-K)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("SEC companyfacts fetch failed for CIK %s: %s", cik, exc)
        return None

    facts = resp.json().get("facts", {}).get("us-gaap", {})
    if not facts:
        return None

    revenue_fy, revenue_ltm = _extract_duration(facts, "revenue")
    if revenue_fy is None:
        return None  # no 10-K income-statement data at all -- caller falls back to yfinance

    gross_profit_fy, _ = _extract_duration(facts, "gross_profit")
    cost_of_revenue_fy, _ = _extract_duration(facts, "cost_of_revenue")
    if gross_profit_fy is None and cost_of_revenue_fy is not None:
        gross_profit_fy = revenue_fy - cost_of_revenue_fy

    sga_fy, _ = _extract_duration(facts, "sga")
    ebit_fy, _ = _extract_duration(facts, "ebit")
    da_fy, _ = _extract_duration(facts, "depreciation_amortization")
    net_income_fy, _ = _extract_duration(facts, "net_income")
    cfo_fy, _ = _extract_duration(facts, "cfo")
    cfi_fy, _ = _extract_duration(facts, "cfi")
    capex_fy, _ = _extract_duration(facts, "capex")
    change_in_nwc_fy, _ = _extract_duration(facts, "change_in_nwc")
    diluted_shares, _ = _extract_duration(facts, "diluted_shares")
    debt_issued_fy, _ = _extract_duration(facts, "debt_issued")
    debt_repaid_fy, _ = _extract_duration(facts, "debt_repaid")

    ebitda_fy = ebit_fy + da_fy if ebit_fy is not None and da_fy is not None else None
    fcf_fy = cfo_fy - capex_fy if cfo_fy is not None and capex_fy is not None else None
    net_borrowing_fy = (
        debt_issued_fy - debt_repaid_fy if debt_issued_fy is not None and debt_repaid_fy is not None else None
    )
    fcfe_fy = fcf_fy + net_borrowing_fy if fcf_fy is not None and net_borrowing_fy is not None else None

    cash = _extract_instant(facts, "cash")
    debt_noncurrent = _extract_instant(facts, "debt_noncurrent")
    debt_current = _extract_instant(facts, "debt_current")
    gross_debt = None
    if debt_noncurrent is not None or debt_current is not None:
        gross_debt = (debt_noncurrent or 0) + (debt_current or 0)

    notes = []
    if change_in_nwc_fy is None:
        notes.append("Change in NWC: not separately tagged by this filer, left blank rather than estimated.")
    if fcfe_fy is None:
        notes.append("FCFE: needs debt issuance/repayment cash-flow tags this filer didn't report separately.")

    return Fundamentals(
        source="sec_10k",
        revenue_fy=revenue_fy,
        revenue_ltm=revenue_ltm,
        gross_profit_fy=gross_profit_fy,
        sga_fy=sga_fy,
        ebit_fy=ebit_fy,
        ebitda_fy=ebitda_fy,
        capex_fy=capex_fy,
        change_in_nwc_fy=change_in_nwc_fy,
        fcf_fy=fcf_fy,
        cfo_fy=cfo_fy,
        cfi_fy=cfi_fy,
        fcfe_fy=fcfe_fy,
        net_income_fy=net_income_fy,
        cash=cash,
        gross_debt=gross_debt,
        diluted_shares=diluted_shares,
        notes=notes,
    )


def _fetch_yfinance_fundamentals(ticker: str) -> Fundamentals:
    """Fallback when SEC has no 10-K data (recent IPO, FPI filing 20-F, etc.)."""
    notes = ["Sourced from yfinance -- no 10-K available from SEC for this filer."]
    try:
        t = yf.Ticker(ticker)
        fin = t.financials  # annual income statement, columns = fiscal years, most recent first
        cf = t.cashflow
        bs = t.balance_sheet

        def _row(df, *names):
            for name in names:
                if df is not None and name in df.index:
                    series = df.loc[name].dropna()
                    if len(series):
                        return float(series.iloc[0])
            return None

        revenue_fy = _row(fin, "Total Revenue", "Operating Revenue")
        gross_profit_fy = _row(fin, "Gross Profit")
        sga_fy = _row(fin, "Selling General And Administration", "SG&A Expense")
        ebit_fy = _row(fin, "EBIT", "Operating Income")
        ebitda_fy = _row(fin, "EBITDA")
        net_income_fy = _row(fin, "Net Income", "Net Income Common Stockholders")
        cfo_fy = _row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
        cfi_fy = _row(cf, "Investing Cash Flow", "Cash Flow From Continuing Investing Activities")
        capex_fy = _row(cf, "Capital Expenditure")
        if capex_fy is not None:
            capex_fy = abs(capex_fy)  # yfinance reports this negative; we treat capex as positive
        cash = _row(bs, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
        gross_debt = _row(bs, "Total Debt")
        diluted_shares = _row(fin, "Diluted Average Shares")

        fcf_fy = cfo_fy - capex_fy if cfo_fy is not None and capex_fy is not None else None
        if ebitda_fy is None and ebit_fy is not None:
            da_fy = _row(cf, "Depreciation And Amortization")
            if da_fy is not None:
                ebitda_fy = ebit_fy + da_fy

        # BDCs, REITs, banks, and insurers don't file a standard commercial income
        # statement -- yfinance's generic "Total Revenue" row can end up mapped to
        # something like net investment income/(loss), which goes negative in a bad
        # quarter. A negative revenue is never right for an operating company, so
        # flag it rather than silently showing a nonsensical margin.
        if revenue_fy is not None and revenue_fy < 0:
            notes.append(
                "Revenue is negative -- likely a BDC/REIT/bank/insurer whose statements don't fit "
                "the standard commercial template this fallback uses. Treat revenue-based figures "
                "(margins, FCF margin) on this row as unreliable."
            )

        return Fundamentals(
            source="yfinance",
            revenue_fy=revenue_fy,
            revenue_ltm=revenue_fy,  # yfinance annual figure is our best available proxy for LTM here
            gross_profit_fy=gross_profit_fy,
            sga_fy=sga_fy,
            ebit_fy=ebit_fy,
            ebitda_fy=ebitda_fy,
            capex_fy=capex_fy,
            fcf_fy=fcf_fy,
            cfo_fy=cfo_fy,
            cfi_fy=cfi_fy,
            net_income_fy=net_income_fy,
            cash=cash,
            gross_debt=gross_debt,
            diluted_shares=diluted_shares,
            notes=notes,
        )
    except Exception as exc:
        logger.warning("yfinance fundamentals fallback failed for %s: %s", ticker, exc)
        return Fundamentals(source="yfinance", notes=[f"No fundamentals data available: {exc}"])


def fetch_fundamentals(ticker: str, cik: str, user_agent: str | None = None) -> Fundamentals:
    """
    SEC 10-K first, yfinance fallback if SEC has no usable data. Market cap always
    comes from yfinance (XBRL has no share price) and is merged in either way.
    """
    user_agent = user_agent or os.environ.get("SEC_USER_AGENT", "")
    result = None
    try:
        result = _fetch_sec_fundamentals(int(cik), user_agent) if cik else None
    except (TypeError, ValueError):
        result = None

    if result is None:
        result = _fetch_yfinance_fundamentals(ticker)

    try:
        info = yf.Ticker(ticker).info
        result.market_cap = info.get("marketCap")
        if result.diluted_shares is None:
            result.diluted_shares = info.get("sharesOutstanding")
    except Exception as exc:
        logger.warning("yfinance market-cap lookup failed for %s: %s", ticker, exc)

    return result
