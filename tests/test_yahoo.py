"""Adapter tests: real pandas frames shaped like yfinance's, no network."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from pwi.sources import DividendSource, EarningsSource, SourceUnavailable
from pwi.yahoo import COL_ESTIMATE, COL_REPORTED, COL_SURPRISE, YahooDividends, YahooEarnings

# Shaped after the live response for UCG.MI: newest first, tz-aware index, and the
# not-yet-reported quarter carrying NaN in Reported EPS.
FRAME = pd.DataFrame(
    {
        COL_ESTIMATE: [1.87, 1.79, 1.85],
        COL_REPORTED: [float("nan"), 1.80, 2.09],
        COL_SURPRISE: [float("nan"), 0.55, 13.16],
    },
    index=pd.DatetimeIndex(
        ["2026-10-22 11:00:00-04:00", "2026-07-23 01:00:00-04:00", "2026-05-05 01:00:00-04:00"],
        name="Earnings Date",
    ),
)

# The report on 2026-07-23 covers the quarter ending 2026-06-30 — a 23-day announcement
# lag, well inside the normal range.
INCOME = pd.DataFrame(
    {
        pd.Timestamp("2026-06-30"): [float("nan")],  # lags publication by weeks
        pd.Timestamp("2026-03-31"): [7073.0],
    },
    index=["Total Revenue"],
)


def _source(frame=FRAME, income=INCOME, error: Exception | None = None):
    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def earnings_dates(self):
            if error is not None:
                raise error
            return frame

        @property
        def quarterly_income_stmt(self):
            return income

    return YahooEarnings(ticker_factory=FakeTicker)


def test_satisfies_the_protocol() -> None:
    assert isinstance(_source(), EarningsSource)


def test_fetch_separates_scheduled_from_published() -> None:
    earnings = _source().fetch("UCG.MI")

    # The 2026-10-22 row has no reported EPS: scheduled, so it is the next date and must
    # not appear among published quarters.
    assert earnings.next_date == date(2026, 10, 22)
    assert [r.reported_date for r in earnings.published] == [date(2026, 7, 23), date(2026, 5, 5)]


def test_latest_is_the_most_recent_published_quarter() -> None:
    latest = _source().fetch("UCG.MI").latest

    assert latest.reported_date == date(2026, 7, 23)
    assert latest.reported_eps == 1.80
    assert latest.estimated_eps == 1.79
    assert latest.surprise_percentage == 0.55
    assert latest.beat is True


def test_nothing_published_yet_yields_no_latest() -> None:
    earnings = _source(FRAME.head(1)).fetch("UCG.MI")

    assert earnings.latest is None
    assert earnings.next_date == date(2026, 10, 22)


def test_all_reported_means_no_next_date() -> None:
    assert _source(FRAME.tail(2)).fetch("UCG.MI").next_date is None


def test_unknown_symbol_is_empty_not_an_error() -> None:
    empty = pd.DataFrame(columns=[COL_ESTIMATE, COL_REPORTED, COL_SURPRISE])

    earnings = _source(empty).fetch("NOPE")

    assert earnings.published == ()
    assert earnings.next_date is None


def test_lookup_failure_is_source_unavailable() -> None:
    # yfinance raises a wide, unstable set of exceptions; all become one retryable type.
    with pytest.raises(SourceUnavailable):
        _source(error=RuntimeError("rate limited")).fetch("UCG.MI")


class TestRevenue:
    def test_lagging_actual_is_reported_as_unknown(self) -> None:
        # The newest column is NaN because the income statement trails publication. That
        # must read as "not known yet", never as zero.
        revenue = _source().revenue("UCG.MI", reported_date=date(2026, 7, 23))

        assert revenue.actual is None

    def test_actual_is_read_once_the_source_fills_it_in(self) -> None:
        filled = pd.DataFrame(
            {pd.Timestamp("2026-06-30"): [6500.0]},
            index=["Total Revenue"],
        )

        revenue = _source(income=filled).revenue("UCG.MI", reported_date=date(2026, 7, 23))

        assert revenue.actual == Decimal("6500.0")

    def test_stale_column_is_not_mistaken_for_the_quarter_just_reported(self) -> None:
        # Confirmed live against NVDA: the statement's newest column can be a whole
        # quarter behind the one just announced, with a real (non-NaN) figure in it — not
        # "not filled in yet" but "still on the previous quarter". Showing it would
        # misreport an older quarter's revenue as this one's actual.
        stale = pd.DataFrame(
            {pd.Timestamp("2026-04-30"): [81615.0]},
            index=["Total Revenue"],
        )

        revenue = _source(income=stale).revenue("UCG.MI", reported_date=date(2026, 8, 26))

        assert revenue.actual is None

    def test_missing_statement_is_not_a_failure(self) -> None:
        revenue = _source(income=pd.DataFrame()).revenue("UCG.MI")

        assert revenue.actual is None

    def test_no_reported_date_skips_the_staleness_check(self) -> None:
        # Calendar Sync-style callers with no report on hand yet get whatever is there,
        # same as before this check existed.
        filled = pd.DataFrame(
            {pd.Timestamp("2020-01-01"): [1.0]},
            index=["Total Revenue"],
        )

        assert _source(income=filled).revenue("UCG.MI").actual == Decimal("1.0")


# Shaped after the live probe against UCG.MI: ex-date index, small decimal values.
DIVIDENDS = pd.Series(
    [0.9872, 1.8029, 1.7208],
    index=pd.DatetimeIndex(
        ["2024-04-22 09:00:00+02:00", "2025-04-22 09:00:00+02:00", "2026-04-20 09:00:00+02:00"],
        name="Date",
    ),
    name="Dividends",
)


def _dividend_source(series=DIVIDENDS, error: Exception | None = None):
    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def dividends(self):
            if error is not None:
                raise error
            return series

    return YahooDividends(ticker_factory=FakeTicker)


class TestDividends:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(_dividend_source(), DividendSource)

    def test_history_is_returned_with_the_ex_date_and_decimal_amount(self) -> None:
        history = _dividend_source().history("UCG.MI")

        assert [d.ex_date for d in history] == [
            date(2024, 4, 22),
            date(2025, 4, 22),
            date(2026, 4, 20),
        ]
        assert history[-1].amount == Decimal("1.7208")
        assert all(d.symbol == "UCG.MI" for d in history)

    def test_no_history_is_empty_not_an_error(self) -> None:
        empty = pd.Series([], index=pd.DatetimeIndex([]), dtype=float)

        assert _dividend_source(empty).history("NOPE") == ()

    def test_lookup_failure_is_source_unavailable(self) -> None:
        with pytest.raises(SourceUnavailable):
            _dividend_source(error=RuntimeError("rate limited")).history("UCG.MI")
