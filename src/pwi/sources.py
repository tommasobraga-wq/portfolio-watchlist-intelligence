"""The contracts every data source must satisfy.

Stated in domain terms only. No vendor concept — API keys, session crumbs, CSV, `NaN`,
DataFrames — crosses this boundary; each of those stays inside its implementation. Jobs,
digest logic and the MCP server depend on these protocols, never on a concrete vendor, so
replacing one is a new class rather than a rewrite (see the ADR's *Source abstraction*).

Kept to what the two jobs actually need. Widen only when a job needs more.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from .models import EarningsReport


class SourceError(RuntimeError):
    """A source could not answer. Implementations raise this, not vendor-specific errors."""


class SourceUnavailable(SourceError):
    """Transient: throttled, rate-limited, or the session needs re-establishing.

    Distinct from a genuine "no data" answer, which is expressed by returning None. The
    difference matters: a job should retry or back off on this, but treat a None as fact.
    """


@runtime_checkable
class EarningsSource(Protocol):
    def next_earnings_date(self, symbol: str) -> date | None:
        """The next expected reporting date, or None if the source has no scheduled date.

        Expected dates shift; callers re-read them rather than caching indefinitely.
        """
        ...

    def latest_report(self, symbol: str) -> EarningsReport | None:
        """The most recently *published* quarter, or None if nothing has been reported.

        Must never return a quarter that has been scheduled but not yet released — that
        distinction is the entire basis of the publication check, so it belongs to the
        implementation, which knows how its vendor signals "not out yet".
        """
        ...


@runtime_checkable
class PriceSource(Protocol):
    def daily_closes(self, symbol: str, start: date, end: date) -> dict[date, Decimal]:
        """Closing prices per trading day in `[start, end]`, split- and dividend-adjusted.

        Missing days are absent from the mapping rather than interpolated: a gap must stay
        visible, since a silently filled price becomes a wrong return. `Decimal`, not
        `float` — repeated valuation accumulates binary floating-point error.
        """
        ...
