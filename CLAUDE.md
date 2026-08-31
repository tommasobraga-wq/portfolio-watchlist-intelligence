# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

Working end to end and deployed on GitHub Actions. Not built: the custom MCP server (Phase 3),
the optional Lambda deployment (Phase 5), the simulated portfolio (Phase 6).

Layering, and it matters — the interesting logic is deliberately kept away from I/O:

| Module | Role |
|---|---|
| `digest.py` | **all** decisions: window, de-duplication, adoption, overdue. Pure, takes `today` |
| `render.py` | **all** wording (Italian). Pure |
| `yahoo.py` | **all** vendor detail: DataFrames, `NaN`, tz-aware stamps. Nothing leaks past it |
| `jobs.py` | orchestration and persistence only |
| `sources.py` | the `EarningsSource` / `PriceSource` / `DividendSource` contracts both sides depend on |

Earnings happen four times a year, so the decision logic can only be exercised against fakes with
invented dates. Keep it pure — putting a network call or `date.today()` into `digest.py` makes the
interesting cases untestable.

**Two orderings are load-bearing, do not rearrange them:**
- State is written *after* a mail is delivered. Stamping first means an SMTP failure marks a
  quarter notified that nobody saw, and it is never retried.
- An unavailable ticker is skipped, never fatal — one throttled lookup must not cost the other
  twenty-six their results.

Read [`docs/adr/adr-portfolio-watchlist-earnings-intelligence.md`](docs/adr/adr-portfolio-watchlist-earnings-intelligence.md)
before proposing structure — it fixes the scope, the data model, and *why* each data source was chosen.

## Commands

Python 3.12+, managed with `uv`.

```sh
uv sync
uv run pytest -q                               # all tests; no network, no credentials
uv run pytest tests/test_digest.py -q          # one file
uv run ruff check . && uv run ruff format .

uv run python -m pwi calendar-sync             # refresh expected dates
uv run python -m pwi results-watch             # detect and mail results
uv run python -m pwi results-watch --dry-run   # prints mails, writes nothing
uv run python -m pwi dividend-watch            # detect and mail held-ticker distributions

uv run scripts/watchlist_tool.py show          # add / remove / edit / generate-key
uv run scripts/smoke_test.py                   # real source + real SMTP, sends real mail
```

**The watchlist is encrypted** (`data/watchlist.enc`, committed in that form) because it names
which securities are held. `PWI_WATCHLIST_KEY` in `.env` decrypts it; the same value plus
`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` and `NOTIFY_TO` exist as repository secrets. `.env` is
loaded automatically by entry points, with real environment variables taking precedence so CI
secrets are never shadowed.

Never write cleartext holdings into the repository — `watchlist_tool.py edit` decrypts to a temp
file and removes it afterwards, which is the pattern to follow.

## What this project is

A personal earnings-notification system for a watchlist of ~10–15 stocks (some held in portfolio,
some watch-only). For each tracked ticker it tracks the next earnings date and notifies when actual
results are published, distinguishing `held` from `watching` tickers in the notification.

It is a **notifier, not an analyst**. Out of scope in v1: earnings prediction / "will it beat"
analysis, buy-sell signals, automatic watchlist additions, and automatic sync of the
`held`/`watching` label with the real Fineco portfolio (Fineco's Open Banking API requires a PSD2
PSP license — rejected as disproportionate). Don't add these back without an ADR update.

## Architecture

Three **separate** scheduled jobs over one shared data store — they have different cadences and
triggers, and the ADR treats keeping them separate as a decision, not an accident:

1. **Calendar Sync** (~weekly) — refresh `next_earnings_date` for every tracked ticker; expected
   dates shift often.
