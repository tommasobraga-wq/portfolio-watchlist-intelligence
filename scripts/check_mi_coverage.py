#!/usr/bin/env python
"""Phase 1: does Alpha Vantage cover Milan-listed tickers?

The ADR flags this as an open risk to validate before the architecture commits to Alpha
Vantage as the earnings source. Run it against a handful of .MI tickers and record the
answer in the ADR.

    ALPHAVANTAGE_API_KEY=... uv run scripts/check_mi_coverage.py UCG.MI FBK.MI

Each symbol costs one request against the 25/day free-tier quota.
"""

from __future__ import annotations

import os
import sys

from pwi.alphavantage import AlphaVantageClient, RateLimited

DEFAULT_SYMBOLS = ["UCG.MI", "FBK.MI", "RACE.MI", "PRY.MI"]


def main(argv: list[str]) -> int:
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not api_key:
        print("ALPHAVANTAGE_API_KEY is not set", file=sys.stderr)
        return 2

    symbols = argv[1:] or DEFAULT_SYMBOLS
    with AlphaVantageClient(api_key) as client:
        for symbol in symbols:
            try:
                quarters = client.earnings(symbol)
            except RateLimited as exc:
                print(f"quota exhausted, stopping: {exc}", file=sys.stderr)
                return 1

            if not quarters:
                print(f"{symbol}: NO COVERAGE")
                continue

            latest = quarters[0]
            print(
                f"{symbol}: {len(quarters)} quarters, latest {latest.period} "
                f"reported {latest.reported_date} EPS {latest.reported_eps} "
                f"(est. {latest.estimated_eps})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
