"""The logic that cannot be tested against the live source, tested against invented dates."""

from datetime import date

import pytest

from pwi.digest import (
    Outcome,
    apply,
    classify,
    is_in_window,
    to_report,
)
from pwi.models import EarningsReport, Label, TrackedTicker

TODAY = date(2026, 10, 22)


def ticker(next_date: date | None = TODAY, notified: str | None = None) -> TrackedTicker:
    return TrackedTicker("UCG.MI", Label.HELD, next_date, notified)


def report(reported_on: date = TODAY, eps: float = 1.80) -> EarningsReport:
    return EarningsReport(
        symbol="UCG.MI",
        fiscal_date_ending=reported_on,
        reported_date=reported_on,
        reported_eps=eps,
        estimated_eps=1.79,
        surprise_percentage=0.55,
    )


class TestWindow:
    @pytest.mark.parametrize(
        "today,expected",
        [
            (date(2026, 10, 19), False),  # 3 days early: outside
            (date(2026, 10, 20), True),  # 2 days early: opens
            (date(2026, 10, 22), True),  # the day itself
            (date(2026, 11, 1), True),  # 10 days late: still open, dates slip
            (date(2026, 11, 2), False),  # 11 days late: closed
        ],
    )
    def test_window_is_asymmetric(self, today: date, expected: bool) -> None:
        assert is_in_window(TODAY, today) is expected

    def test_no_date_means_no_lookup(self) -> None:
        # This is what keeps the job free on quiet days.
        assert is_in_window(None, TODAY) is False


class TestFirstSighting:
    def test_old_history_is_absorbed_silently(self) -> None:
        # Otherwise adding 15 tickers would fire 15 notifications about stale quarters.
        decision = classify(ticker(notified=None), report(reported_on=date(2026, 7, 23)), TODAY)

        assert decision.outcome is Outcome.ADOPTED

    def test_but_a_fresh_result_is_still_news(self) -> None:
        # A ticker added days before it reports must not have its first result swallowed.
        decision = classify(ticker(notified=None), report(reported_on=date(2026, 10, 21)), TODAY)

        assert decision.outcome is Outcome.NOTIFY

    def test_adoption_still_stamps_the_period(self) -> None:
        decision = classify(ticker(notified=None), report(reported_on=date(2026, 7, 23)), TODAY)

        assert apply(ticker(notified=None), decision).last_notified_period == "2026-Q3"


class TestDeduplication:
    def test_same_quarter_is_not_sent_twice(self) -> None:
        published = report()
        decision = classify(ticker(notified=published.period), published, TODAY)

        assert decision.outcome is Outcome.ALREADY_NOTIFIED

    def test_a_new_quarter_is_sent(self) -> None:
        decision = classify(ticker(notified="2026-Q2"), report(), TODAY)

        assert decision.outcome is Outcome.NOTIFY

    def test_notifying_clears_the_expected_date(self) -> None:
        # Left in place it would keep the ticker in-window, re-queried daily for nothing.
        decision = classify(ticker(notified="2026-Q2"), report(), TODAY)
        updated = apply(ticker(notified="2026-Q2"), decision)

        assert updated.next_earnings_date is None
        # Derived from the announcement date, not the fiscal period — a stable de-dup key
        # only, never shown to the reader. See the ADR.
        assert updated.last_notified_period == "2026-Q4"


class TestSilence:
    def test_before_the_date_silence_is_normal(self) -> None:
        decision = classify(ticker(next_date=date(2026, 10, 23)), None, TODAY)

        assert decision.outcome is Outcome.NOT_YET_PUBLISHED

    def test_prolonged_silence_becomes_reportable(self) -> None:
        # The FBK.MI case: the source has the date but never publishes the result. Staying
        # quiet would be indistinguishable from working correctly.
        decision = classify(ticker(next_date=date(2026, 10, 19)), None, TODAY)

        assert decision.outcome is Outcome.OVERDUE
        assert decision.days_overdue == 3
        assert decision.expected_on == date(2026, 10, 19)

    def test_overdue_does_not_fire_once_already_notified(self) -> None:
        published = report()
        decision = classify(
            ticker(next_date=date(2026, 10, 19), notified=published.period), published, TODAY
        )

        assert decision.outcome is Outcome.ALREADY_NOTIFIED

    def test_nothing_is_stamped_when_there_is_no_news(self) -> None:
        original = ticker(next_date=date(2026, 10, 23))
        decision = classify(original, None, TODAY)

        assert apply(original, decision) == original


class TestInstrumentsThatNeverReport:
    """ETFs are legitimate holdings but publish no earnings.

    The watchlist doubles as the portfolio list, so such entries are permanent. They must
    stay completely silent: never notified, and never flagged as overdue — an alert every
    few days about an instrument that has nothing to report would train the reader to
    ignore the alerts that matter.
    """

    def test_no_expected_date_means_no_lookup(self) -> None:
        assert is_in_window(None, TODAY) is False

    def test_never_notifies(self) -> None:
        etf = TrackedTicker("GOAI-EUR.MI", Label.HELD, next_earnings_date=None)

        assert classify(etf, None, TODAY).outcome is Outcome.NOT_YET_PUBLISHED

    def test_never_goes_overdue(self) -> None:
        # OVERDUE needs an expected date; without one it can never trigger.
        etf = TrackedTicker("GOAI-EUR.MI", Label.HELD, next_earnings_date=None)

        decision = classify(etf, None, TODAY)

        assert decision.outcome is not Outcome.OVERDUE
        assert to_report([decision]) == ([], [])

    def test_state_is_left_untouched(self) -> None:
        etf = TrackedTicker("GOAI-EUR.MI", Label.HELD)

        assert apply(etf, classify(etf, None, TODAY)) == etf


def test_report_splits_announcements_from_faults() -> None:
    decisions = [
        classify(ticker(notified="2026-Q2"), report(), TODAY),
        classify(TrackedTicker("FBK.MI", Label.WATCHING, date(2026, 10, 15)), None, TODAY),
        classify(ticker(next_date=date(2026, 10, 30)), None, TODAY),
    ]

    published, overdue = to_report(decisions)

    assert [d.symbol for d in published] == ["UCG.MI"]
    assert [d.symbol for d in overdue] == ["FBK.MI"]
