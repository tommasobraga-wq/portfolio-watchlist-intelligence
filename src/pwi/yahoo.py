"""Yahoo Finance as an `EarningsSource`, backed by `yfinance`.

Why the library and not a hand-written REST client: Yahoo fingerprints the TLS handshake,
so a plain `httpx` client is refused (429) no matter how correctly it handles cookies and
crumbs. `yfinance` ships `curl_cffi` to deal with that. See the ADR.

Everything vendor-shaped — DataFrames, `NaN`, tz-aware timestamps — is converted here and
never crosses into the rest of the package.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from .models import EarningsReport
from .sources import SourceUnavailable

# yfinance column names, kept in one place since they are the thing most likely to change.
COL_ESTIMATE = "EPS Estimate"
COL_REPORTED = "Reported EPS"
COL_SURPRISE = "Surprise(%)"


class YahooEarnings:
    """Satisfies `EarningsSource`.

    One `yfinance.Ticker` per symbol per call; `yfinance` maintains its own session and
    cache underneath, so there is no connection state worth holding here.
    """

    def __init__(self, ticker_factory: Any = None) -> None:
        # Injectable so tests can drive the adapter without touching the network.
        if ticker_factory is None:
            import yfinance

            ticker_factory = yfinance.Ticker
        self._ticker = ticker_factory

    def next_earnings_date(self, symbol: str) -> date | None:
        frame = self._earnings_frame(symbol)
        if frame is None:
            return None

        # Rows with no reported EPS are scheduled but unpublished. Take the earliest such
        # row rather than the latest: after a company reports, the next quarter is already
        # listed, and we want the one coming up, not the furthest out.
        upcoming = [
            _as_date(index) for index, row in frame.iterrows() if _is_missing(row.get(COL_REPORTED))
        ]
        return min(upcoming) if upcoming else None

    def latest_report(self, symbol: str) -> EarningsReport | None:
        frame = self._earnings_frame(symbol)
        if frame is None:
            return None

        # A row carrying a reported EPS is published, by definition. This is the whole
        # publication check: scheduled-but-unreleased rows have NaN and are skipped, so a
        # result can never be announced before it exists.
        published = [
            (_as_date(index), row)
            for index, row in frame.iterrows()
            if not _is_missing(row.get(COL_REPORTED))
        ]
        if not published:
            return None

        reported_on, row = max(published, key=lambda pair: pair[0])
        return EarningsReport(
            symbol=symbol,
            # Yahoo indexes by announcement date and does not expose the fiscal period end.
            # Using the announcement date keeps `period` stable for de-duplication, which
            # is all it is needed for, but it is not the fiscal date — see the ADR note.
            fiscal_date_ending=reported_on,
            reported_date=reported_on,
            reported_eps=_as_float(row.get(COL_REPORTED)),
            estimated_eps=_as_float(row.get(COL_ESTIMATE)),
            surprise_percentage=_as_float(row.get(COL_SURPRISE)),
        )

    def _earnings_frame(self, symbol: str):
        try:
            frame = self._ticker(symbol).earnings_dates
        except Exception as exc:  # yfinance raises a wide and unstable set of errors
            raise SourceUnavailable(f"Yahoo lookup failed for {symbol}: {exc}") from exc

        # An unknown symbol yields an empty frame or None — that is "no data", not a fault.
        if frame is None or frame.empty:
            return None
        return frame


def _is_missing(value: Any) -> bool:
    """`NaN` marks a quarter that is scheduled but not yet reported."""
    if value is None:
        return True
    return isinstance(value, float) and math.isnan(value)


def _as_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date:
    """Yahoo indexes by tz-aware timestamp; we track calendar days."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()
