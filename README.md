# Portfolio & Watchlist Earnings Intelligence

Tracks earnings dates for a personal stock watchlist and mails the results as they are
published, distinguishing tickers held in portfolio from tickers only being watched.

Design and rationale — including why several obvious-looking choices were rejected on
evidence — live in
[`docs/adr/adr-portfolio-watchlist-earnings-intelligence.md`](docs/adr/adr-portfolio-watchlist-earnings-intelligence.md).

## How it runs

Two scheduled GitHub Actions workflows over one encrypted state file:

| Job | Cadence | What it does |
|---|---|---|
| **Calendar Sync** | weekly (Sun 03:00 UTC) | Refreshes each ticker's expected reporting date, and re-arms tickers whose date was cleared after notifying. |
| **Results Watch** | every 3 hours | Queries only tickers inside their reporting window, mails each published result, and flags dates that passed with no result. |

One mail per published result — the subject names the ticker. Overdue warnings are grouped
into a single mail, because they are maintenance signals rather than news.

## Setup

```sh
uv sync
cp .env.example .env
uv run scripts/watchlist_tool.py generate-key   # add the output to .env
```

`.env` needs:

```
PWI_WATCHLIST_KEY=...      # from generate-key
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=...     # Google account -> app passwords (needs 2FA)
NOTIFY_TO=you@gmail.com
```

The same four values must exist as repository secrets for the workflows:

```sh
gh secret set PWI_WATCHLIST_KEY
gh secret set GMAIL_ADDRESS
gh secret set GMAIL_APP_PASSWORD
gh secret set NOTIFY_TO
```

## Managing the watchlist

The watchlist names which securities are actually held, so it is stored **encrypted** as
`data/watchlist.enc` and committed in that form only. Losing `PWI_WATCHLIST_KEY` means
rebuilding the list by hand — keep a copy somewhere safe.

```sh
uv run scripts/watchlist_tool.py add UCG.MI:held RACE.MI:watching ENI.MI
uv run scripts/watchlist_tool.py remove FBK.MI
uv run scripts/watchlist_tool.py show
uv run scripts/watchlist_tool.py edit          # full JSON in $EDITOR
```

Symbols follow Yahoo's convention: Milan takes a `.MI` suffix (`UCG.MI`), US listings are
bare (`AAPL`). An unlabelled symbol defaults to `watching`. Re-adding an existing ticker
changes only its label and preserves its notification history, so moving a symbol from
`watching` to `held` after a trade never re-sends an old quarter.

## Running the jobs

```sh
uv run python -m pwi calendar-sync
uv run python -m pwi results-watch
uv run python -m pwi results-watch --dry-run   # prints mails, writes nothing
```

Or on demand in CI: **Actions → Calendar Sync / Results Watch → Run workflow**, which
offers the same dry-run switch.

## Development

```sh
uv run pytest -q
uv run pytest tests/test_digest.py -q
uv run ruff check . && uv run ruff format .
uv run scripts/smoke_test.py                   # real end-to-end, sends real mail
```

Tests touch neither the network nor credentials by design: every source is injected, and
the decision logic takes `today` as an argument. Earnings happen four times a year, so the
interesting cases — de-duplication, the reporting window, first-run adoption, a source that
went stale — are only reachable against fakes with invented dates.

## Status

Working end to end. Not built yet: the custom MCP server (Phase 3), the optional Lambda
deployment (Phase 5), and the simulated portfolio (Phase 6).

Known limitations, all measured rather than assumed:

- **`FBK.MI`, `TIT.MI`, `A2A.MI`** — Yahoo supplies forward dates but stopped publishing
  their actual EPS in 2020–2021. These produce an overdue warning naming the cause instead
  of a result, and need a manual check.
- **Revenue actuals lag** publication by weeks, so a result mail often carries the estimate
  and says so explicitly rather than staying silent.
- **The quarter label** is derived from the announcement date and is a quarter off. It is
  used only as a de-duplication key and never displayed.
- Yahoo's endpoints are undocumented and can change without notice.
