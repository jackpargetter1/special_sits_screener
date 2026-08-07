"""Builds the HTML email digest from classified situations."""

from __future__ import annotations

from html import escape

from . import config
from .market_data import MarketData
from .models import Situation

_STYLE = """
  body { font-family: -apple-system, Helvetica, Arial, sans-serif; color: #1a1a1a; }
  h1 { font-size: 18px; margin-bottom: 4px; }
  .subtitle { color: #666; font-size: 13px; margin-bottom: 20px; }
  h2 { font-size: 15px; margin-top: 28px; margin-bottom: 6px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
  h3 { font-size: 13px; margin-top: 18px; margin-bottom: 6px; color: #444; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  td, th { text-align: left; padding: 5px 8px; border-bottom: 1px solid #eee; vertical-align: top; }
  th { color: #666; font-weight: 600; }
  .ticker { font-weight: 600; }
  .form { color: #666; font-family: monospace; font-size: 12px; }
  .section-label { color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; margin-top: 32px; }
  a { color: #0b5fff; text-decoration: none; }
  a.read-form, a.filing-page { color: #0b5fff; text-decoration: underline; font-weight: 600; }
  .link-sep { color: #ccc; margin: 0 4px; }
  .text-match-badge {
    display: inline-block; margin-left: 6px; padding: 1px 5px; border-radius: 3px;
    background: #fdf0d5; color: #9a5b00; font-size: 10px; font-weight: 700;
    letter-spacing: 0.03em; vertical-align: middle;
  }
  .footnote { color: #888; font-size: 11px; margin-top: 24px; border-top: 1px solid #eee; padding-top: 8px; }
"""


def _format_market_cap(value: float | None) -> str:
    if value is None:
        return "&mdash;"
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    return f"${value:,.0f}"


def _format_day_change(value: float | None) -> str:
    if value is None:
        return "&mdash;"
    color = "#0a8a3f" if value >= 0 else "#c92a2a"
    sign = "+" if value >= 0 else ""
    return f'<span style="color:{color}">{sign}{value:.2f}%</span>'


def _situation_row(s: Situation, market_data: dict[str, MarketData] | None = None) -> str:
    # Public rows show the ticker; private filers never have one, so show CIK instead —
    # otherwise two unrelated private funds with similar names (e.g. "BlackRock Private
    # Credit Fund" vs "Blackstone Private Credit Fund") both render an empty first
    # column and read as duplicate rows even though they're different filers.
    if s.ticker:
        id_cell = f'<span class="ticker">{escape(s.ticker)}</span>'
    else:
        id_cell = f'<span class="cik">CIK {escape(s.cik)}</span>' if s.cik else "&mdash;"
    items = ", ".join(s.items) if s.items else ""
    badge = (
        ' <span class="text-match-badge" title="Keyword match, not exact classification — verify before acting">'
        "TEXT MATCH</span>"
        if s.via_text_search
        else ""
    )
    form_text = escape(s.form) + (f" ({escape(items)})" if items else "") + badge
    link_parts = []
    if s.read_form_link:
        link_parts.append(f'<a class="read-form" href="{escape(s.read_form_link)}">Read Form</a>')
    if s.more_info_link:
        link_parts.append(f'<a class="filing-page" href="{escape(s.more_info_link)}">SEC Filing Page</a>')
    links_cell = '<span class="link-sep">|</span>'.join(link_parts) if link_parts else "&mdash;"
    cells = [
        f"<td>{id_cell}</td>",
        f"<td>{escape(s.company)}</td>",
        f'<td class="form">{form_text}</td>',
        f"<td>{links_cell}</td>",
    ]
    if market_data is not None:
        md = (s.ticker and market_data.get(s.ticker)) or None
        cells.append(f"<td>{_format_day_change(md.day_change_pct if md else None)}</td>")
        cells.append(f"<td>{_format_market_cap(md.market_cap if md else None)}</td>")
        cells.append(f"<td>{escape(md.industry) if md and md.industry else '&mdash;'}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def _render_group(
    situations_by_category: dict[str, list[Situation]],
    market_data: dict[str, MarketData] | None,
    id_header: str,
) -> str:
    sections = []
    for category in config.CATEGORY_ORDER:
        situations = situations_by_category.get(category)
        if not situations:
            continue
        label = config.CATEGORY_LABELS.get(category, category)
        header_cells = [
            f"<th>{id_header}</th>", "<th>Company</th>", "<th>Form</th>", "<th>Links</th>",
        ]
        if market_data is not None:
            header_cells += ["<th>Day %</th>", "<th>Market Cap</th>", "<th>Industry</th>"]
        rows = "\n".join(_situation_row(s, market_data) for s in situations)
        sections.append(
            f"<h3>{escape(label)} ({len(situations)})</h3>"
            "<table>"
            f"<tr>{''.join(header_cells)}</tr>"
            f"{rows}"
            "</table>"
        )
    return "\n".join(sections)


def _group_by_category(situations: list[Situation]) -> dict[str, list[Situation]]:
    grouped: dict[str, list[Situation]] = {}
    for s in situations:
        grouped.setdefault(s.category, []).append(s)
    return grouped


def build_html_digest(
    situations_by_category: dict[str, list[Situation]],
    run_date: str,
    market_data: dict[str, MarketData] | None = None,
) -> str:
    all_situations = [s for situations in situations_by_category.values() for s in situations]
    total = len(all_situations)

    public = [s for s in all_situations if s.is_public]
    private = [s for s in all_situations if not s.is_public]

    body_parts = []
    if public:
        body_parts.append(f'<div class="section-label">Public Companies ({len(public)})</div>')
        body_parts.append(_render_group(_group_by_category(public), market_data, id_header="Ticker"))
    if private:
        body_parts.append(f'<div class="section-label">Private Companies ({len(private)})</div>')
        body_parts.append(_render_group(_group_by_category(private), None, id_header="CIK"))

    body = "\n".join(body_parts) if body_parts else "<p>No situations matched today.</p>"

    footnote = ""
    if any(s.via_text_search for s in all_situations):
        footnote = (
            '<div class="footnote">'
            '<span class="text-match-badge">TEXT MATCH</span> rows were found by keyword search '
            "(6-K filings carry no item-code metadata like 8-K does), not exact form/item "
            "classification — treat these as leads to verify, not confirmed situations."
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{_STYLE}</style></head>
<body>
  <h1>SpecialSits Screener</h1>
  <div class="subtitle">{escape(run_date)} &middot; {total} situation{"s" if total != 1 else ""} across EDGAR</div>
  {body}
  {footnote}
</body>
</html>"""
