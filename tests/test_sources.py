from datetime import date
from decimal import Decimal

import pytest

from fakes import FakeEarningsSource, FakePriceSource
from pwi.models import EarningsReport, Revenue, TickerEarnings
from pwi.sources import EarningsSource, PriceSource, SourceUnavailable


def report(when: date, eps: float, estimate: float) -> EarningsReport:
    return EarningsReport("UCG.MI", when, when, eps, estimate, None)


def test_fakes_satisfy_the_protocols() -> None:
    # Structural typing: no base class, no registration — matching methods is enough.
    assert isinstance(FakeEarningsSource(), EarningsSource)
    assert isinstance(FakePriceSource(), PriceSource)


def test_unknown_symbol_is_empty_not_an_error() -> None:
    result = FakeEarningsSource().fetch("NOPE")

    assert result.latest is None
    assert result.next_date is None


def test_unavailable_is_distinct_from_no_data() -> None:
    # A throttled source must not look like "this ticker has never reported": one is
    # retryable, the other is fact.
    with pytest.raises(SourceUnavailable):
        FakeEarningsSource(unavailable={"UCG.MI"}).fetch("UCG.MI")


def test_latest_is_the_first_published_quarter() -> None:
    earnings = TickerEarnings(
        "UCG.MI",
        next_date=date(2026, 10, 22),
        published=(report(date(2026, 7, 23), 1.80, 1.79), report(date(2026, 5, 5), 2.09, 1.85)),
    )

    assert earnings.latest.reported_date == date(2026, 7, 23)


class TestTrackRecord:
    def test_counts_beats_over_the_recent_window(self) -> None:
        earnings = TickerEarnings(
            "UCG.MI",
            published=(
                report(date(2026, 7, 23), 1.80, 1.79),  # beat
                report(date(2026, 5, 5), 2.09, 1.85),  # beat
                report(date(2026, 2, 9), 1.09, 1.21),  # miss
                report(date(2025, 10, 22), 1.68, 1.59),  # beat
                report(date(2025, 7, 23), 2.03, 1.62),  # beyond the window
            ),
        )

        assert earnings.track_record() == (3, 4)

    def test_quarters_missing_either_figure_are_not_comparable(self) -> None:
        earnings = TickerEarnings(
            "X",
            published=(
                EarningsReport("X", date(2026, 7, 1), date(2026, 7, 1), 1.0, None, None),
                report(date(2026, 4, 1), 2.0, 1.0),
            ),
        )

        assert earnings.track_record() == (1, 1)


def test_revenue_defaults_to_unknown_rather_than_zero() -> None:
    revenue = FakeEarningsSource().revenue("UCG.MI")

    assert revenue == Revenue(actual=None, estimate=None, currency=None)


def test_price_source_clips_to_range_and_keeps_gaps() -> None:
    series = {
        date(2026, 8, 3): Decimal("81.70"),
        # 2026-08-04 deliberately absent — a gap must stay a gap, never be interpolated.
        date(2026, 8, 5): Decimal("84.71"),
    }

    closes = FakePriceSource({"UCG.MI": series}).daily_closes(
        "UCG.MI", date(2026, 8, 3), date(2026, 8, 4)
    )

    assert closes == {date(2026, 8, 3): Decimal("81.70")}
