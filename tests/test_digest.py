"""The logic that cannot be tested against the live source, tested against invented dates."""

from datetime import date
from decimal import Decimal

import pytest

from pwi.digest import (
    Outcome,
    apply,
    classify,
    is_in_window,
    is_source_stale,
    reaction,
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


class TestSourceWithAStaleHistory:
    """FBK.MI, TIT.MI and A2A.MI: Yahoo supplies forward dates but stopped publishing the
    actuals years ago.

    The trap this class pins down: after adoption, `last_notified_period` equals the period
    of that years-old report. Testing "have we already notified this period?" therefore
    answers yes and suppresses the alert — leaving total silence exactly where the reader
    most needs telling. The test must be whether a result exists for *this* cycle.
    """

    STALE = EarningsReport("FBK.MI", date(2021, 11, 9), date(2021, 11, 9), 0.11, 0.11, None)

    def ticker(self) -> TrackedTicker:
        return TrackedTicker("FBK.MI", Label.HELD, date(2026, 11, 5), self.STALE.period)

    def test_a_years_old_report_does_not_suppress_the_alert(self) -> None:
        decision = classify(self.ticker(), self.STALE, date(2026, 11, 9))

        assert decision.outcome is Outcome.OVERDUE
        assert decision.days_overdue == 4

    def test_the_alert_carries_what_the_source_last_had(self) -> None:
        decision = classify(self.ticker(), self.STALE, date(2026, 11, 9))

        assert decision.source_last_seen == date(2021, 11, 9)
        assert is_source_stale(decision, date(2026, 11, 9)) is True

    def test_a_recent_gap_is_not_blamed_on_the_source(self) -> None:
        # A company that reported last quarter and is merely late this time.
        recent = EarningsReport("UCG.MI", date(2026, 7, 23), date(2026, 7, 23), 1.8, 1.79, 0.55)
        ticker = TrackedTicker("UCG.MI", Label.HELD, date(2026, 10, 22), recent.period)

        decision = classify(ticker, recent, date(2026, 10, 26))

        assert decision.outcome is Outcome.OVERDUE
        assert is_source_stale(decision, date(2026, 10, 26)) is False

    def test_a_result_for_this_cycle_ends_the_alert(self) -> None:
        published = EarningsReport("FBK.MI", date(2026, 11, 5), date(2026, 11, 5), 0.3, 0.3, None)
        ticker = TrackedTicker("FBK.MI", Label.HELD, date(2026, 11, 5), published.period)

        decision = classify(ticker, published, date(2026, 11, 9))

        assert decision.outcome is Outcome.ALREADY_NOTIFIED

    def test_publishing_slightly_early_still_counts(self) -> None:
        early = EarningsReport("FBK.MI", date(2026, 11, 4), date(2026, 11, 4), 0.3, 0.3, None)
        ticker = TrackedTicker("FBK.MI", Label.HELD, date(2026, 11, 5), early.period)

        assert classify(ticker, early, date(2026, 11, 9)).outcome is Outcome.ALREADY_NOTIFIED


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


class TestPriceReaction:
    """Publication-day move, computed over closes that have gaps by nature."""

    CLOSES = {
        date(2026, 7, 21): Decimal("82.62"),
        date(2026, 7, 22): Decimal("83.21"),  # last close before publication
        date(2026, 7, 23): Decimal("79.22"),  # publication day
        date(2026, 7, 24): Decimal("80.43"),
        date(2026, 8, 4): Decimal("85.17"),  # latest
    }

    def test_measures_from_the_close_before_publication(self) -> None:
        r = reaction(self.CLOSES, date(2026, 7, 23))

        assert r.before == Decimal("83.21")
        assert r.on_day == Decimal("79.22")
        assert round(r.day_move_pct, 2) == -4.8

    def test_measures_the_drift_since(self) -> None:
        r = reaction(self.CLOSES, date(2026, 7, 23))

        assert r.latest == Decimal("85.17")
        assert round(r.since_move_pct, 2) == 7.51

    def test_a_weekend_publication_uses_the_next_session(self) -> None:
        # Reporting after Friday's close: the reaction shows up on Monday, and the
        # reference is still Friday. Missing days are absent, not zero.
        r = reaction(self.CLOSES, date(2026, 7, 23))
        weekend = reaction(
            {date(2026, 7, 24): Decimal("80.43"), date(2026, 7, 22): Decimal("83.21")},
            date(2026, 7, 23),
        )

        assert weekend.before == Decimal("83.21")
        assert weekend.on_day == Decimal("80.43")
        assert r.on_day != weekend.on_day

    def test_no_prices_yields_nothing_rather_than_zero(self) -> None:
        # A zero would read as "the stock did not move", which is a claim we cannot make.
        r = reaction({}, date(2026, 7, 23))

        assert r.day_move_pct is None and r.before is None

    def test_no_close_before_publication_yields_no_move(self) -> None:
        r = reaction({date(2026, 7, 23): Decimal("79.22")}, date(2026, 7, 23))

        assert r.day_move_pct is None
        assert r.on_day == Decimal("79.22")

    def test_an_unknown_publication_date_yields_nothing(self) -> None:
        assert reaction(self.CLOSES, None).day_move_pct is None
