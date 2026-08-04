"""In-memory sources for tests.

The point of the protocols: the interesting logic — de-duplication, the active window,
first-run bootstrap — is testable against these with arbitrary dates, no network, and no
waiting for a real quarter to be published.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pwi.models import EarningsReport
from pwi.sources import SourceUnavailable


class FakeEarningsSource:
    """Satisfies `EarningsSource`.

    `unavailable` makes a symbol raise, so callers can be tested against a source that is
    throttled rather than empty — the two must not be conflated.
    """

    def __init__(
        self,
        next_dates: dict[str, date] | None = None,
        reports: dict[str, EarningsReport] | None = None,
        unavailable: set[str] | None = None,
    ) -> None:
        self._next_dates = next_dates or {}
        self._reports = reports or {}
        self._unavailable = unavailable or set()
        self.calls: list[str] = []  # lets tests assert that quota was not wasted

    def next_earnings_date(self, symbol: str) -> date | None:
        self._record(symbol)
        return self._next_dates.get(symbol)

    def latest_report(self, symbol: str) -> EarningsReport | None:
        self._record(symbol)
        return self._reports.get(symbol)

    def _record(self, symbol: str) -> None:
        self.calls.append(symbol)
        if symbol in self._unavailable:
            raise SourceUnavailable(f"fake source is throttled for {symbol}")


class FakePriceSource:
    """Satisfies `PriceSource`. Returns only the days it was given, gaps included."""

    def __init__(self, closes: dict[str, dict[date, Decimal]] | None = None) -> None:
        self._closes = closes or {}

    def daily_closes(self, symbol: str, start: date, end: date) -> dict[date, Decimal]:
        series = self._closes.get(symbol, {})
        return {d: p for d, p in series.items() if start <= d <= end}
