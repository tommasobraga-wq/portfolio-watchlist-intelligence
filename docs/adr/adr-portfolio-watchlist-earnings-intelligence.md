# ADR: Portfolio & Watchlist Earnings Intelligence

> Note: assign the correct sequential number when adding this to your personal ADR/pattern catalog (e.g. `ADR-00X`). Written in English for consistency with the rest of the codebase — flag if you'd rather keep project docs in Italian.

## Status
Proposed

**Revised 2026-08-04** — the deployment and delivery sections were rewritten after discovering that
MCP connectors are not reachable from headless scheduled compute, and that the Gmail connector
cannot send mail (see *Deployment* and *Alternatives* 4–5). The data-model, scope, and data-source
decisions are unchanged.

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

**Correction: `EARNINGS` alone is not sufficient.** The `EARNINGS` endpoint returns *already reported*
quarters only; it carries no future date, so Calendar Sync cannot be built on it. The upcoming-date
half of the problem needs `EARNINGS_CALENDAR`, a separate endpoint that responds in **CSV, not JSON**.
The two jobs therefore use two different endpoints with two different parsers. This does not change
the "raw REST, no MCP layer" decision — if anything it strengthens it, since handling a second
response format is exactly the integration work the project set out to exercise.

**Open risk to validate in Phase 1, not assumed:** Alpha Vantage's earnings-data coverage for Milan-listed tickers (UCG.MI, FBK.MI, RACE.MI, PRY.MI) is unconfirmed. Test with 2–3 Italian tickers before committing the architecture around this source.

**Quota budget.** Free tier is capped at 25 requests/day, which is comfortable once the two jobs are
budgeted separately:

- *Calendar Sync*: `EARNINGS_CALENDAR` called **without** a `symbol` and with `horizon=3month` returns
  every covered company in a single CSV. Filtering to the watchlist happens locally, so the cost is
  **1 request/week**, not one per ticker.
- *Results Watch*: `EARNINGS` is per-symbol, but only tickers whose `next_earnings_date` falls inside
  the active window are queried — typically 0–3/day, 5–8/day at the mid-Feb / mid-May / early-Aug /
  mid-Nov peaks.

Worst case stays well under the cap. Rate-limit handling is still explicit, because Alpha Vantage
signals quota exhaustion with HTTP 200 plus a `Note`/`Information` key rather than an error status.

### Deployment: GitHub Actions, headless

**The constraint that drives this section:** MCP connectors (Kensho, Gmail) are authenticated to a
personal Claude account and are invoked *by a model inside a client session*. They are not an HTTP
API that arbitrary code can call. A job woken by cron at 06:00 with nobody at the keyboard cannot
reach them. Any scheduled process is therefore ordinary headless Python, and whatever it needs must
be reachable with a plain credential.

Scheduling runs on **GitHub Actions** (`schedule:` cron), chosen over Lambda + EventBridge: the
repository is already hosted there, it is free at this volume, and it avoids an AWS account,
deployment packaging, and cold starts. Serverless is a *secondary* goal of this project (see
*Context*), and deferring it costs nothing architecturally — the jobs are plain entry points and can
be lifted onto Lambda later without touching the logic. Two workflows:

| Job | Cron (UTC) | Notes |
|---|---|---|
| Calendar Sync | weekly | Refreshes `next_earnings_date` for every tracked ticker. |
| Results Watch | daily, ~06:00 | Catches the previous evening's US after-close and the morning's Italian pre-open releases. |

The ADR's "active only in the window around each expected date" is a **filter inside the job**, not a
scheduler feature: Results Watch fires every day and queries only the tickers currently in window,
making zero API calls on empty days. Cron is UTC while Italy shifts between UTC+1 and UTC+2, so the
local run time moves by an hour across DST — harmless here, but not a bug when observed.

### Delivery: SMTP, not the Gmail MCP

The Gmail connector exposes `create_draft` / `update_draft` and no send tool. It can compose a draft
but cannot deliver it, and a draft sitting in a folder raises no phone notification — it would be a
notifier that does not notify. Combined with the headless constraint above, the Gmail MCP is not a
viable delivery path in either direction.

Digests are sent over **SMTP using a Gmail app password**, held as a GitHub Actions secret. A handful
of lines, no OAuth flow, works headless. The Gmail API with a self-managed refresh token, or SES,
remain available if richer control is ever needed.

### State persistence

