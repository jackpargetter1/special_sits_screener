"""
Renders the interactive HTML dashboard: every column from the email digest, plus an
expandable per-row financial detail panel (screener/financials.py) for public
companies. Self-contained single file (inline CSS/JS, no external requests) so it can
be published as a static artifact or served as-is.

This is a point-in-time snapshot, not a live page -- fundamentals require server-side
SEC/yfinance calls that can't run in a static HTML file. Regenerate by re-running
build_dashboard.py, same cadence consideration as the email digest.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from . import config
from .financials import Fundamentals
from .market_data import MarketData
from .models import Situation

_STYLE = """
:root {
  --bg: #f6f7f9;
  --surface: #ffffff;
  --surface-alt: #eef0f3;
  --border: #dfe3e8;
  --text: #14181f;
  --text-muted: #5b6472;
  --accent: #26415e;
  --accent-soft: #e8edf3;
  --positive: #0a8a3f;
  --negative: #c92a2a;
  --warning-bg: #fdf0d5;
  --warning-text: #9a5b00;
  --font-display: "Iowan Old Style", "Palatino Linotype", Georgia, "Times New Roman", serif;
  --font-body: -apple-system, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-mono: "SF Mono", "Cascadia Code", "Consolas", "Roboto Mono", monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1319; --surface: #161c24; --surface-alt: #1d242e; --border: #2a323e;
    --text: #e7ebf0; --text-muted: #8b95a3; --accent: #86aed9; --accent-soft: #1f2c3d;
    --positive: #3ecf76; --negative: #ff6b6b; --warning-bg: #3a2b12; --warning-text: #e0a94a;
  }
}
:root[data-theme="dark"] {
  --bg: #0f1319; --surface: #161c24; --surface-alt: #1d242e; --border: #2a323e;
  --text: #e7ebf0; --text-muted: #8b95a3; --accent: #86aed9; --accent-soft: #1f2c3d;
  --positive: #3ecf76; --negative: #ff6b6b; --warning-bg: #3a2b12; --warning-text: #e0a94a;
}
:root[data-theme="light"] {
  --bg: #f6f7f9; --surface: #ffffff; --surface-alt: #eef0f3; --border: #dfe3e8;
  --text: #14181f; --text-muted: #5b6472; --accent: #26415e; --accent-soft: #e8edf3;
  --positive: #0a8a3f; --negative: #c92a2a; --warning-bg: #fdf0d5; --warning-text: #9a5b00;
}

* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text); font-family: var(--font-body);
  font-size: 14px; line-height: 1.5;
}
.wrap { max-width: 1400px; margin: 0 auto; padding: 28px 24px 80px; }

header.masthead {
  display: flex; align-items: baseline; justify-content: space-between; gap: 16px;
  flex-wrap: wrap; padding-bottom: 18px; border-bottom: 2px solid var(--text);
  margin-bottom: 20px;
}
h1 {
  font-family: var(--font-display); font-size: 30px; font-weight: 600; margin: 0;
  letter-spacing: -0.01em; text-wrap: balance;
}
.masthead .meta { color: var(--text-muted); font-size: 13px; text-align: right; }

.summary-bar {
  display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px;
}
.stat-tile {
  background: var(--surface); border: 1px solid var(--border); border-radius: 6px;
  padding: 10px 16px; min-width: 108px;
}
.stat-tile .n {
  font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 22px;
  font-weight: 600; display: block;
}
.stat-tile .label { color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }

.controls {
  display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 18px;
}
#search {
  flex: 1; min-width: 200px; padding: 8px 12px; border: 1px solid var(--border);
  border-radius: 6px; background: var(--surface); color: var(--text); font-family: var(--font-body);
  font-size: 13px;
}
#search:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.chip {
  padding: 5px 12px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); color: var(--text-muted); font-size: 12px; cursor: pointer;
  font-family: var(--font-body); user-select: none;
}
.chip:hover { border-color: var(--accent); color: var(--text); }
.chip.active { background: var(--accent); border-color: var(--accent); color: var(--surface); }
.chip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

.section-label {
  font-family: var(--font-display); font-size: 15px; font-weight: 600; margin: 26px 0 8px;
  padding-top: 4px;
}
.category-group { margin-bottom: 6px; }
.category-header {
  font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase;
  letter-spacing: 0.04em; margin: 18px 0 6px; padding-bottom: 4px; border-bottom: 1px solid var(--border);
}

