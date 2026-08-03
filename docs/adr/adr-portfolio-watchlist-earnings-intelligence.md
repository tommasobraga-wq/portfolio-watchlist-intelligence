# ADR: Portfolio & Watchlist Earnings Intelligence

> Note: assign the correct sequential number when adding this to your personal ADR/pattern catalog (e.g. `ADR-00X`). Written in English for consistency with the rest of the codebase — flag if you'd rather keep project docs in Italian.

## Status
Proposed

## Context

This is a personal, hobby-scope project, standalone with its own repository and context. Its purpose is twofold:

1. Automate a real, recurring personal task: tracking earnings dates and results for a watchlist of stocks (some held in portfolio, some purely monitored).
2. Deliberately exercise three technical skills: consuming an existing MCP server as a client, calling an external REST API directly (no MCP abstraction), and building a minimal custom MCP server. Cloud deployment (scheduled, serverless) is a secondary goal.

Today this tracking is done manually: checking earnings dates ticker by ticker, and separately remembering to check whether results have been published.

### Problem statement

For each tracked ticker (held in portfolio or watchlist-only), the system should know the next earnings date, notify when actual results are published, and distinguish — in the notification — tickers held in portfolio from tickers only being watched.

### Explicitly out of scope (v1)

- Earnings estimate prediction or "will it beat expectations" analysis — this stays a notification system, not a research/analysis engine.
- Buy/sell recommendations or any trading signal.
- Automatic addition of new tickers to the watchlist (manual only).
- Automatic sync between the `held`/`watching` label and the real Fineco portfolio. Verified that Fineco's Open Banking API requires a PSD2 PSP license from a National Competent Authority — disproportionate for a personal project. The label is managed manually.

## Decision

### Data model

A tracked-ticker record with, at minimum:

- `symbol`
- `label`: `held` | `watching` — static, manually managed, updated by hand when a position is opened/closed
- `next_earnings_date`
- `last_notified_period` — guards against duplicate notifications for the same quarter

### Two scheduled processes, not one

Calendar tracking and results notification have different cadences and triggers, so they are modeled as two separate jobs sharing the same data store:

1. **Calendar Sync** (e.g. weekly) — for every tracked ticker, fetch/refresh the next expected earnings date. Expected dates shift often enough to need periodic re-checking.
2. **Results Watch** (e.g. daily, active only in the window around each ticker's expected date) — check whether actual results have been published. If so, compile and send a digest (EPS actual vs. estimate, revenue, surprise %, source link), then mark the ticker as notified for that period to prevent duplicate alerts.

### Data sources — mapped deliberately to distinct skills

| Concern | Source | Why |
|---|---|---|
| Fundamentals / company data | S&P Global (Kensho) MCP — already connected | MCP **client** skill: consuming an existing MCP server |
| Earnings calendar + actual vs. estimate results | Alpha Vantage `EARNINGS` REST endpoint, called directly (not via their official MCP server) | Raw **REST API** skill: auth, rate-limiting, parsing, without an MCP layer in between |
| Aggregation / state logic not covered by the above (e.g. computed digest content, cross-ticker rollup) | Custom-built minimal MCP server (Phase 3) | MCP **server** skill: exposing your own tools |

This mapping is deliberate: Alpha Vantage also offers an official MCP server for the same data, but using it here would exercise "MCP client" a second time (after Kensho) while leaving the raw-API skill uncovered. Kensho and Alpha Vantage are complementary in data (fundamentals vs. earnings actuals), not redundant — the overlap to avoid is in *skill*, not in *data*.

**Open risk to validate in Phase 1, not assumed:** Alpha Vantage's earnings-data coverage for Milan-listed tickers (UCG.MI, FBK.MI, RACE.MI, PRY.MI) is unconfirmed. Test with 2–3 Italian tickers before committing the architecture around this source. Free tier is capped at 25 requests/day — workable for a ~10–15 ticker watchlist if batched carefully, but requires deliberate rate-limit handling.

### Deployment

Scheduled compute (Lambda + EventBridge, or Fargate if containerization is preferred) running the two jobs on their respective cadences. Notification delivery via Gmail MCP (already connected) rather than a custom frontend.

## Consequences

**Positive:**
- Closes a real personal gap (manual earnings tracking) with a system that persists past any single event (unlike a trip-scoped project).
- Each data source maps to exactly one target skill, so the project can be evaluated phase by phase against concrete learning goals, not just "it works."
- Narrow, self-contained scope (notification only, no analysis) keeps the project achievable end-to-end without scope creep.

**Trade-offs / accepted limitations:**
- Manual label maintenance (`held`/`watching`) means it can drift from the real portfolio if forgotten after a trade — accepted for v1 given the PSD2 licensing cost of automating it.
- Free-tier API limits constrain watchlist size and polling frequency; scaling up would require a paid tier or a different data source.
- No earnings-outcome analysis; this is a notification system, not a research one.

## Alternatives considered and rejected

1. **Alpha Vantage via their official MCP server instead of raw REST** — rejected: would duplicate the MCP-client skill already exercised via Kensho and leave direct API integration unexercised.
2. **Dynamic `held`/`watching` label synced automatically to the real Fineco portfolio** — rejected: requires a PSD2 PSP license from a National Competent Authority; disproportionate for a personal-use project. Revisit only if a manual CSV-export-based sync (not a live API) is ever worth the effort.
3. **Adding earnings-outcome prediction/analysis** — rejected: a narrower scope (notifier, not analyst) is more likely to actually ship end-to-end.

## Implementation phases

| Phase | Deliverable | Skill exercised |
|---|---|---|
| 1 | Local script: pull watchlist state via Kensho MCP; validate Alpha Vantage earnings coverage for .MI tickers | MCP client |
| 2 | Alpha Vantage REST integration for calendar dates + actual results, with rate-limit handling | Direct API integration |
| 3 | Minimal custom MCP server wrapping aggregation/state logic | MCP server |
| 4 | Deploy both scheduled jobs (Lambda/Fargate + EventBridge) with digest delivery via Gmail MCP | Cloud deployment |

Each phase is independently shippable; stopping after any phase still yields a usable increment.
