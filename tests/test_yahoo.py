"""Adapter tests: real pandas frames shaped like yfinance's, no network."""

from datetime import date

import pandas as pd
import pytest

from pwi.sources import EarningsSource, SourceUnavailable
from pwi.yahoo import COL_ESTIMATE, COL_REPORTED, COL_SURPRISE, YahooEarnings

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


def _source(frame=FRAME, error: Exception | None = None) -> YahooEarnings:
    class FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        @property
        def earnings_dates(self):
            if error is not None:
                raise error
            return frame

    return YahooEarnings(ticker_factory=FakeTicker)


def test_satisfies_the_protocol() -> None:
    assert isinstance(_source(), EarningsSource)


def test_next_date_is_the_earliest_unreported_quarter() -> None:
    assert _source().next_earnings_date("UCG.MI") == date(2026, 10, 22)


def test_latest_report_is_the_most_recent_published_quarter() -> None:
    report = _source().latest_report("UCG.MI")

    # 2026-10-22 is scheduled but unreported, so it must not win.
    assert report.reported_date == date(2026, 7, 23)
    assert report.reported_eps == 1.80
    assert report.estimated_eps == 1.79
    assert report.surprise_percentage == 0.55


def test_scheduled_quarter_is_never_reported_as_published() -> None:
    # Only the future row: nothing has been published yet, so there is no report at all.
    frame = FRAME.head(1)

    assert _source(frame).latest_report("UCG.MI") is None
    assert _source(frame).next_earnings_date("UCG.MI") == date(2026, 10, 22)


def test_all_reported_means_no_next_date() -> None:
    frame = FRAME.tail(2)

    assert _source(frame).next_earnings_date("UCG.MI") is None


def test_unknown_symbol_is_no_data_not_an_error() -> None:
    empty = pd.DataFrame(columns=[COL_ESTIMATE, COL_REPORTED, COL_SURPRISE])

    assert _source(empty).latest_report("NOPE") is None
    assert _source(empty).next_earnings_date("NOPE") is None


def test_lookup_failure_is_source_unavailable() -> None:
    # yfinance raises a wide, unstable set of exceptions; all become one retryable type.
    with pytest.raises(SourceUnavailable):
        _source(error=RuntimeError("rate limited")).latest_report("UCG.MI")
