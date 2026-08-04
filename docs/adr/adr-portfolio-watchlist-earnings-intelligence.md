# ADR: Portfolio & Watchlist Earnings Intelligence

> Note: assign the correct sequential number when adding this to your personal ADR/pattern catalog (e.g. `ADR-00X`). Written in English for consistency with the rest of the codebase — flag if you'd rather keep project docs in Italian.

## Status
Proposed

**Revised 2026-08-04**, twice, both times because a stated assumption failed when tested:

1. Deployment and delivery were rewritten after finding that MCP connectors are unreachable from
   headless scheduled compute and that the Gmail connector cannot send mail (*Deployment*,
   *Delivery*, *Alternatives* 4–6).
2. The earnings data source moved from Alpha Vantage to Yahoo Finance after Phase 1 measured that
   Alpha Vantage covers no Milan-listed security, for earnings or prices (*Phase 1 outcome*,
   *Alternatives* 7). Sources are now consumed through `EarningsSource` / `PriceSource` protocols.

The problem statement, data model and scope are unchanged. Both revisions vindicate the original
decision to mark coverage an *open risk to validate*, not an assumption.

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

### Known future direction (out of v1, but binding on this ADR)

A **simulated portfolio** tracking positions and performance over time is intended as a later phase.
It is not in v1 scope, but it is recorded here because it *constrains the decision above*: it
requires daily prices, dividends and splits, from a single consistent source. Valuing a portfolio
from two vendors yields subtly incoherent returns — different close conventions, different
split/dividend adjustment methodology. Any source chosen now must therefore be assessed on price
coverage as well as earnings coverage, which is why Alpha Vantage's empty `TIME_SERIES_DAILY` for
`.MI` (below) was disqualifying rather than merely inconvenient.

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
| Earnings dates + actual vs. estimate results, and (later) prices | **Yahoo Finance via `yfinance`** | The only source verified to cover the actual watchlist at zero cost |
| Aggregation / state logic not covered by the above (e.g. computed digest content, cross-ticker rollup) | Custom-built minimal MCP server (Phase 3) | MCP **server** skill: exposing your own tools |

#### The raw-REST skill is set aside in v1 — deliberately

This slot originally existed to exercise raw REST integration. That goal is **dropped for now**, and
the reasoning is recorded so it is not silently reintroduced:

- A hand-written client cannot talk to Yahoo. Yahoo fingerprints the **TLS handshake (JA3/JA4)**, not
  just cookies and headers; `yfinance`'s own HTTP layer documents this and ships `curl_cffi` for
  browser impersonation. Measured: `httpx` received HTTP 429 on every endpoint — including `chart`,
  which needs no crumb — across three retries over nine minutes. No amount of correct HTTP fixes it.
- Writing that client would therefore mean deliberately defeating bot detection, which is a different
  activity from API integration, and would teach evasion rather than the intended skill.
- A documented, paid API would exercise the skill properly, but the cheapest option covering Milan,
  earnings and prices is Twelve Data Pro at $99/month — **out of budget** — and EODHD (~€40/month
  à la carte) does not list Milan among its supported markets at all.

**Priority, stated plainly:** a working unattended notifier for the real watchlist outranks the
learning goal. The MCP-client and MCP-server skills remain exercised. Raw REST can be revisited if a
covered, affordable, documented API appears, or in a later phase against a different target.

Consequently `yfinance` is a **runtime dependency**, not a test oracle, and `alphavantage.py` is
retired: it covers no Milan security, and keeping a second source alive purely to exercise a skill
would be complexity without functional benefit.

#### Phase 1 outcome: Alpha Vantage rejected on coverage (verified 2026-08-04)

The original choice was Alpha Vantage `EARNINGS`. Phase 1 existed to validate its Milan coverage
rather than assume it, and the answer was negative. Measured, not inferred:

- `LISTING_STATUS` returns **14,243 securities across NASDAQ, NYSE, BATS, AMEX, NYSE ARCA and NYSE MKT
  only** — no other exchange, and **zero** symbols carrying a foreign-exchange suffix.
- `UCG.MI` returns no earnings data. IBM, used as a control, returns 122 quarters, so the key and
  client were working.