.table-scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: 13px; min-width: 900px; }
thead th {
  position: sticky; top: 0; background: var(--surface-alt); text-align: left; font-weight: 600;
  color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.03em;
  padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap;
}
tbody td { padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: middle; }
tbody tr.row { cursor: pointer; }
tbody tr.row:hover { background: var(--accent-soft); }
tbody tr.row.hidden, tbody tr.detail-row.hidden { display: none; }

.chevron { display: inline-block; width: 14px; color: var(--text-muted); transition: transform 0.15s; font-size: 11px; }
tr.expanded .chevron { transform: rotate(90deg); }

.ticker { font-family: var(--font-mono); font-weight: 700; }
.cik { font-family: var(--font-mono); color: var(--text-muted); font-size: 12px; }
.num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.pos { color: var(--positive); }
.neg { color: var(--negative); }
.muted { color: var(--text-muted); }

a { color: var(--accent); text-decoration: none; }
a.read-form, a.filing-page { text-decoration: underline; font-weight: 600; }
.link-sep { color: var(--border); margin: 0 4px; }

.badge {
  display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 3px;
  background: var(--warning-bg); color: var(--warning-text); font-size: 10px; font-weight: 700;
  letter-spacing: 0.03em; vertical-align: middle;
}

tr.detail-row td { padding: 0; border-bottom: 1px solid var(--border); }
.detail-panel { padding: 16px 20px 20px 40px; background: var(--surface-alt); }
.detail-source { font-size: 11px; color: var(--text-muted); margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.03em; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 18px; }
.detail-col h4 {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted);
  margin: 0 0 8px; font-weight: 600;
}
.metric-row { display: flex; justify-content: space-between; gap: 12px; padding: 3px 0; font-size: 12.5px; }
.metric-row .m-label { color: var(--text-muted); }
.metric-row .m-val { font-family: var(--font-mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.detail-notes { grid-column: 1 / -1; font-size: 11.5px; color: var(--text-muted); margin-top: 4px; border-top: 1px dashed var(--border); padding-top: 8px; }
.no-data { color: var(--text-muted); font-style: italic; padding: 4px 0; }

.footnote {
  color: var(--text-muted); font-size: 11px; margin-top: 28px; border-top: 1px solid var(--border); padding-top: 10px;
}
"""

_SCRIPT = """
function toggleRow(id) {
  const detail = document.getElementById('detail-' + id);
  const row = document.getElementById('row-' + id);
  const willShow = detail.classList.contains('hidden');
  detail.classList.toggle('hidden', !willShow);
  row.classList.toggle('expanded', willShow);
}

function applyFilters() {
  const q = document.getElementById('search').value.trim().toLowerCase();
  const activeChips = Array.from(document.querySelectorAll('.chip.active')).map(c => c.dataset.cat);
  const showAll = activeChips.length === 0 || activeChips.includes('All');

  document.querySelectorAll('tr.row').forEach(row => {
    const text = row.dataset.search || '';
    const cat = row.dataset.cat || '';
    const matchesText = !q || text.includes(q);
    const matchesCat = showAll || activeChips.includes(cat);
    const visible = matchesText && matchesCat;
    row.classList.toggle('hidden', !visible);
    const detail = document.getElementById('detail-' + row.dataset.id);
    if (detail && !visible) detail.classList.add('hidden');
  });

  document.querySelectorAll('.category-group').forEach(group => {
    const anyVisible = group.querySelectorAll('tr.row:not(.hidden)').length > 0;
    group.classList.toggle('hidden', !anyVisible);
  });
}

function toggleChip(el) {
  if (el.dataset.cat === 'All') {
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
  } else {
    document.querySelector('.chip[data-cat="All"]').classList.remove('active');
    el.classList.toggle('active');
    if (!document.querySelector('.chip.active')) {
      document.querySelector('.chip[data-cat="All"]').classList.add('active');
    }
  }
  applyFilters();
}

document.getElementById('search').addEventListener('input', applyFilters);
"""


def _fmt_usd(value: float | None) -> str:
    if value is None:
        return '<span class="muted">&mdash;</span>'
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000_000:
        return f"{sign}${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:.0f}K"
    return f"{sign}${value:,.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return '<span class="muted">&mdash;</span>'
    cls = "pos" if value >= 0 else "neg"
    return f'<span class="{cls}">{value:+.1%}</span>'


def _fmt_ratio(value: float | None, suffix: str = "x") -> str:
    if value is None:
        return '<span class="muted">&mdash;</span>'
    return f"{value:.2f}{suffix}"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return '<span class="muted">&mdash;</span>'
    return f"{value:,.0f}"


def _metric(label: str, value_html: str) -> str:
    return f'<div class="metric-row"><span class="m-label">{escape(label)}</span><span class="m-val">{value_html}</span></div>'


def _detail_panel(fund: Fundamentals | None) -> str:
    if fund is None:
        return '<div class="detail-panel"><div class="no-data">No fundamentals available for this filer.</div></div>'

    income = "".join([
        _metric("Revenue (last FY)", _fmt_usd(fund.revenue_fy)),
        _metric("Revenue (LTM)", _fmt_usd(fund.revenue_ltm)),
        _metric("Gross profit", _fmt_usd(fund.gross_profit_fy)),
        _metric("Gross margin", _fmt_pct(fund.gross_margin)),
        _metric("SG&A", _fmt_usd(fund.sga_fy)),
        _metric("EBIT", _fmt_usd(fund.ebit_fy)),
        _metric("EBIT margin", _fmt_pct(fund.ebit_margin)),
        _metric("EBITDA", _fmt_usd(fund.ebitda_fy)),
        _metric("Net income", _fmt_usd(fund.net_income_fy)),
        _metric("Net margin", _fmt_pct(fund.net_margin)),
    ])
    cashflow = "".join([
        _metric("Cash flow from ops", _fmt_usd(fund.cfo_fy)),
        _metric("Cash flow from investing", _fmt_usd(fund.cfi_fy)),
        _metric("CapEx", _fmt_usd(fund.capex_fy)),
        _metric("Change in NWC", _fmt_usd(fund.change_in_nwc_fy)),
        _metric("FCF", _fmt_usd(fund.fcf_fy)),
        _metric("FCF margin", _fmt_pct(fund.fcf_margin)),
        _metric("FCFE", _fmt_usd(fund.fcfe_fy)),
        _metric("FCFE margin", _fmt_pct(fund.fcfe_margin)),
        _metric("FCFE / market cap", _fmt_pct(fund.fcfe_to_market_cap)),
    ])
    balance = "".join([
        _metric("Cash", _fmt_usd(fund.cash)),
        _metric("Gross debt", _fmt_usd(fund.gross_debt)),
        _metric("Net debt", _fmt_usd(fund.net_debt)),
        _metric("Gross debt / EBITDA", _fmt_ratio(fund.gross_leverage)),
        _metric("Net debt / EBITDA", _fmt_ratio(fund.net_leverage)),
        _metric("Cash / market cap", _fmt_pct(fund.cash_to_market_cap)),
        _metric("Net cash / market cap", _fmt_pct(fund.net_cash_to_market_cap)),
    ])
    valuation = "".join([
        _metric("Market cap", _fmt_usd(fund.market_cap)),
        _metric("Diluted shares out.", _fmt_num(fund.diluted_shares)),
        _metric("Net income / share (EPS)", f"${fund.eps:.2f}" if fund.eps is not None else '<span class="muted">&mdash;</span>'),
        _metric("P/E ratio", _fmt_ratio(fund.pe_ratio, suffix="x") if fund.pe_ratio else '<span class="muted">&mdash;</span>'),
    ])

    notes_html = ""
    if fund.notes:
        notes_html = '<div class="detail-notes">' + " &middot; ".join(escape(n) for n in fund.notes) + "</div>"

    source_label = "SEC 10-K" if fund.source == "sec_10k" else "yfinance (no 10-K available)"
    return f"""<div class="detail-panel">
      <div class="detail-source">Source: {escape(source_label)}</div>
      <div class="detail-grid">
        <div class="detail-col"><h4>Income Statement</h4>{income}</div>
        <div class="detail-col"><h4>Cash Flow</h4>{cashflow}</div>
        <div class="detail-col"><h4>Balance Sheet &amp; Leverage</h4>{balance}</div>
        <div class="detail-col"><h4>Valuation</h4>{valuation}</div>
        {notes_html}
      </div>
    </div>"""


def _situation_rows(
    situations: list[Situation],
    market_data: dict[str, MarketData],
    fundamentals: dict[str, Fundamentals],
    show_market_cols: bool,
    row_counter: list[int],
) -> str:
    rows = []
    for s in situations:
        row_counter[0] += 1
        row_id = row_counter[0]

        if s.ticker:
            id_cell = f'<span class="ticker">{escape(s.ticker)}</span>'
        else:
            id_cell = f'<span class="cik">CIK {escape(s.cik)}</span>' if s.cik else "&mdash;"

        items = ", ".join(s.items) if s.items else ""
        badge = ' <span class="badge" title="Keyword match, not exact classification">TEXT MATCH</span>' if s.via_text_search else ""
        form_text = escape(s.form) + (f" ({escape(items)})" if items else "") + badge

        link_parts = []
        if s.read_form_link:
            link_parts.append(f'<a class="read-form" href="{escape(s.read_form_link)}" target="_blank" rel="noopener">Read Form</a>')
        if s.more_info_link:
            link_parts.append(f'<a class="filing-page" href="{escape(s.more_info_link)}" target="_blank" rel="noopener">SEC Filing Page</a>')
        links_cell = '<span class="link-sep">|</span>'.join(link_parts) if link_parts else "&mdash;"

        market_cells = ""
        if show_market_cols:
            md = (s.ticker and market_data.get(s.ticker)) or None
            day_pct = _fmt_pct(md.day_change_pct / 100 if md and md.day_change_pct is not None else None)
            mcap = _fmt_usd(md.market_cap if md else None)
            industry = escape(md.industry) if md and md.industry else '<span class="muted">&mdash;</span>'
            market_cells = f'<td class="num">{day_pct}</td><td class="num">{mcap}</td><td>{industry}</td>'

        search_blob = f"{s.ticker or ''} {s.company} {s.cik}".lower()
        fund = fundamentals.get(s.ticker) if s.ticker else None
        expand_cell = f'<span class="chevron">&#9656;</span>' if fund is not None else ""

        row_click = f' onclick="toggleRow({row_id})"' if fund is not None else ""
        row_style = "" if fund is not None else ' style="cursor:default"'

        rows.append(
            f'<tr class="row" id="row-{row_id}" data-id="{row_id}" data-cat="{escape(s.category)}" '
            f'data-search="{escape(search_blob)}"{row_click}{row_style}>'
            f"<td>{expand_cell}</td>"
            f"<td>{id_cell}</td>"
            f"<td>{escape(s.company)}</td>"
            f'<td class="muted">{form_text}</td>'
            f"<td>{links_cell}</td>"
            f"{market_cells}"
            "</tr>"
        )
        rows.append(
            f'<tr class="detail-row hidden" id="detail-{row_id}"><td colspan="{8 if show_market_cols else 5}">'
            f"{_detail_panel(fund)}</td></tr>"
        )
    return "\n".join(rows)


def _render_group(
    situations_by_category: dict[str, list[Situation]],
    market_data: dict[str, MarketData],
    fundamentals: dict[str, Fundamentals],
    show_market_cols: bool,
    id_header: str,
    row_counter: list[int],
) -> str:
    sections = []
    header_cells = [
        "<th></th>", f"<th>{id_header}</th>", "<th>Company</th>", "<th>Form</th>", "<th>Links</th>",
    ]
    if show_market_cols:
        header_cells += ["<th>Day %</th>", "<th>Market Cap</th>", "<th>Industry</th>"]

    for category in config.CATEGORY_ORDER:
        situations = situations_by_category.get(category)
        if not situations:
            continue
        label = config.CATEGORY_LABELS.get(category, category)
        rows = _situation_rows(situations, market_data, fundamentals, show_market_cols, row_counter)
        sections.append(
            f'<div class="category-group">'
            f'<div class="category-header">{escape(label)} ({len(situations)})</div>'
            '<div class="table-scroll"><table>'
            f"<thead><tr>{''.join(header_cells)}</tr></thead><tbody>{rows}</tbody>"
            "</table></div></div>"
        )
    return "\n".join(sections)


def _group_by_category(situations: list[Situation]) -> dict[str, list[Situation]]:
    grouped: dict[str, list[Situation]] = {}
    for s in situations:
        grouped.setdefault(s.category, []).append(s)
    return grouped


def build_html_dashboard(
    situations_by_category: dict[str, list[Situation]],
    run_date: str,
    market_data: dict[str, MarketData],
    fundamentals: dict[str, Fundamentals],
    auto_refresh_seconds: int | None = None,
) -> str:
    """
    auto_refresh_seconds: if set, the page reloads itself on that interval (via
    <meta http-equiv="refresh">) -- meaningful once the file is hosted somewhere that
    re-publishes on a matching cron (see build_dashboard.py's DASHBOARD_GCS_BUCKET),
    so a browser tab left open keeps picking up each refresh automatically. Harmless
    but pointless for a one-off static snapshot (there's nothing new to reload).
    """
    all_situations = [s for situations in situations_by_category.values() for s in situations]
    total = len(all_situations)
    public = [s for s in all_situations if s.is_public]
    private = [s for s in all_situations if not s.is_public]

    categories_present = sorted({s.category for s in all_situations}, key=lambda c: config.CATEGORY_ORDER.index(c) if c in config.CATEGORY_ORDER else 999)
    chips = ['<button class="chip active" data-cat="All" onclick="toggleChip(this)">All</button>']
    for cat in categories_present:
        label = config.CATEGORY_LABELS.get(cat, cat)
        n = len(situations_by_category.get(cat, []))
        chips.append(f'<button class="chip" data-cat="{escape(cat)}" onclick="toggleChip(this)">{escape(label)} ({n})</button>')

    row_counter = [0]
    body_parts = []
    if public:
        body_parts.append(f'<div class="section-label">Public Companies ({len(public)})</div>')
        body_parts.append(_render_group(_group_by_category(public), market_data, fundamentals, True, "Ticker", row_counter))
    if private:
        body_parts.append(f'<div class="section-label">Private Companies ({len(private)})</div>')
        body_parts.append(_render_group(_group_by_category(private), market_data, {}, False, "CIK", row_counter))

    body = "\n".join(body_parts) if body_parts else "<p>No situations matched today.</p>"

    footnote = ""
    if any(s.via_text_search for s in all_situations):
        footnote = (
            '<div class="footnote">'
            '<span class="badge">TEXT MATCH</span> rows were found by keyword search, not exact form/item '
            "classification &mdash; treat these as leads to verify. Financial detail panels are sourced "
            "from each filer's latest SEC 10-K where available, falling back to yfinance otherwise "
            "(flagged in each panel's source line) &mdash; both are best-effort and can be incomplete "
            "or, for BDCs/REITs/banks/insurers whose statements don't fit a standard commercial template, "
            "unreliable (noted inline when detected)."
            "</div>"
        )

    refresh_tag = f'<meta http-equiv="refresh" content="{auto_refresh_seconds}">' if auto_refresh_seconds else ""
    generated_at = datetime.now(timezone.utc).strftime("%H:%M UTC")
    refresh_note = f" &middot; refreshes every {auto_refresh_seconds // 60} min" if auto_refresh_seconds else ""

    return f"""<title>SpecialSits Screener — Dashboard</title>
{refresh_tag}
<style>{_STYLE}</style>
<div class="wrap">
  <header class="masthead">
    <h1>SpecialSits Screener</h1>
    <div class="meta">{escape(run_date)}<br>{total} situation{"s" if total != 1 else ""} across EDGAR
      <br><span class="muted">generated {generated_at}{refresh_note}</span></div>
  </header>

  <div class="summary-bar">
    <div class="stat-tile"><span class="n">{total}</span><span class="label">Total</span></div>
    <div class="stat-tile"><span class="n">{len(public)}</span><span class="label">Public</span></div>
    <div class="stat-tile"><span class="n">{len(private)}</span><span class="label">Private</span></div>
    <div class="stat-tile"><span class="n">{len(categories_present)}</span><span class="label">Categories</span></div>
  </div>

  <div class="controls">
    <input id="search" type="text" placeholder="Filter by ticker, company, or CIK…">
    {"".join(chips)}
  </div>

  {body}
  {footnote}
</div>
<script>{_SCRIPT}</script>"""
