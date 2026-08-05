"""Decision logic. Pure: no network, no clock, no mail.

Everything interesting about this system lives here — which tickers are worth querying,
whether a result is genuinely new, and when silence has gone on long enough to be a fault.
None of it can be exercised against the live source (earnings happen four times a year), so
it takes `today` as an argument and is tested against fakes with invented dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from .models import Dividend, EarningsReport, TrackedTicker

# Expected dates slip forward far more often than backward, so the window is asymmetric.
WINDOW_OPENS_DAYS_BEFORE = 2
WINDOW_CLOSES_DAYS_AFTER = 10

# How long after the expected date silence stops being normal and becomes reportable.
OVERDUE_AFTER_DAYS = 3

# Beyond this, the newest result the source holds is not "recent history" but evidence that
# the source stopped publishing figures for this security. Yahoo does exactly that for some
# Milan mid-caps: it keeps supplying forward dates while the actuals stop years earlier.
STALE_HISTORY_DAYS = 400

# A result found on a ticker we have never notified before is treated as genuinely new if
# it is this recent; older than that, it is history and gets absorbed silently.
FRESH_RESULT_DAYS = 3

# Same bootstrap problem as FRESH_RESULT_DAYS, sized for dividends instead: Yahoo's
# historical series was observed to lag the ex-dividend date by up to five days (JEPI: 2
# days; O: 5 days, both confirmed present), so a shorter window would risk treating a
# genuinely fresh distribution as old history on a newly tracked ticker.
DIVIDEND_FRESH_DAYS = 10


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
    # On OVERDUE: the newest result the source holds, if any. When that is years old the
    # cause is a gap in the source rather than a company delaying, and saying so turns a
    # vague "check manually" into a specific, actionable statement.
    source_last_seen: date | None = None


@dataclass(frozen=True, slots=True)
class PriceReaction:
    """How the market took the result. Any field may be None when prices are missing."""

    before: Decimal | None = None
    on_day: Decimal | None = None
    latest: Decimal | None = None
    day_move_pct: float | None = None
    since_move_pct: float | None = None


def reaction(closes: dict[date, Decimal], published_on: date | None) -> PriceReaction:
    """Compare the close on the day of publication with the one before, and with today.

    Beating an estimate and falling anyway is common and not deducible from EPS, so the
    move is worth stating. Gaps are respected: the reference is the last close *before*
    publication, whatever day that was — weekends and holidays are simply not there.
    """
    if not closes or published_on is None:
        return PriceReaction()

    earlier = [d for d in closes if d < published_on]
    before = closes[max(earlier)] if earlier else None
    # A company reporting before the open moves the price that same session; one reporting
    # after the close moves it the next. Taking the first close on or after publication
    # covers both without needing to know which it was.
    on_or_after = [d for d in closes if d >= published_on]
    on_day = closes[min(on_or_after)] if on_or_after else None
    latest = closes[max(closes)]

    return PriceReaction(
        before=before,
        on_day=on_day,
        latest=latest,
        day_move_pct=_pct(before, on_day),
        since_move_pct=_pct(on_day, latest),
    )


def _pct(start: Decimal | None, end: Decimal | None) -> float | None:
    if start is None or end is None or start == 0:
        return None
    return float((end - start) / start * 100)


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

    if expected is not None and (today - expected).days >= OVERDUE_AFTER_DAYS:
        # The test is whether a result exists *for this reporting cycle*, not whether we
        # have ever notified anything. Asking the latter suppresses the alert exactly where
        # it is needed most: a source stuck on a years-old quarter matches
        # `last_notified_period`, which would read as "already handled" and stay silent.
        if not _covers_cycle(report, expected):
            return Decision(
                ticker.symbol,
                Outcome.OVERDUE,
                report=report,
                expected_on=expected,
                days_overdue=(today - expected).days,
                source_last_seen=report.reported_date if report else None,
            )

    outcome = Outcome.ALREADY_NOTIFIED if already else Outcome.NOT_YET_PUBLISHED
    return Decision(ticker.symbol, outcome, report=report, expected_on=expected)


def _covers_cycle(report: EarningsReport | None, expected: date) -> bool:
    """Whether this result belongs to the reporting round that was expected on `expected`.

    A company may publish slightly early, so anything from the window opening onwards
    counts; anything before it belongs to an earlier quarter.
    """
    if report is None or report.reported_date is None:
        return False
    return report.reported_date >= expected - timedelta(days=WINDOW_OPENS_DAYS_BEFORE)


def is_source_stale(decision: Decision, today: date) -> bool:
    """Whether the gap is the source's rather than the company's."""
    if decision.source_last_seen is None:
        return False
    return (today - decision.source_last_seen).days > STALE_HISTORY_DAYS


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
        last_notified_dividend_date=ticker.last_notified_dividend_date,
    )


def to_report(decisions: list[Decision]) -> tuple[list[Decision], list[Decision]]:
    """Split decisions into what to announce and what to flag. Everything else is silence."""
    published = [d for d in decisions if d.outcome is Outcome.NOTIFY]
    overdue = [d for d in decisions if d.outcome is Outcome.OVERDUE]
    return published, overdue


class DividendOutcome(StrEnum):
    NOTIFY = "notify"
    NOTHING_NEW = "nothing_new"
    ADOPTED = "adopted"  # first sighting, recorded without notifying


@dataclass(frozen=True, slots=True)
class DividendDecision:
    symbol: str
    outcome: DividendOutcome
    dividend: Dividend | None = None


def classify_dividend(
    ticker: TrackedTicker, history: tuple[Dividend, ...], today: date
) -> DividendDecision:
    """Decide what to do about the newest dividend a source reports, if any.

    No window, no overdue concept — unlike earnings, a dividend is never "expected and
    missing": a company simply hasn't stacked one yet. The only question is whether the
    newest entry in `history` is one we have already notified.
    """
    if not history:
        return DividendDecision(ticker.symbol, DividendOutcome.NOTHING_NEW)

    latest = max(history, key=lambda d: d.ex_date)

    if (
        ticker.last_notified_dividend_date is not None
        and latest.ex_date <= ticker.last_notified_dividend_date
    ):
        return DividendDecision(ticker.symbol, DividendOutcome.NOTHING_NEW, dividend=latest)

    if ticker.last_notified_dividend_date is None:
        # First time this ticker's dividend history has been looked at. Absorb it quietly
        # unless the newest entry is recent enough to be real news — the same first-run
        # bootstrap problem `classify` solves for earnings, via FRESH_RESULT_DAYS there.
        if (today - latest.ex_date).days <= DIVIDEND_FRESH_DAYS:
            return DividendDecision(ticker.symbol, DividendOutcome.NOTIFY, dividend=latest)
        return DividendDecision(ticker.symbol, DividendOutcome.ADOPTED, dividend=latest)

    return DividendDecision(ticker.symbol, DividendOutcome.NOTIFY, dividend=latest)


def apply_dividend(ticker: TrackedTicker, decision: DividendDecision) -> TrackedTicker:
    """The state change a dividend decision implies. Returns a new record; does not mutate."""
    if decision.outcome not in (DividendOutcome.NOTIFY, DividendOutcome.ADOPTED):
        return ticker
    assert decision.dividend is not None
    return TrackedTicker(
        symbol=ticker.symbol,
        label=ticker.label,
        next_earnings_date=ticker.next_earnings_date,
        last_notified_period=ticker.last_notified_period,
        last_notified_dividend_date=decision.dividend.ex_date,
    )