- `EARNINGS_CALENDAR` is 4,782 USD rows out of 4,789. None of UniCredit, Ferrari, Fineco or Prysmian
  appear.
- `TIME_SERIES_DAILY` for `UCG.MI` returns an empty object — **prices are uncovered too**, not just
  fundamentals.

`SYMBOL_SEARCH` is a trap worth recording: it happily returns `CRIN.DEX`, `UNCRY`, `0RLS.LON` for
these companies, none of which exist in the fundamentals universe. The search index is far broader
than the data, so it cannot be used to establish coverage.

A second, independent defect surfaced: `EARNINGS_CALENDAR` (4,789 entries) is much narrower than the
listing universe (14,243), and `RACE` — NYSE-listed, 44 quarters of history, reported 8 days prior —
has **no calendar entry at all**. The forward-date half was therefore unreliable even for covered US
names, which alone would have blocked Calendar Sync.

#### Why Yahoo, and what it costs

Verified against the actual watchlist: forward earnings dates and EPS estimates for all four `.MI`
tickers, 25 quarters of history with estimate / reported / surprise %, plus daily prices, dividends
and splits. One source covers both the current scope and the intended portfolio extension.

It carries a real, accepted risk: the endpoints are undocumented, Yahoo's terms discourage automated
access, and anti-bot measures (cookie + crumb) are active and were observed rejecting naive requests.
This is accepted for a personal, non-commercial project that redistributes nothing. The expected
failure mode is "stops working one day", and the fallbacks are mapped in advance:

| Half | Fallback if Yahoo breaks | Cost |
|---|---|---|
| Earnings calendar (`.MI`) | Borsa Italiana's own corporate calendar — regulatory filing, PDF | €0, awkward parsing |
| Daily prices (`.MI`) | A worldwide EOD plan (e.g. EODHD) | ~€17–20/month |

Surveyed and rejected on coverage or price: Twelve Data (free tier is 3 markets / trial symbols,
earnings from Pro+), EODHD (worldwide EOD from ~€20/mo but the calendar only in a €99.99 bundle),
Financial Modeling Prep (free tier US-only, calendar is premium), Finnhub (international is paid).
The pattern is structural rather than incidental: Euronext licenses Borsa Italiana data, so no
commercial provider gives it away. Stooq, a free CSV alternative for prices, is behind a JavaScript
bot challenge and unusable headlessly.

### Source abstraction: `EarningsSource` and `PriceSource`

Data sources are consumed through two narrow `Protocol`s stated in domain terms — "next earnings
date", "latest reported quarter", "daily closes" — with no vendor concept (API keys, crumbs, CSV,
`NaN`, DataFrames) crossing the boundary. Jobs, digest logic and the MCP server depend on the
protocol; each vendor is one implementation behind it.

This is not speculative abstraction. Within a single day this project selected a source, built a
client against it, and found it unfit for the perimeter; the replacement is explicitly fragile and
its paid successor is already identified. Swapping vendors must stay a one-class change. The
protocols are also what let the interesting logic — notification de-duplication, the active window,
first-run bootstrap — be tested offline against fakes, with no network and no live quarter to wait for.

The protocols are to be **derived from what the jobs actually require** (two or three methods), not
designed up front for every eventuality.

**Money is `Decimal`, never `float`** — repeated portfolio valuation accumulates binary
floating-point error. EPS values tolerate `float`; positions and prices do not.

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
- **The whole system rests on undocumented, actively defended endpoints.** Yahoo can break the client without notice, and the same break takes out both earnings and (later) portfolio valuation. Gaps in a price series propagate into wrong returns, which is worse than a missed notification because it is silent. The `PriceSource` / `EarningsSource` protocols exist to bound the blast radius; the fallbacks and their cost are mapped above.
- Accepting a source whose terms discourage automated access is a deliberate compromise, justified only by this being personal, non-commercial and non-redistributing. It would not be acceptable for anything shared or published.
- The free-tier landscape means the project is free today but has a known price if it must move: ~€17–20/month for prices, €0 plus awkward PDF parsing for the calendar.

## Alternatives considered and rejected