`last_notified_period` must survive between runs — it is the only thing preventing the same result
from being re-notified every day for a week — and a cloud job cannot see a local `data/watchlist.json`.
State is kept by having the job **commit the updated watchlist JSON back to the repository**. It is
rustic, but at this volume it is free, needs no extra service, and yields a full audit history of
every date shift and notification for nothing. S3 or DynamoDB become worthwhile only alongside a
later move to Lambda.

### Keeping the MCP-server goal intact

Since the scheduled path is headless, the custom MCP server (Phase 3) is deliberately **not** placed
in it. Aggregation and state logic live in a plain module (`pwi.digest`) with **two entry points over
the same code**: a CLI that cron invokes, and an MCP server used interactively from Claude Code for
ad-hoc queries ("what's reporting this week?"). The MCP-server skill is still exercised for real, and
the scheduled job stays dependency-free. Kensho MCP likewise stays an interactive Phase 1 tool,
outside the scheduled path.

## Consequences

**Positive:**
- Closes a real personal gap (manual earnings tracking) with a system that persists past any single event (unlike a trip-scoped project).
- Each data source maps to exactly one target skill, so the project can be evaluated phase by phase against concrete learning goals, not just "it works."
- Narrow, self-contained scope (notification only, no analysis) keeps the project achievable end-to-end without scope creep.

**Trade-offs / accepted limitations:**
- Manual label maintenance (`held`/`watching`) means it can drift from the real portfolio if forgotten after a trade — accepted for v1 given the PSD2 licensing cost of automating it.
- Free-tier API limits constrain watchlist size and polling frequency; scaling up would require a paid tier or a different data source.
- No earnings-outcome analysis; this is a notification system, not a research one.
- Serverless deployment, one of the stated learning goals, is deferred to an optional Phase 5 rather than delivered in v1.
- Committing state back to the repository means the watchlist is public if the repository is, and every run produces a commit — acceptable for a personal watchlist of 10–15 symbols, but it is a real coupling of data to version control.
- A Gmail app password is a long-lived, broadly-scoped credential. It lives only as a GitHub Actions secret, but it is weaker isolation than a scoped OAuth token.
- Two Alpha Vantage endpoints in two response formats (JSON and CSV) mean two parsers to maintain instead of one.

## Alternatives considered and rejected

1. **Alpha Vantage via their official MCP server instead of raw REST** — rejected: would duplicate the MCP-client skill already exercised via Kensho and leave direct API integration unexercised.
2. **Dynamic `held`/`watching` label synced automatically to the real Fineco portfolio** — rejected: requires a PSD2 PSP license from a National Competent Authority; disproportionate for a personal-use project. Revisit only if a manual CSV-export-based sync (not a live API) is ever worth the effort.
3. **Adding earnings-outcome prediction/analysis** — rejected: a narrower scope (notifier, not analyst) is more likely to actually ship end-to-end.
4. **Lambda + EventBridge for the scheduled jobs** — deferred, not rejected on merit. It is the right choice when the *goal* is practising AWS infrastructure, but it is more setup for an identical functional result, and serverless is a secondary goal here. The jobs are plain CLI entry points, so migrating later is a packaging exercise, not a rewrite. Revisit as a follow-up phase.
5. **Gmail MCP for delivery** — rejected on two independent grounds: it is unreachable from headless scheduled compute, and the connector has no send capability (drafts only). Neither is a limitation that better code could work around.
6. **A Claude scheduled cloud agent instead of GitHub Actions** — this *would* keep MCP connectors available in a scheduled context, but it puts a model in the loop for what is deterministic work (fetch, compare, format), and the Gmail send limitation applies regardless. Rejected as the wrong tool for a fixed-logic job.

## Implementation phases

| Phase | Deliverable | Skill exercised |
|---|---|---|
| 1 | Local script: pull watchlist state via Kensho MCP; validate Alpha Vantage earnings coverage for .MI tickers | MCP client |
| 2 | Alpha Vantage REST integration: `EARNINGS` (JSON, reported results) + `EARNINGS_CALENDAR` (CSV, upcoming dates), with rate-limit handling | Direct API integration |
| 3 | `pwi.digest` aggregation/state module, wrapped by a minimal custom MCP server for interactive use | MCP server |
| 4 | Both jobs scheduled on GitHub Actions, digest delivered over SMTP, state committed back to the repo | CI/CD + scheduling |
| 5 *(optional, later)* | Lift the same entry points onto Lambda + EventBridge | Serverless deployment |

Each phase is independently shippable; stopping after any phase still yields a usable increment.