2. **Results Watch** (~daily, active only in the window around each ticker's expected date) — detect
   published results, send a digest (EPS actual vs. estimate, revenue, surprise %, source link),
   then stamp `last_notified_period` to suppress duplicate alerts for that quarter.
3. **Dividend Watch** (daily, `held` tickers only) — detect a new entry in a ticker's dividend
   history and mail it, then stamp `last_notified_dividend_date`. No window: unlike earnings, no
   vendor here publishes a forward payment date for every exchange this watchlist covers (Milan
   included), so there is nothing to schedule against, only a historical series to watch. `watching`
   tickers are skipped — they pay nothing to the reader, so a distribution on one is not actionable.

Tracked-ticker record, minimum fields: `symbol`, `label` (`held` | `watching`, static and manually
maintained), `next_earnings_date`, `last_notified_period`, `last_notified_dividend_date`.

## Data sources — do not substitute them

The source mapping is deliberate and each source exists to exercise a *distinct* integration style.
Swapping one for a more convenient equivalent defeats the point of the project:

| Concern | Source | Style being exercised |
|---|---|---|
| Fundamentals / company data | S&P Global (Kensho) MCP (already connected) | MCP **client** |
| Earnings dates, results, and (later) prices | **Yahoo Finance via `yfinance`** | the only source verified to cover this watchlist at zero cost |
| Aggregation / digest / cross-ticker state logic | Custom minimal MCP server (Phase 3) | MCP **server** |

**Alpha Vantage was rejected on 2026-08-04 after measurement — do not reinstate it.** Its catalogue
is 14,243 securities across US exchanges only, with zero foreign-suffix symbols; `UCG.MI` returns no
earnings *and* an empty `TIME_SERIES_DAILY`. `SYMBOL_SEARCH` is a trap: it returns `CRIN.DEX`,
`UNCRY`, `0RLS.LON` for these companies, none of which exist in the fundamentals universe — never use
it to establish coverage. The Alpha Vantage client has been removed now that Yahoo is in place.

**`yfinance` is the runtime dependency; do not try to replace it with a hand-written REST client.**
That was attempted and abandoned on evidence: Yahoo fingerprints the **TLS handshake** (JA3/JA4),
not just cookies and headers, so `httpx` was refused with 429 on every endpoint — including
`chart`, which needs no crumb — across three retries over nine minutes. Correct HTTP does not help;
it would take browser impersonation via `curl_cffi`, i.e. defeating bot detection, which teaches
evasion rather than API integration.

**The raw-REST learning goal is therefore set aside in v1, deliberately** (see the ADR). The
cheapest documented API covering Milan, earnings and prices is $99/month, out of budget, and EODHD
does not list Milan at all. A working unattended notifier outranks the learning goal; the MCP
client and server skills are unaffected.

**Borsa Italiana's own dividend calendar was checked on 2026-08-05 as a candidate for a forward
`next_dividend_date` and not adopted, for now.** Manually driven via browser (`calendario-eventi-dividendi.html`
is a JS search form, no clean API endpoint found) it does return genuine forward ex-dividend dates
for some Milan names — Eni's 21/09/2026 matched Yahoo's own forward date exactly, a useful
cross-check — but a same-window search for UCG.MI and FBK.MI, the tickers this watchlist actually
holds, returned nothing: their boards have not yet set the next date anywhere, Yahoo included. The
gap is the event, not either source. Revisit only if a held ticker is one Borsa Italiana already
covers forward; until then it would add a scraper for a benefit nothing in the current watchlist
would see.

**`yfinance`'s own `Ticker.calendar` also carries `Ex-Dividend Date` / `Dividend Date` fields and
was checked the same way on 2026-08-31, with the same outcome.** They hold genuine forward dates,
but only once a board has declared the next distribution — until then the fields just echo the
last completed cycle, silently, with no flag distinguishing the two. Checked live against every
held ticker: GOOGL had a genuine forward pair; ACN and SCHW showed already-past dates (nothing
declared yet); UCG.MI, FBK.MI and RACE.MI (all Milan) had no `Dividend Date` at all — same gap as
Borsa Italiana, for the same reason: the boards haven't set one, not a source failure. Not adopted
for the same reason as above — it would help only intermittently for the non-Milan holdings, not
the Milan side this project cares most about covering.

Constraints to code against:
- **Sources go behind `EarningsSource` / `PriceSource` / `DividendSource` protocols.** No vendor concept — API keys,
  crumbs, `NaN`, DataFrames — crosses that boundary; `digest.py`, `jobs.py` and the MCP server depend
  on the protocol only. Derive the protocols from what the jobs actually need (two or three methods),
  not from every eventuality. This is why a vendor swap stays a one-class change.
- **Money is `Decimal`, never `float`.** Repeated portfolio valuation accumulates binary
  floating-point error. EPS tolerates `float`; prices and positions do not.
- Yahoo's endpoints are undocumented and can change without notice, so parse defensively and
  treat `SourceUnavailable` as "retry next run", never as "this ticker has no data".
- **`Ticker.calendar`'s `Revenue Average` is consensus for the *next unreported* quarter, not the
  one just published — do not pair it with actual revenue as an "estimate".** Confirmed live on
  2026-08-31 against NVDA: the digest showed an August-quarter actual against a November-quarter
  estimate. `Revenue` therefore has no `estimate` field; `_revenue_actual` in `yahoo.py` also
  rejects a stale income-statement column (>75 days before the report date) rather than showing
  an older quarter's figure as current.
- A simulated portfolio is a later phase but already binds source choice: it needs daily prices,
  dividends and splits from **one** consistent source, since mixing vendors yields incoherent returns.

## Deployment target

**GitHub Actions** (`schedule:` cron), headless — Calendar Sync weekly (Sun 03:00 UTC), Results
Watch every 3 hours, Dividend Watch daily (06:00 UTC). There is no frontend and none is planned.

Three hours rather than faster because the limit on latency is how long Yahoo takes to populate an
actual EPS after a company reports, not the polling rate. Actions cron is best-effort and drifts;
at this cadence a missed run is invisible.

Dividend Watch runs daily rather than every three hours: the held tickers on this watchlist pay
1-4 times a year (measured), so a daily check costs nothing. A faster cadence would not buy more
timeliness either — Yahoo's own historical series was measured to lag the real ex-dividend date by
2-5 days regardless of polling frequency, and that lag is the bottleneck, not this schedule. Do not
assume a forward payment date exists to schedule against: Yahoo never publishes one for the Milan
tickers this watchlist actually holds (UCG.MI, FBK.MI, RACE.MI), only for some US names.

Three constraints here are decisions, not conveniences (ADR *Deployment* / *Delivery*):

- **MCP connectors are unreachable from scheduled jobs.** Kensho and Gmail are authenticated to a
  personal Claude account and invoked by a model in a client session — not an HTTP API code can
  call. Anything scheduled is plain Python with plain credentials.
- **Delivery is SMTP with a Gmail app password**, not the Gmail MCP: that connector only exposes
  `create_draft`/`update_draft` and cannot send. Don't reintroduce it as a delivery path.
- **State (`last_notified_period`, `last_notified_dividend_date`) is committed back to the repo** by
  the job, since a cloud runner cannot see a local `data/watchlist.json`.

The custom MCP server (Phase 3) is deliberately *outside* the scheduled path. Aggregation lives in
`pwi.digest` with two entry points over it: a CLI for cron, an MCP server for interactive use from
Claude Code. Lambda + EventBridge is deferred to an optional Phase 5, not rejected.

## Implementation phases

Each phase is independently shippable; stopping after any one still leaves a usable increment.

1. ~~Validate Alpha Vantage `.MI` coverage~~ — done, negative, source moved. Kensho MCP read still open.
2. `EarningsSource`/`PriceSource` protocols + hand-written Yahoo REST client, `yfinance` as oracle.
3. `pwi.digest` aggregation/state module, wrapped by a minimal custom MCP server.
4. Both jobs on GitHub Actions, digest over SMTP, state committed back to the repo.
5. *(optional, later)* Lift the same entry points onto Lambda + EventBridge.
6. *(intended, later)* Simulated portfolio: positions, daily valuation, performance.

## Conventions

- Project docs and code are written in **English** (see the ADR's opening note).
- Architectural decisions live in `docs/adr/`. The existing ADR is still **Proposed** and carries no
  sequential number — assign one when it is adopted into the personal ADR catalog.