1. **Alpha Vantage via their official MCP server instead of raw REST** — rejected: would duplicate the MCP-client skill already exercised via Kensho and leave direct API integration unexercised.
2. **Dynamic `held`/`watching` label synced automatically to the real Fineco portfolio** — rejected: requires a PSD2 PSP license from a National Competent Authority; disproportionate for a personal-use project. Revisit only if a manual CSV-export-based sync (not a live API) is ever worth the effort.
3. **Adding earnings-outcome prediction/analysis** — rejected: a narrower scope (notifier, not analyst) is more likely to actually ship end-to-end.
4. **Lambda + EventBridge for the scheduled jobs** — deferred, not rejected on merit. It is the right choice when the *goal* is practising AWS infrastructure, but it is more setup for an identical functional result, and serverless is a secondary goal here. The jobs are plain CLI entry points, so migrating later is a packaging exercise, not a rewrite. Revisit as a follow-up phase.
5. **Gmail MCP for delivery** — rejected on two independent grounds: it is unreachable from headless scheduled compute, and the connector has no send capability (drafts only). Neither is a limitation that better code could work around.
6. **A Claude scheduled cloud agent instead of GitHub Actions** — this *would* keep MCP connectors available in a scheduled context, but it puts a model in the loop for what is deterministic work (fetch, compare, format), and the Gmail send limitation applies regardless. Rejected as the wrong tool for a fixed-logic job.
7. **Alpha Vantage as the earnings source** — rejected after measurement; see *Phase 1 outcome*. Retained here because the reasoning that selected it was sound and only the coverage assumption failed.
8. **An LLM + web-search job instead of a structured API** — this mirrors how the task is done manually today and has no coverage limit, since it can read Borsa Italiana, company IR pages or the financial press for any ticker. Rejected for unattended operation: it is non-deterministic, its failure mode is a *plausible wrong answer* rather than an error, and around an expected reporting date the web is full of preview articles that are hard to distinguish from actual published results — risking notifications for results that were never released. The manual method works because a human is in the loop supplying plausibility checks and source judgement; automating it removes exactly that. It is also unusable for the intended portfolio extension, which needs consistent daily price series. Revisit only as a *fallback* for tickers no API covers, never as the primary path.
9. **A hand-written Yahoo REST client** — rejected on evidence, not preference: Yahoo blocks on TLS fingerprint, so `httpx` is refused regardless of correct cookie/crumb handling (429 on all endpoints across three retries). Building it would require browser impersonation via `curl_cffi`, i.e. defeating bot detection. `yfinance` is adopted instead as a runtime dependency.
10. **A paid API to get a legitimate raw-REST integration** — rejected on budget. Twelve Data Pro ($99/month) is the cheapest tier combining EU markets, earnings and dividends/splits; EODHD à la carte is ~€40/month but its published market list omits Milan entirely, so it may not even cover the perimeter. Not worth the spend for a personal project.
11. **Keeping Alpha Vantage alongside Yahoo for the US subset** — considered, since earnings are discrete events and mixing sources there causes no inconsistency (unlike prices, where one source is mandatory). Rejected: it would exist purely to keep the raw-REST skill alive, adding a second source, a composite and a fallback path for no functional gain. Simplicity of an unattended job wins.

## Implementation phases

| Phase | Deliverable | Skill exercised |
|---|---|---|
| 1 | ~~Validate Alpha Vantage `.MI` coverage~~ — **done 2026-08-04, negative**; source moved to Yahoo. Kensho MCP read of watchlist state still outstanding | MCP client |
| 2 | `EarningsSource` / `PriceSource` protocols + `yfinance`-backed implementation | — (raw REST set aside; see above) |
| 3 | `pwi.digest` aggregation/state module, wrapped by a minimal custom MCP server for interactive use | MCP server |
| 4 | Both jobs scheduled on GitHub Actions, digest delivered over SMTP, state committed back to the repo | CI/CD + scheduling |
| 5 *(optional, later)* | Lift the same entry points onto Lambda + EventBridge | Serverless deployment |
| 6 *(intended, later)* | Simulated portfolio: positions, daily valuation, performance over time | Data modelling over a time series |

Each phase is independently shippable; stopping after any phase still yields a usable increment.
