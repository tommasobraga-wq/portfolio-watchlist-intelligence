"""In-memory sources for tests.

The point of the protocols: the interesting logic — de-duplication, the active window,
first-run bootstrap — is testable against these with arbitrary dates, no network, and no
waiting for a real quarter to be published.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pwi.models import Revenue, TickerEarnings
from pwi.sources import SourceUnavailable


class FakeEarningsSource:
    """Satisfies `EarningsSource`.

    `unavailable` makes a symbol raise, so callers can be tested against a source that is
    throttled rather than empty — the two must not be conflated.
    """

    def __init__(
        self,
        earnings: dict[str, TickerEarnings] | None = None,
        revenues: dict[str, Revenue] | None = None,
        unavailable: set[str] | None = None,
    ) -> None:
        self._earnings = earnings or {}
        self._revenues = revenues or {}
        self._unavailable = unavailable or set()
        self.fetched: list[str] = []  # lets tests assert no lookup is wasted
        self.revenue_calls: list[str] = []

    def fetch(self, symbol: str) -> TickerEarnings:
        self.fetched.append(symbol)
        self._guard(symbol)
        return self._earnings.get(symbol, TickerEarnings(symbol))

    def revenue(self, symbol: str) -> Revenue:
        self.revenue_calls.append(symbol)
        self._guard(symbol)
        return self._revenues.get(symbol, Revenue())

    def _guard(self, symbol: str) -> None:
        if symbol in self._unavailable:
            raise SourceUnavailable(f"fake source is throttled for {symbol}")


class FakePriceSource:
    """Satisfies `PriceSource`. Returns only the days it was given, gaps included."""

    def __init__(self, closes: dict[str, dict[date, Decimal]] | None = None) -> None:
        self._closes = closes or {}

    def daily_closes(self, symbol: str, start: date, end: date) -> dict[date, Decimal]:
        series = self._closes.get(symbol, {})
        return {d: p for d, p in series.items() if start <= d <= end}
