#!/usr/bin/env python
"""End-to-end check against the real source and real SMTP.

The unit suite proves the logic; this proves the wiring — that Yahoo answers, that the
credentials work, that the mail renders and arrives. Those are exactly the things that
break silently between one earnings season and the next.

It works on a **copy** of the watchlist and forces the two states that are otherwise only
reachable during a reporting window:

    uv run scripts/smoke_test.py            # both scenarios
    uv run scripts/smoke_test.py --overdue  # only the overdue warning
    uv run scripts/smoke_test.py --result   # only a published result

The real `data/watchlist.enc` is never written.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import warnings
from datetime import timedelta
from pathlib import Path

from pwi import jobs, vault, watchlist
from pwi.config import load_environment
from pwi.mailer import Mailer
from pwi.models import Label, TrackedTicker
from pwi.sources import SourceUnavailable
from pwi.yahoo import YahooEarnings

# Old enough that any real ticker's latest quarter differs from it, which is what makes
# classify() treat the newest published result as news and send it.
FORCED_STALE_PERIOD = "2000-Q1"


def pick_reporting_ticker(source: YahooEarnings, candidates: list[TrackedTicker]) -> TrackedTicker:
    """The first ticker that actually has a published result to talk about."""
    for ticker in candidates:
        try:
            if source.fetch(ticker.symbol).latest is not None:
                return ticker
        except SourceUnavailable:
            continue
    raise SystemExit("no ticker in the watchlist has any published result — is Yahoo reachable?")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", action="store_true", help="only the published-result mail")
    parser.add_argument("--overdue", action="store_true", help="only the overdue warning")
    args = parser.parse_args(argv)
    both = not (args.result or args.overdue)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    warnings.filterwarnings("ignore")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    load_environment()
    key = vault.key_from_env()
    mailer = Mailer.from_env()
    source = YahooEarnings()
    today = jobs.today_utc()

    real = watchlist.load(key=key)
    print(f"watchlist has {len(real)} tickers; the real file will not be modified\n")

    scenario: list[TrackedTicker] = []

    if both or args.result:
        subject = pick_reporting_ticker(source, real)
        # Due today and never notified for this period: the newest published quarter
        # therefore reads as news.
        scenario.append(TrackedTicker(subject.symbol, subject.label, today, FORCED_STALE_PERIOD))
        print(f"result scenario   -> {subject.symbol}")

    if both or args.overdue:
        # A date well in the past on a symbol with nothing to report: the source cannot
        # satisfy it, which is precisely the overdue case.
        scenario.append(
            TrackedTicker("SMOKE-TEST.MI", Label.WATCHING, today - timedelta(days=6), None)
        )
        print("overdue scenario  -> SMOKE-TEST.MI (deliberately unknown symbol)")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "watchlist.enc"
        watchlist.save(scenario, path, key=key)
        print()
        summary = jobs.results_watch(source, mailer, path, key=key, today=today, delay=1.5)

    print(f"\n{summary}")
    print("check the inbox — and that nothing landed in spam")
    return 0


if __name__ == "__main__":
    # Guard against ever being pointed at the real file by a future edit.
    assert watchlist.DEFAULT_PATH.name == "watchlist.enc"
    shutil.rmtree(Path(tempfile.gettempdir()) / "pwi-smoke", ignore_errors=True)
    raise SystemExit(main())
