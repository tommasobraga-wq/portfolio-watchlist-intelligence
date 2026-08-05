"""Core records, as fixed by the ADR's data model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum


class Label(StrEnum):
    """Manually maintained; never synced automatically from the broker (see ADR)."""

    HELD = "held"
    WATCHING = "watching"


@dataclass(slots=True)
class TrackedTicker:
    symbol: str
    label: Label
    next_earnings_date: date | None = None
    # Fiscal period ("2025-Q3") already notified, guarding against duplicate alerts.
    last_notified_period: str | None = None
    # Ex-dividend date of the last dividend notified, guarding against duplicate alerts.
    # There is no forward-looking counterpart to `next_earnings_date`: Yahoo never publishes
    # a forward payment date for every exchange this watchlist covers (Milan included), so
    # dividend detection has no window to open and close — see `Dividend`.
    last_notified_dividend_date: date | None = None


@dataclass(slots=True, frozen=True)
class EarningsReport:
    """One published quarter."""

    symbol: str
    fiscal_date_ending: date
    reported_date: date | None
    reported_eps: float | None
    estimated_eps: float | None
    surprise_percentage: float | None

    @property
    def period(self) -> str:
        """De-duplication key only — never displayed.

        Derived from the announcement date, which sits a quarter after the fiscal period
        it reports on, so the label reads "Q3" over Q2 figures. It is stable and unique
        per quarter, which is all de-duplication needs. See the ADR.
        """
        quarter = (self.fiscal_date_ending.month - 1) // 3 + 1
        return f"{self.fiscal_date_ending.year}-Q{quarter}"

    @property
    def beat(self) -> bool | None:
        if self.reported_eps is None or self.estimated_eps is None:
            return None
        return self.reported_eps > self.estimated_eps


@dataclass(slots=True, frozen=True)
class Dividend:
    """One dividend payment, keyed by ex-dividend date.

    Ex-date, not payment date: it is the only date Yahoo exposes consistently across every
    exchange this watchlist covers. Milan tickers never carry a forward payment date, only
    a forward ex-dividend date, and the historical series (`t.dividends`) is itself indexed
    by ex-date. Using anything else would silently drop Milan holdings from detection.
    """

    symbol: str
    ex_date: date
    amount: Decimal


@dataclass(slots=True, frozen=True)
class Revenue:
    """Revenue around the quarter just reported.

    `actual` lags badly: Yahoo refreshes `earnings_dates` at publication but the income
    statement much later — UCG.MI still had no June figure two weeks after reporting. So
    `actual` is frequently None at notification time and `estimate` carries the message.
    """

    actual: Decimal | None = None
    estimate: Decimal | None = None
    currency: str | None = None


@dataclass(slots=True, frozen=True)
class TickerEarnings:
    """Everything one lookup yields about a symbol.

    Returned whole so a job spends one request per ticker rather than one per question.
    """

    symbol: str
    next_date: date | None = None
    # Published quarters, newest first. Scheduled-but-unreported ones are excluded.
    published: tuple[EarningsReport, ...] = ()

    @property
    def latest(self) -> EarningsReport | None:
        return self.published[0] if self.published else None

    def year_before(self, report: EarningsReport) -> EarningsReport | None:
        """The comparable quarter a year earlier, for a like-for-like comparison.

        Matched by proximity to the same date one year back rather than by counting four
        rows, because a company that skipped or delayed a quarter would put the count out
        of step and compare the wrong periods.
        """
        if report.reported_date is None:
            return None
        target = report.reported_date - timedelta(days=365)
        candidates = [
            r
            for r in self.published
            if r.reported_date is not None
            and r is not report
            and abs((r.reported_date - target).days) <= 45
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda r: abs((r.reported_date - target).days))

    def track_record(self, quarters: int = 4) -> tuple[int, int]:
        """How often the company beat its estimate recently, as (beats, comparable)."""
        comparable = [
            r
            for r in self.published[:quarters]
            if r.reported_eps is not None and r.estimated_eps is not None
        ]
        return sum(1 for r in comparable if r.beat), len(comparable)
