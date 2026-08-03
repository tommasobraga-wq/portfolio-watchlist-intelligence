"""Core records, as fixed by the ADR's data model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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


@dataclass(slots=True, frozen=True)
class EarningsReport:
    """One reported quarter as returned by Alpha Vantage `EARNINGS`."""

    symbol: str
    fiscal_date_ending: date
    reported_date: date | None
    reported_eps: float | None
    estimated_eps: float | None
    surprise_percentage: float | None

    @property
    def period(self) -> str:
        quarter = (self.fiscal_date_ending.month - 1) // 3 + 1
        return f"{self.fiscal_date_ending.year}-Q{quarter}"
