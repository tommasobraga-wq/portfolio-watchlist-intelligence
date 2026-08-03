# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

The repository currently contains **no code** — only the design document at
[`docs/adr/adr-portfolio-watchlist-earnings-intelligence.md`](docs/adr/adr-portfolio-watchlist-earnings-intelligence.md).
It is not yet a git repository, has no build tooling, and no test suite. Read the ADR before
proposing any structure: it fixes the scope, the data model, and — importantly — *why* each
data source was chosen. Update this file with real build/test/run commands as soon as tooling lands.

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
| Earnings calendar + actual vs. estimate | Alpha Vantage `EARNINGS` REST endpoint, **called directly** | Raw **REST** — auth, rate limiting, parsing |
| Aggregation / digest / cross-ticker state logic | Custom minimal MCP server (Phase 3) | MCP **server** |

Alpha Vantage also ships an official MCP server for the same data — using it was explicitly
rejected, since it would repeat the MCP-client skill already covered by Kensho and leave raw REST
integration unexercised. The overlap to avoid is in *style*, not in *data*.

Constraints to code against:
- Alpha Vantage free tier: **25 requests/day**. Batch deliberately and handle rate limits explicitly.
- **Unvalidated assumption:** Alpha Vantage earnings coverage for Milan-listed tickers
  (`UCG.MI`, `FBK.MI`, `RACE.MI`, `PRY.MI`). Verify with 2–3 `.MI` tickers before building the
  architecture on top of this source.

## Deployment target

Scheduled serverless compute — Lambda + EventBridge (or Fargate if containerized) running the two
jobs on their own cadences. Notification delivery goes through the already-connected **Gmail MCP**;
there is no frontend and none is planned.

## Implementation phases

Each phase is independently shippable; stopping after any one still leaves a usable increment.

1. Local script: read watchlist state via Kensho MCP; validate Alpha Vantage `.MI` coverage.
2. Alpha Vantage REST integration (calendar dates + actual results) with rate-limit handling.
3. Minimal custom MCP server wrapping aggregation/state logic.
4. Deploy both scheduled jobs with digest delivery via Gmail MCP.

## Conventions

- Project docs and code are written in **English** (see the ADR's opening note).
- Architectural decisions live in `docs/adr/`. The existing ADR is still **Proposed** and carries no
  sequential number — assign one when it is adopted into the personal ADR catalog.
