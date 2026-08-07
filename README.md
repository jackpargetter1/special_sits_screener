# SpecialSits Screener — v0

Daily screener: pulls yesterday's SEC EDGAR filings, classifies the ones that match
a 20-category special-situations taxonomy (M&A, activist, tender offers, going-private,
spin-offs, capital return, restructuring/insolvency, delisting, litigation, etc. — see
Taxonomy below), and emails an HTML digest. Designed to run as a Cloud Run Job on a
daily Cloud Scheduler trigger.

## How it works

Discovery runs entirely against SEC's full-text search API (`efts.sec.gov`) rather
than the daily index files — it returns structured JSON per filing (company, ticker,
CIK, form type, and for 8-Ks, the disclosed item numbers) in one call, so no separate
fetch-and-parse-the-document step is needed for v0.

1. `main.py` queries every tracked form type (see `screener/config.py`) for the
   target date in one paginated sweep — a single EFTS request (comma-separated
   `forms` param, paginated), not one request per form.
2. `screener/classify.py` maps each hit to a category — directly for most forms
   (e.g. any `SCHEDULE 13D` is Activist Initial), via disclosed item code for
   8-Ks (e.g. item `1.03` = Insolvency), and for `13F-HR` only if the filer is in
   `screener/tracked_funds.py` (unfiltered, 400+ managers file one on a given day).
   `screener/text_search.py` runs supplementary/primary keyword-search passes for
   categories with a weak or nonexistent exact signal — see the taxonomy notes below.
3. `screener/models.py` turns each classified hit into a `Situation`, deduplicates
   at three levels (raw hit, accession, and filer identity — see module docstrings),
   and builds two links: a direct "Read Form" link to the human-readable primary
   document, and a "More Information" link to the filing's index page.
4. `screener/market_data.py` enriches public-company tickers with day % change,
   market cap, and industry via `yfinance`.
