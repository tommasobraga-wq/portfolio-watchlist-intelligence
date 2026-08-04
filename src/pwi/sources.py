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

from .models import Revenue, TickerEarnings


class SourceError(RuntimeError):
    """A source could not answer. Implementations raise this, not vendor-specific errors."""


class SourceUnavailable(SourceError):
    """Transient: throttled, rate-limited, or the session needs re-establishing.

    Distinct from a genuine "no data" answer, which is expressed by returning None. The
    difference matters: a job should retry or back off on this, but treat a None as fact.
    """


@runtime_checkable
class EarningsSource(Protocol):
    def fetch(self, symbol: str) -> TickerEarnings:
        """Next expected date and published history, in one request.

        Deliberately one call rather than one per question: a job needs both values
        together, and asking twice doubles the load on a source that rate-limits.

        `published` must never include a quarter that is scheduled but not yet released.
        That distinction is the entire basis of the publication check, so it belongs to the
        implementation, which knows how its vendor signals "not out yet".
        """
        ...

    def revenue(self, symbol: str) -> Revenue:
        """Revenue for the most recent quarter. Both fields may be None.

        Separate from `fetch` because it costs extra requests and is only wanted for the
        handful of tickers actually being notified — never for the window check.
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
