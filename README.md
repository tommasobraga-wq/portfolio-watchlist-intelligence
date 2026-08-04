# Portfolio & Watchlist Earnings Intelligence

Tracks earnings dates for a personal stock watchlist and notifies when actual results are
published, distinguishing tickers held in portfolio from tickers only being watched.

Design and rationale: [`docs/adr/adr-portfolio-watchlist-earnings-intelligence.md`](docs/adr/adr-portfolio-watchlist-earnings-intelligence.md).

## Setup

```sh
uv sync
cp .env.example .env          # add your Alpha Vantage key
cp data/watchlist.example.json data/watchlist.json
```

## Commands

```sh
uv run pytest                                  # all tests
uv run pytest tests/test_alphavantage.py::test_parses_quarters   # a single test
uv run ruff check .                            # lint
uv run ruff format .                           # format
```

## Phase 1 — validate the earnings source

Alpha Vantage's coverage of Milan-listed tickers is unverified; the ADR treats it as an
open risk to settle before the architecture commits to it:

```sh
uv run scripts/check_mi_coverage.py UCG.MI FBK.MI
```

Each symbol costs one request against the 25/day free-tier quota. Record the outcome in
the ADR.

## Status

Phase 1. Implemented so far: the tracked-ticker record, the JSON watchlist store, and the
direct Alpha Vantage REST client with quota handling. The Calendar Sync and Results Watch
jobs, the custom MCP server, and the scheduled deployment are not built yet.
