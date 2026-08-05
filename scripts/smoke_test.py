#!/usr/bin/env python
"""End-to-end check against the real source and real SMTP.

The unit suite proves the logic; this proves the wiring — that Yahoo answers, that the
credentials work, that the mail renders and arrives. Those are exactly the things that
break silently between one earnings season and the next.

It works on a **copy** of the watchlist and forces the states that are otherwise only
reachable during a reporting window or on an actual distribution:

    uv run scripts/smoke_test.py             # result + overdue (unchanged default)
    uv run scripts/smoke_test.py --overdue   # only the overdue warning
    uv run scripts/smoke_test.py --result    # only a published result
    uv run scripts/smoke_test.py --dividend  # only the dividend distribution mail

`--dividend` is opt-in only — it is not part of the no-argument default, so an existing
habit of running this with no flags keeps sending exactly the two mails it always has.

The real `data/watchlist.enc` is never written.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import warnings
from datetime import date, timedelta
from pathlib import Path

from pwi import jobs, vault, watchlist
from pwi.config import load_environment
from pwi.mailer import Mailer
from pwi.models import Label, TrackedTicker
from pwi.sources import SourceUnavailable
from pwi.yahoo import YahooDividends, YahooEarnings

# Old enough that any real ticker's latest quarter differs from it, which is what makes
# classify() treat the newest published result as news and send it.
FORCED_STALE_PERIOD = "2000-Q1"

# Old enough that any real ticker's latest dividend post-dates it, which is what makes
# classify_dividend() treat that dividend as news rather than a first-sighting adoption.
FORCED_STALE_DIVIDEND_DATE = date(2000, 1, 1)


def pick_reporting_ticker(source: YahooEarnings, candidates: list[TrackedTicker]) -> TrackedTicker:
    """The first ticker that actually has a published result to talk about."""
    for ticker in candidates:
        try:
            if source.fetch(ticker.symbol).latest is not None:
                return ticker
        except SourceUnavailable:
            continue
    raise SystemExit("no ticker in the watchlist has any published result — is Yahoo reachable?")


def pick_dividend_ticker(source: YahooDividends, candidates: list[TrackedTicker]) -> TrackedTicker:
    """The first held ticker that actually has dividend history to talk about.

    Held only, mirroring dividend_watch itself: a watching-only ticker is never looked up
    by the real job, so forcing one here would test a code path that never runs in
    production.
    """
    for ticker in candidates:
        if ticker.label is not Label.HELD:
            continue
        try:
            if source.history(ticker.symbol):
                return ticker
        except SourceUnavailable:
            continue
    raise SystemExit(
        "no held ticker in the watchlist has any dividend history — is Yahoo reachable?"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", action="store_true", help="only the published-result mail")
    parser.add_argument("--overdue", action="store_true", help="only the overdue warning")
    parser.add_argument(
        "--dividend", action="store_true", help="only the dividend distribution mail"
    )
    args = parser.parse_args(argv)
    # Unchanged from before --dividend existed: no flags means result + overdue, exactly as
    # documented. --dividend is never implied by the bare default — see the module docstring.
    both = not (args.result or args.overdue or args.dividend)

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

    results_scenario: list[TrackedTicker] = []
    dividend_scenario: list[TrackedTicker] = []

    if both or args.result:
        subject = pick_reporting_ticker(source, real)
        # Due today and never notified for this period: the newest published quarter
        # therefore reads as news.
        results_scenario.append(
            TrackedTicker(subject.symbol, subject.label, today, FORCED_STALE_PERIOD)
        )
        print(f"result scenario    -> {subject.symbol}")

    if both or args.overdue:
        # A date well in the past on a symbol with nothing to report: the source cannot
        # satisfy it, which is precisely the overdue case.
        results_scenario.append(
            TrackedTicker("SMOKE-TEST.MI", Label.WATCHING, today - timedelta(days=6), None)
        )
        print("overdue scenario   -> SMOKE-TEST.MI (deliberately unknown symbol)")

    dividend_source = YahooDividends()
    if args.dividend:
        subject = pick_dividend_ticker(dividend_source, real)
        # Notified before the ticker's actual latest dividend: classify_dividend() then
        # reads that dividend as unseen news rather than a first-sighting adoption, which
        # is the only way to reach NOTIFY without waiting for a real distribution.
        dividend_scenario.append(
            TrackedTicker(
                subject.symbol,
                subject.label,
                last_notified_dividend_date=FORCED_STALE_DIVIDEND_DATE,
            )
        )
        print(f"dividend scenario  -> {subject.symbol}")

    with tempfile.TemporaryDirectory() as tmp:
        print()
        if results_scenario:
            path = Path(tmp) / "watchlist-results.enc"
            watchlist.save(results_scenario, path, key=key)
            summary = jobs.results_watch(source, mailer, path, key=key, today=today, delay=1.5)
            print(f"\n{summary}")
        if dividend_scenario:
            # Its own file, not shared with results_scenario: the two jobs would otherwise
            # risk the same symbol appearing twice if a ticker happened to have both a
            # forced result and a forced dividend pending.
            path = Path(tmp) / "watchlist-dividend.enc"
            watchlist.save(dividend_scenario, path, key=key)
            div_summary = jobs.dividend_watch(
                dividend_source, mailer, path, key=key, today=today, delay=1.5
            )
            print(f"\n{div_summary}")

    print("check the inbox — and that nothing landed in spam")
    return 0


if __name__ == "__main__":
    # Guard against ever being pointed at the real file by a future edit.
    assert watchlist.DEFAULT_PATH.name == "watchlist.enc"
    shutil.rmtree(Path(tempfile.gettempdir()) / "pwi-smoke", ignore_errors=True)
    raise SystemExit(main())
