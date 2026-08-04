# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

Phase 1, early. Implemented: the tracked-ticker record (`src/pwi/models.py`), the JSON watchlist
store (`src/pwi/watchlist.py`), and the direct Alpha Vantage REST client with quota handling
(`src/pwi/alphavantage.py`). Not built yet: the two scheduled jobs, the custom MCP server, the
deployment.

Read [`docs/adr/adr-portfolio-watchlist-earnings-intelligence.md`](docs/adr/adr-portfolio-watchlist-earnings-intelligence.md)
before proposing structure — it fixes the scope, the data model, and *why* each data source was chosen.

## Commands

Python 3.12+, managed with `uv`.

```sh
uv sync                                        # install deps (incl. dev group)
uv run pytest                                  # all tests
uv run pytest tests/test_alphavantage.py::test_parses_quarters   # a single test
uv run ruff check . && uv run ruff format .    # lint + format
uv run scripts/check_mi_coverage.py UCG.MI     # Phase 1 coverage probe (costs 1 request)
```

Secrets come from `.env` (`ALPHAVANTAGE_API_KEY`); `data/watchlist.json` is local and untracked —
copy `data/watchlist.example.json` to create it.

## What this project is

A personal earnings-notification system for a watchlist of ~10–15 stocks (some held in portfolio,
some watch-only). For each tracked ticker it tracks the next earnings date and notifies when actual
results are published, distinguishing `held` from `watching` tickers in the notification.

It is a **notifier, not an analyst**. Out of scope in v1: earnings prediction / "will it beat"
analysis, buy-sell signals, automatic watchlist additions, and automatic sync of the
`held`/`watching` label with the real Fineco portfolio (Fineco's Open Banking API requires a PSD2
PSP license — rejected as disproportionate). Don't add these back without an ADR update.

## Architecture

Two **separate** scheduled jobs over one shared data store — they have different cadences and
triggers, and the ADR treats keeping them separate as a decision, not an accident:

1. **Calendar Sync** (~weekly) — refresh `next_earnings_date` for every tracked ticker; expected
   dates shift often.
2. **Results Watch** (~daily, active only in the window around each ticker's expected date) — detect
   published results, send a digest (EPS actual vs. estimate, revenue, surprise %, source link),
   then stamp `last_notified_period` to suppress duplicate alerts for that quarter.

Tracked-ticker record, minimum fields: `symbol`, `label` (`held` | `watching`, static and manually
maintained), `next_earnings_date`, `last_notified_period`.

## Data sources — do not substitute them

The source mapping is deliberate and each source exists to exercise a *distinct* integration style.
Swapping one for a more convenient equivalent defeats the point of the project:

| Concern | Source | Style being exercised |
|---|---|---|
| Fundamentals / company data | S&P Global (Kensho) MCP (already connected) | MCP **client** |
| Upcoming earnings dates | Alpha Vantage `EARNINGS_CALENDAR`, **called directly** — returns **CSV** | Raw **REST** — auth, rate limiting, parsing |
| Reported results (actual vs. estimate) | Alpha Vantage `EARNINGS`, **called directly** — returns JSON | Raw **REST** |
| Aggregation / digest / cross-ticker state logic | Custom minimal MCP server (Phase 3) | MCP **server** |

`EARNINGS` returns *only already-reported* quarters — it has no future date, so Calendar Sync cannot
be built on it. That job needs `EARNINGS_CALENDAR`, a different endpoint in a different format.
Two endpoints, two parsers.

Alpha Vantage also ships an official MCP server for the same data — using it was explicitly
rejected, since it would repeat the MCP-client skill already covered by Kensho and leave raw REST
integration unexercised. The overlap to avoid is in *style*, not in *data*.

Constraints to code against:
- Alpha Vantage free tier: **25 requests/day**. Budget: Calendar Sync calls `EARNINGS_CALENDAR`
  *without* `symbol` and with `horizon=3month`, getting every company in **one** CSV and filtering
  locally — 1 request/week, not one per ticker. Results Watch queries only tickers inside the active
  window, typically 0–3/day.
- Quota exhaustion arrives as **HTTP 200** with a `Note`/`Information` key, not an error status —
  already handled in `alphavantage.py`; keep it that way for any new endpoint.
- The daily quota is **not** tracked across runs yet; the client only reacts once the API refuses.
- **Unvalidated assumption:** Alpha Vantage earnings coverage for Milan-listed tickers
  (`UCG.MI`, `FBK.MI`, `RACE.MI`, `PRY.MI`). Verify with 2–3 `.MI` tickers before building the
  architecture on top of this source.

## Deployment target

**GitHub Actions** (`schedule:` cron), headless — Calendar Sync weekly, Results Watch daily ~06:00
UTC. There is no frontend and none is planned.

Three constraints here are decisions, not conveniences (ADR *Deployment* / *Delivery*):

- **MCP connectors are unreachable from scheduled jobs.** Kensho and Gmail are authenticated to a
  personal Claude account and invoked by a model in a client session — not an HTTP API code can
  call. Anything scheduled is plain Python with plain credentials.
- **Delivery is SMTP with a Gmail app password**, not the Gmail MCP: that connector only exposes
  `create_draft`/`update_draft` and cannot send. Don't reintroduce it as a delivery path.
- **State (`last_notified_period`) is committed back to the repo** by the job, since a cloud runner
  cannot see a local `data/watchlist.json`.

The custom MCP server (Phase 3) is deliberately *outside* the scheduled path. Aggregation lives in
`pwi.digest` with two entry points over it: a CLI for cron, an MCP server for interactive use from
Claude Code. Lambda + EventBridge is deferred to an optional Phase 5, not rejected.

## Implementation phases

Each phase is independently shippable; stopping after any one still leaves a usable increment.

1. Local script: read watchlist state via Kensho MCP; validate Alpha Vantage `.MI` coverage.
2. Alpha Vantage REST: `EARNINGS` (JSON) + `EARNINGS_CALENDAR` (CSV), with rate-limit handling.
3. `pwi.digest` aggregation/state module, wrapped by a minimal custom MCP server.
4. Both jobs on GitHub Actions, digest over SMTP, state committed back to the repo.
5. *(optional, later)* Lift the same entry points onto Lambda + EventBridge.

## Conventions

- Project docs and code are written in **English** (see the ADR's opening note).
- Architectural decisions live in `docs/adr/`. The existing ADR is still **Proposed** and carries no
  sequential number — assign one when it is adopted into the personal ADR catalog.
