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
from decimal import Decimal
from typing import Any

from .models import EarningsReport, Revenue, TickerEarnings
from .sources import SourceUnavailable

# yfinance column names, kept in one place since they are the thing most likely to change.
COL_ESTIMATE = "EPS Estimate"
COL_REPORTED = "Reported EPS"
COL_SURPRISE = "Surprise(%)"

REVENUE_ROWS = ("Total Revenue", "Operating Revenue")


class YahooEarnings:
    """Satisfies `EarningsSource`."""

    def __init__(self, ticker_factory: Any = None) -> None:
        # Injectable so tests can drive the adapter without touching the network.
        if ticker_factory is None:
            import yfinance

            ticker_factory = yfinance.Ticker
        self._ticker = ticker_factory

    def fetch(self, symbol: str) -> TickerEarnings:
        frame = self._call(symbol, lambda t: t.earnings_dates)
        if frame is None or frame.empty:
            return TickerEarnings(symbol)

        published: list[EarningsReport] = []
        scheduled: list[date] = []

        for index, row in frame.iterrows():
            when = _as_date(index)
            # A NaN reported EPS means the quarter is scheduled but not out. This single
            # test is the whole publication check: it is what stops a result being
            # announced before it exists.
            if _is_missing(row.get(COL_REPORTED)):
                scheduled.append(when)
                continue
            published.append(
                EarningsReport(
                    symbol=symbol,
                    # Yahoo indexes by announcement date and never exposes the fiscal
                    # period end, so this doubles as both. Fine for de-duplication, which
                    # is the only thing `period` is used for.
                    fiscal_date_ending=when,
                    reported_date=when,
                    reported_eps=_as_float(row.get(COL_REPORTED)),
                    estimated_eps=_as_float(row.get(COL_ESTIMATE)),
                    surprise_percentage=_as_float(row.get(COL_SURPRISE)),
                )
            )

        published.sort(key=lambda r: r.reported_date or date.min, reverse=True)
        # The earliest scheduled date, not the furthest: once a company reports, later
        # quarters are already listed and the nearest one is what the window cares about.
        return TickerEarnings(symbol, min(scheduled) if scheduled else None, tuple(published))

    def revenue(self, symbol: str) -> Revenue:
        estimate, currency = self._revenue_estimate(symbol)
        return Revenue(actual=self._revenue_actual(symbol), estimate=estimate, currency=currency)

    def _revenue_actual(self, symbol: str) -> Decimal | None:
        statement = self._call(symbol, lambda t: t.quarterly_income_stmt)
        if statement is None or statement.empty or statement.columns.empty:
            return None
        for label in REVENUE_ROWS:
            if label in statement.index:
                # Columns are quarters, newest first; the newest is often still empty
                # because the income statement lags publication by weeks.
                value = statement.loc[label].iloc[0]
                if not _is_missing(value):
                    return _as_decimal(value)
        return None

    def _revenue_estimate(self, symbol: str) -> tuple[Decimal | None, str | None]:
        calendar = self._call(symbol, lambda t: t.calendar)
        if not isinstance(calendar, dict):
            return None, None
        return _as_decimal(calendar.get("Revenue Average")), None

    def _call(self, symbol: str, get: Any) -> Any:
        try:
            return get(self._ticker(symbol))
        except Exception as exc:  # yfinance raises a wide and unstable set of errors
            raise SourceUnavailable(f"Yahoo lookup failed for {symbol}: {exc}") from exc


def _is_missing(value: Any) -> bool:
    """`NaN` marks a quarter that is scheduled but not yet reported."""
    if value is None:
        return True
    try:
        return bool(math.isnan(float(value)))
    except (TypeError, ValueError):
        return False


def _as_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    if _is_missing(value):
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _as_date(value: Any) -> date:
    """Yahoo indexes by tz-aware timestamp; we track calendar days."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()
