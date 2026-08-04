from datetime import date
from decimal import Decimal

import pytest

from fakes import FakeEarningsSource, FakePriceSource
from pwi.models import EarningsReport
from pwi.sources import EarningsSource, PriceSource, SourceUnavailable

REPORT = EarningsReport(
    symbol="UCG.MI",
    fiscal_date_ending=date(2026, 6, 30),
    reported_date=date(2026, 7, 23),
    reported_eps=1.80,
    estimated_eps=1.79,
    surprise_percentage=0.55,
)


def test_fakes_satisfy_the_protocols() -> None:
    # Structural typing: no base class, no registration — matching methods is enough.
    assert isinstance(FakeEarningsSource(), EarningsSource)
    assert isinstance(FakePriceSource(), PriceSource)


def test_no_data_is_none_not_an_error() -> None:
    source = FakeEarningsSource()

    assert source.next_earnings_date("UCG.MI") is None
    assert source.latest_report("UCG.MI") is None


def test_unavailable_is_distinct_from_no_data() -> None:
    # A throttled source must not look like "this ticker has never reported": one is
    # retryable, the other is fact.
    source = FakeEarningsSource(unavailable={"UCG.MI"})

    with pytest.raises(SourceUnavailable):
        source.latest_report("UCG.MI")


def test_earnings_source_returns_domain_types() -> None:
    source = FakeEarningsSource(
        next_dates={"UCG.MI": date(2026, 10, 22)}, reports={"UCG.MI": REPORT}
    )

    assert source.next_earnings_date("UCG.MI") == date(2026, 10, 22)
    assert source.latest_report("UCG.MI").period == "2026-Q2"


def test_price_source_clips_to_range_and_keeps_gaps() -> None:
    series = {
        date(2026, 8, 3): Decimal("81.70"),
        # 2026-08-04 deliberately absent — a gap must stay a gap, never be interpolated.
        date(2026, 8, 5): Decimal("84.71"),
    }
    source = FakePriceSource({"UCG.MI": series})

    closes = source.daily_closes("UCG.MI", date(2026, 8, 3), date(2026, 8, 4))

    assert closes == {date(2026, 8, 3): Decimal("81.70")}
