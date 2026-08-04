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
| Earnings dates, results, and (later) prices | **Yahoo Finance undocumented endpoints, called directly** | Raw **REST** — cookie/crumb session, token refresh, defensive parsing |
| Aggregation / digest / cross-ticker state logic | Custom minimal MCP server (Phase 3) | MCP **server** |

**Alpha Vantage was rejected on 2026-08-04 after measurement — do not reinstate it.** Its catalogue
is 14,243 securities across US exchanges only, with zero foreign-suffix symbols; `UCG.MI` returns no
earnings *and* an empty `TIME_SERIES_DAILY`. `SYMBOL_SEARCH` is a trap: it returns `CRIN.DEX`,
`UNCRY`, `0RLS.LON` for these companies, none of which exist in the fundamentals universe — never use
it to establish coverage. `src/pwi/alphavantage.py` is kept only as the reference pattern for quota
and error handling.

**Do not replace the hand-written Yahoo client with `yfinance`.** It is a dev dependency and test
oracle only — comparing against it tells you whether Yahoo changed or the client did. Using it as
the integration would abstract away the raw-REST work this slot exists to exercise, exactly like
Alpha Vantage's official MCP server would have.

Alpha Vantage also ships an official MCP server for the same data — using it was explicitly
rejected, since it would repeat the MCP-client skill already covered by Kensho and leave raw REST
integration unexercised. The overlap to avoid is in *style*, not in *data*.

Constraints to code against:
- **Sources go behind `EarningsSource` / `PriceSource` protocols.** No vendor concept — API keys,
  crumbs, `NaN`, DataFrames — crosses that boundary; `digest.py`, `jobs.py` and the MCP server depend
  on the protocol only. Derive the protocols from what the jobs actually need (two or three methods),
  not from every eventuality. This is why a vendor swap stays a one-class change.
- **Money is `Decimal`, never `float`.** Repeated portfolio valuation accumulates binary
  floating-point error. EPS tolerates `float`; prices and positions do not.
- Yahoo requires a **cookie + crumb** handshake and rejects naive requests. Expect to refresh the
  token and to parse defensively — the endpoints are undocumented and can change without notice.
- A simulated portfolio is a later phase but already binds source choice: it needs daily prices,
  dividends and splits from **one** consistent source, since mixing vendors yields incoherent returns.

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
