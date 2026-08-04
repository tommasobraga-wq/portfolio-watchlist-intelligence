"""The two scheduled jobs.

Orchestration only: which tickers to look at, in what order, and what to persist. Every
decision lives in `digest`, every wording in `render`, every vendor detail in `yahoo`.

Two rules shape the error handling, both aimed at the same failure — a result that happens
and never reaches the reader:

1. State is written only after a notification has actually been delivered. Stamping first
   and sending second would, on an SMTP failure, mark a quarter as notified that nobody
   ever saw, and it would never be retried.
2. One unavailable ticker never aborts the run. Yahoo throttles, and the other twenty-six
   tickers still deserve their results; the skipped one is retried on the next run.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import digest, render, watchlist
from .mailer import Mailer
from .models import TrackedTicker
from .sources import EarningsSource, PriceSource, SourceUnavailable

log = logging.getLogger("pwi")

# Yahoo rate-limits, and a scheduled runner shares its IP with many other scrapers, so
# space the calls out. The jobs are not latency-sensitive to seconds.
DELAY_BETWEEN_LOOKUPS = 1.5


@dataclass
class RunSummary:
    looked_up: int = 0
    notified: int = 0
    adopted: int = 0
    overdue: int = 0
    unavailable: int = 0
    dates_changed: int = 0

    def __str__(self) -> str:
        return (
            f"lookups={self.looked_up} notified={self.notified} adopted={self.adopted} "
            f"overdue={self.overdue} unavailable={self.unavailable} "
            f"dates_changed={self.dates_changed}"
        )


def today_utc() -> date:
    return datetime.now(UTC).date()


def calendar_sync(
    source: EarningsSource,
    path: Path = watchlist.DEFAULT_PATH,
    key: str | None = None,
    delay: float = DELAY_BETWEEN_LOOKUPS,
    persist: bool = True,
) -> RunSummary:
    """Refresh every ticker's expected reporting date.

    Runs weekly over the whole watchlist. Expected dates move often, and this is also what
    re-arms a ticker after its result was notified and the date cleared.
    """
    tickers = watchlist.load(path, key=key)
    summary = RunSummary()

    for ticker in _spaced(tickers, delay):
        try:
            earnings = source.fetch(ticker.symbol)
        except SourceUnavailable as exc:
            summary.unavailable += 1
            log.warning("%s: unavailable, will retry next run (%s)", ticker.symbol, exc)
            continue

        summary.looked_up += 1
        if earnings.next_date != ticker.next_earnings_date:
            log.info(
                "%s: expected date %s -> %s",
                ticker.symbol,
                ticker.next_earnings_date,
                earnings.next_date,
            )
            ticker.next_earnings_date = earnings.next_date
            summary.dates_changed += 1

    if persist:
        watchlist.save(tickers, path, key=key)
    else:
        log.info("dry run: state not written")
    log.info("calendar sync: %s", summary)
    return summary


def results_watch(
    source: EarningsSource,
    mailer: Mailer,
    path: Path = watchlist.DEFAULT_PATH,
    key: str | None = None,
    today: date | None = None,
    delay: float = DELAY_BETWEEN_LOOKUPS,
    persist: bool = True,
    prices: PriceSource | None = None,
) -> RunSummary:
    """Detect published results and notify.

    Runs often, but only touches tickers inside their reporting window — on a quiet day it
    makes no requests at all.
    """
    today = today or today_utc()
    tickers = watchlist.load(path, key=key)
    summary = RunSummary()
    overdue: list[digest.Decision] = []
    labels = render.labels_of(tickers)

    due = [t for t in tickers if digest.is_in_window(t.next_earnings_date, today)]
    log.info("%d of %d tickers in window", len(due), len(tickers))

    for ticker in _spaced(due, delay):
        try:
            earnings = source.fetch(ticker.symbol)
        except SourceUnavailable as exc:
            summary.unavailable += 1
            log.warning("%s: unavailable, will retry next run (%s)", ticker.symbol, exc)
            continue

        summary.looked_up += 1
        decision = digest.classify(ticker, earnings.latest, today)

        if decision.outcome is digest.Outcome.NOTIFY:
            if _announce(mailer, decision, labels[ticker.symbol], earnings, source, prices, today):
                _apply(tickers, ticker, decision)
                summary.notified += 1
        elif decision.outcome is digest.Outcome.ADOPTED:
            # No mail to fail, so the stamp is safe to write immediately.
            _apply(tickers, ticker, decision)
            summary.adopted += 1
            log.info("%s: adopted at %s without notifying", ticker.symbol, decision.report.period)
        elif decision.outcome is digest.Outcome.OVERDUE:
            overdue.append(decision)

    if overdue:
        summary.overdue = len(overdue)
        mailer.send(
            render.overdue_subject(overdue),
            render.overdue_body(overdue, labels, today=today),
        )
        log.info("overdue reported for %s", ", ".join(d.symbol for d in overdue))

    if persist:
        watchlist.save(tickers, path, key=key)
    else:
        log.info("dry run: state not written")
    log.info("results watch: %s", summary)
    return summary


def _announce(
    mailer: Mailer,
    decision: digest.Decision,
    label,
    earnings,
    source: EarningsSource,
    prices: PriceSource | None,
    today: date,
) -> bool:
    """Send one result. Returns whether it was delivered, which gates the state write.

    Everything beyond the EPS figures is enrichment: if a lookup for it fails, the section
    is dropped and the result still goes out. A missing detail must never cost the reader
    the notification itself.
    """
    try:
        revenue = source.revenue(decision.symbol)
    except SourceUnavailable:
        revenue = None

    reaction = None
    if prices is not None and decision.report and decision.report.reported_date:
        try:
            closes = prices.daily_closes(
                decision.symbol,
                decision.report.reported_date - timedelta(days=7),
                today,
            )
            reaction = digest.reaction(closes, decision.report.reported_date)
        except SourceUnavailable:
            reaction = None

    try:
        mailer.send(
            render.result_subject(decision, label),
            render.result_body(decision, label, earnings=earnings, revenue=revenue, price=reaction),
        )
    except Exception:
        # Deliberately not stamping: the quarter stays unnotified and is retried next run.
        log.exception("%s: delivery failed, leaving it unnotified for retry", decision.symbol)
        return False

    log.info("%s: notified", decision.symbol)
    return True


def _apply(tickers: list[TrackedTicker], ticker: TrackedTicker, decision: digest.Decision) -> None:
    updated = digest.apply(ticker, decision)
    tickers[tickers.index(ticker)] = updated


def _spaced(items: Iterable, delay: float):
    for index, item in enumerate(items):
        if index and delay:
            time.sleep(delay)
        yield item