5. `screener/digest.py` renders an HTML digest: public companies first (with market
   data), private companies (incl. tracked funds' 13Fs) second, each grouped by category.
6. `screener/mailer.py` sends it over SMTP.

## Taxonomy

20 categories (order matches the digest's section order), each backed by one of
three signal types, in descending order of precision — see the header comment in
`screener/config.py` for the full rationale:

| Category | Signal | Type |
|---|---|---|
| M&A / Merger | `DEFM14A`, `PREM14A` (proxy stage) + keyword search on 8-K/6-K (announcement stage, e.g. `"definitive merger agreement"`) | exact + keyword |
| Acquisition | 8-K item `2.01`, minus anything keyword-matched as Divestiture | exact, keyword-split |
| Divestiture | 8-K item `2.01` / 6-K, keyword-matched (`"divested"`, `"sale of its"`, etc.) | keyword split |
| Activist Proxy | `DFAN14A`, `PREC14A`, `DEFC14A` | exact |
| Activist Initial | `SCHEDULE 13D` (incl. `/A` amendments — no separate amendment bucket in this taxonomy) | exact |
| Strategic Review | keyword search on 8-K/6-K (`"explore strategic alternatives"`, etc.) | keyword |
| Tender Offer | `SC TO-T`, `SC TO-C`, `SC 14D9` | exact |
| Issuer Tender | `SC TO-I` | exact |
| Going-Private | `SC 13E3` | exact |
| Going Dark | `15-12B`, `15-12G`, `15-15D` | exact |
| Spin-Off | `10-12B`, `10-12G` | exact |
| Capital Return | keyword search on 8-K/6-K (`"special dividend"`, `"authorizes a new share repurchase"`, etc.) | keyword |
| Rights Issue | `CB` (foreign cross-border offers) + keyword search (`"rights offering"`) | exact + keyword |
| Restructuring | keyword search on 8-K/6-K (`"restructuring support agreement"`, `"covenant waiver"`, etc.) | keyword |
| Insolvency | 8-K item `1.03` + 6-K keyword gap-fill (`"Chapter 11"`, etc.) | exact + keyword |
| Liquidation | keyword search on 8-K/6-K (`"plan of liquidation"`, etc.) | keyword |
| Delisting | 8-K item `3.01` + `25-NSE` + 6-K keyword gap-fill | exact + keyword |
| Busted M&A | 8-K item `1.02` + 6-K keyword gap-fill | exact + keyword |
| Litigation | keyword search on 8-K/6-K (`"class action complaint"`, etc.) | keyword |
| Domicile Change | keyword search on 8-K/6-K (`"reincorporate in"`, `"redomicile"`, etc.) | keyword |
| Tracked Fund 13F | `13F-HR`, filer CIK in `screener/tracked_funds.py` | exact + curated list |

All form-type labels were spot-checked live against the EDGAR full-text search API
(the API's internal labels don't always match the form names you'd expect — e.g.
Schedule 13D shows up as `SCHEDULE 13D`, not `SC 13D`). If a category never seems to
fire, that's the first thing to check — query `efts.sec.gov` directly for that form
over a wide date range and inspect the hits' `root_forms` to confirm the exact label.

**Not tracked, deliberately:** 8-K item `5.03` blanket (as a Domicile Change proxy —
56 hits/day, dominated by routine bylaw housekeeping unrelated to reincorporation) and
`8-A12B`/`8-A12G` (as a Spin-Off proxy — dominated by ETF trusts, preferred-stock
listings, and SPACs in live sampling). Both would add far more noise than signal;
Domicile Change is covered by keyword search instead, at lower recall but much less noise.

On spin-offs specifically: `10-12B`/`10-12G` are confirmed live and firing correctly
(e.g. Honeywell Aerospace's actual 2026 spinoff shows up). Worth knowing: roughly half
of `10-12G` hits are routine Section 12(g) holder-count registrations from private
credit funds/BDCs (not corporate spinoffs at all) — a possible future filter, not yet
implemented.

`screener/tracked_funds.py` is a curated (not exhaustively verified "top N by AUM" —
no live licensed ranking exists to build that from) list of ~130 well-known
multi-strategy and special-situations/event-driven/activist managers, each CIK
verified live against SEC's own company-search API. Not part of the 20-category list
above but kept on as a 21st bucket by request. It's a plain dict — add/remove entries
directly; the module docstring lists known gaps and how to re-verify a CIK.

### Keyword search (`screener/text_search.py`)

Roughly a third of the categories above have no dedicated SEC form or item code at
all (Strategic Review, Capital Return, Restructuring, Liquidation, Litigation,
Domicile Change), and several more only have an exact signal that catches a *late*
stage of the situation (M&A/Merger's `DEFM14A` only appears once a merger proxy is
filed, weeks/months after the deal was first announced) or only covers domestic
filers (Insolvency/Delisting/Busted M&A/Acquisition's 8-K item codes don't exist for
`6-K`, the foreign-private-issuer equivalent — FPIs would otherwise be invisible to
those categories entirely). `text_search.py` runs one supplementary EFTS full-text
query per phrase (e.g. `q="Chapter 11"`) to cover these:

- **Primary categories** (no exact signal, or the exact signal only catches a late
  stage): queried against **both `8-K` and `6-K`**, since domestic filers need
  covering too, not just FPIs.
- **Supplementary categories** (already have an exact, reliable domestic 8-K signal):
  queried against **`6-K` only** — adding keyword search on top of 8-K here would just
  duplicate the exact signal with a noisier one, for filers we already classify precisely.

This is a fundamentally fuzzier signal than form-type or item-code classification — a
keyword match can be a false positive (a filing mentioning "Chapter 11" only about a
*past* bankruptcy already emerged from, or routine earnings-release boilerplate
restating an existing buyback program rather than announcing a new one — both
observed and phrase-tuned around during testing). Every row built this way carries
`via_text_search=True` and renders with a visible amber **"TEXT MATCH"** badge in the
digest, plus an explanatory footnote — treat those as leads to verify, not confirmed
situations. Extend/tune the phrase lists in `text_search.py` to adjust recall vs. noise.

**Known ceiling on comprehensiveness:** keyword search on a curated phrase list will
always have lower recall than an approach that reads and classifies each filing's
actual content (e.g. an LLM-based classifier) — it only catches situations phrased
close to one of the tracked phrases. If coverage still feels incomplete after tuning
phrases, that's the next architectural step, not a bug in this approach — see chat
history for a live comparison against a reference site that appears to do exactly that.

**Known precision gap, item `1.02` specifically:** "Busted M&A" fires on *any*
terminated material agreement, not just a terminated merger agreement — item 1.02
doesn't distinguish. Observed live: a credit-agreement termination/replacement (a
Restructuring event, not a busted deal) landed in Busted M&A. Not yet fixed; would
need the same kind of keyword sub-split used for Acquisition/Divestiture.

**Not yet covered:** scoring/ranking, dedup across *runs* (a re-run of the same date
re-sends — the three-level dedup in `models.py` is all within a single run), and a
real market-holiday calendar (currently just skips weekends).

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in SMTP_PASSWORD (Gmail: use an app password) and review the rest
```

Load `.env` and run:

```bash
set -a && source .env && set +a   # Windows: use a small script or python-dotenv in a shell
python main.py
```

To test against a specific day instead of "yesterday" (useful since today's filings
won't be classified yet — the digest is meant to run after a full day closes):

```bash
RUN_DATE=2026-07-31 python main.py
```

## Dashboard

A separate, interactive counterpart to the email digest: same situations, same
taxonomy, but rendered as a filterable HTML table with an expandable per-company
financial detail panel (`screener/financials.py`; see that module's docstring for
the full sourcing/reconciliation rationale).

Fundamentals are a **per-field waterfall**, not a per-company fallback: SEC's latest
10-K first (authoritative), then `yfinance` for whatever the filer didn't tag (SG&A
and change-in-NWC are commonly missing), then `openbb` for whatever's still gapped
after both -- optional, `pip install -r requirements-optional.txt` to enable it, see
that file for why it's not a hard dependency. Every non-primary source is checked for
a systematic unit-scale mismatch against the primary source's revenue (the
`_reconcile_scale` function) before its figures are used, so a derived metric like
FCF = CFO − CapEx can't silently combine two same-named fields reported at different
scales.

```bash
RUN_DATE=2026-08-06 python build_dashboard.py dashboard.html
```

Takes ~1-2 minutes (SEC + yfinance calls per public company, sequential). Open the
file directly, or see "Hosting the dashboard" below to publish it somewhere that
refreshes on its own.

## Deploying to Cloud Run Jobs

See `deploy.sh` for the full sequence (Artifact Registry repo, service account,
Secret Manager for the SMTP password, `gcloud run jobs deploy`, and a Cloud Scheduler
trigger). Fill in `PROJECT_ID` at the top and either run it end-to-end or copy/paste
sections — it's written to be idempotent (`|| true` on the one-time setup calls) so
re-running it after code changes is safe, though re-running the whole script will also
just redeploy.

Quick reference once set up:

```bash
gcloud run jobs execute special-sits-screener --region=us-central1   # run now, out of schedule
gcloud builds submit --tag "$IMAGE" . && gcloud run jobs update special-sits-screener \
  --image="$IMAGE" --region=us-central1                              # redeploy after code changes
```

### Hosting the dashboard (auto-refreshing webpage)

The dashboard section of `deploy.sh` (below the email job) sets up a second, independent
Cloud Run Job + Cloud Scheduler pair — same container image, different entrypoint —
that runs `build_dashboard.py` every 15 minutes and uploads the result to a public GCS
bucket:

1. GCS bucket, public read (`roles/storage.objectViewer` for `allUsers`), writable by
   the job's service account (`roles/storage.objectAdmin`).
2. `gcloud run jobs deploy special-sits-dashboard` — same `$IMAGE`, but
   `--command="python" --args="build_dashboard.py,/tmp/dashboard.html"` and
   `DASHBOARD_GCS_BUCKET` set, which tells `build_dashboard.py` to upload after
   writing (see its `_upload_to_gcs`) instead of just leaving the file on local disk.
3. `gcloud scheduler jobs create http ... --schedule="*/15 * * * *"` — same pattern as
   the email job's daily trigger, just a tighter cron and pointed at the new job.

Live page: `https://storage.googleapis.com/<bucket>/dashboard.html`. The page itself
also carries a `<meta http-equiv="refresh" content="900">` tag, so a browser tab left
open reloads on the same 15-minute cadence and picks up each republish automatically
— no manual refresh needed. Run the one-time setup + build/push sections in
`deploy.sh` first if you haven't already (the dashboard job reuses that image and
service account).

**Updating the *Artifact preview link* from this conversation** (the private
`claude.ai/code/artifact/...` URL) is different from the above — that's a manual
snapshot, republished only when asked in a Claude Code session, not on its own
schedule. For a link that updates unattended, use the hosted GCS URL above instead.
