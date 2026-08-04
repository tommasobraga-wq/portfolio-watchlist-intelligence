"""Decision logic. Pure: no network, no clock, no mail.

Everything interesting about this system lives here — which tickers are worth querying,
whether a result is genuinely new, and when silence has gone on long enough to be a fault.
None of it can be exercised against the live source (earnings happen four times a year), so
it takes `today` as an argument and is tested against fakes with invented dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from .models import EarningsReport, TrackedTicker

# Expected dates slip forward far more often than backward, so the window is asymmetric.
WINDOW_OPENS_DAYS_BEFORE = 2
WINDOW_CLOSES_DAYS_AFTER = 10

# How long after the expected date silence stops being normal and becomes reportable.
OVERDUE_AFTER_DAYS = 3

# A result found on a ticker we have never notified before is treated as genuinely new if
# it is this recent; older than that, it is history and gets absorbed silently.
FRESH_RESULT_DAYS = 3


class Outcome(StrEnum):
    NOTIFY = "notify"
    ALREADY_NOTIFIED = "already_notified"
    NOT_YET_PUBLISHED = "not_yet_published"
    OVERDUE = "overdue"
    ADOPTED = "adopted"  # first sighting, recorded without notifying


@dataclass(frozen=True, slots=True)
class Decision:
    symbol: str
    outcome: Outcome
    report: EarningsReport | None = None
    expected_on: date | None = None
    days_overdue: int | None = None


def is_in_window(next_earnings_date: date | None, today: date) -> bool:
    """Whether a ticker is worth spending a lookup on today.

    This is what keeps the daily job cheap: outside the window it makes no calls at all.
    """
    if next_earnings_date is None:
        return False
    opens = next_earnings_date - timedelta(days=WINDOW_OPENS_DAYS_BEFORE)
    closes = next_earnings_date + timedelta(days=WINDOW_CLOSES_DAYS_AFTER)
    return opens <= today <= closes


def classify(ticker: TrackedTicker, report: EarningsReport | None, today: date) -> Decision:
    """Decide what to do about one ticker, given whatever the source returned for it."""
    expected = ticker.next_earnings_date

    if report is None or report.period == ticker.last_notified_period:
        # Nothing published, or the newest published quarter is one we already sent. Either
        # way there is no news — but if the expected date has come and gone, the absence is
        # itself worth reporting rather than sitting silent (see FBK.MI in the ADR).
        return _silence(ticker, report, today, expected)

    if ticker.last_notified_period is None:
        # First time we have ever seen this ticker report. Absorb its history quietly so a
        # newly tracked symbol does not fire off a notification about an old quarter —
        # unless the result is fresh, in which case it is real news and must not be lost.
        if report.reported_date and today - report.reported_date <= timedelta(
            days=FRESH_RESULT_DAYS
        ):
            return Decision(ticker.symbol, Outcome.NOTIFY, report=report)
        return Decision(ticker.symbol, Outcome.ADOPTED, report=report)

    return Decision(ticker.symbol, Outcome.NOTIFY, report=report)


def _silence(
    ticker: TrackedTicker,
    report: EarningsReport | None,
    today: date,
    expected: date | None,
) -> Decision:
    already = report is not None and report.period == ticker.last_notified_period
    if expected is not None:
        overdue_by = (today - expected).days
        if overdue_by >= OVERDUE_AFTER_DAYS and not already:
            return Decision(
                ticker.symbol,
                Outcome.OVERDUE,
                report=report,
                expected_on=expected,
                days_overdue=overdue_by,
            )
    outcome = Outcome.ALREADY_NOTIFIED if already else Outcome.NOT_YET_PUBLISHED
    return Decision(ticker.symbol, outcome, report=report, expected_on=expected)


def apply(ticker: TrackedTicker, decision: Decision) -> TrackedTicker:
    """The state change a decision implies. Returns a new record; does not mutate.

    On NOTIFY and ADOPTED the period is stamped so it is never sent twice, and the expected
    date is cleared: it now points at a quarter already reported, so leaving it would keep
    the ticker inside its window being pointlessly re-queried until the next Calendar Sync.
    """
    if decision.outcome not in (Outcome.NOTIFY, Outcome.ADOPTED) or decision.report is None:
        return ticker
    return TrackedTicker(
        symbol=ticker.symbol,
        label=ticker.label,
        next_earnings_date=None,
        last_notified_period=decision.report.period,
    )


def to_report(decisions: list[Decision]) -> tuple[list[Decision], list[Decision]]:
    """Split decisions into what to announce and what to flag. Everything else is silence."""
    published = [d for d in decisions if d.outcome is Outcome.NOTIFY]
    overdue = [d for d in decisions if d.outcome is Outcome.OVERDUE]
    return published, overdue
