"""Watchlist persistence.

A JSON file is the v1 store: the watchlist is ~10-15 manually maintained rows. Phase 3
replaces the aggregation/state logic around it with a custom MCP server; the record shape
stays the one the ADR fixes.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .models import Label, TrackedTicker

DEFAULT_PATH = Path("data/watchlist.json")


def load(path: Path = DEFAULT_PATH) -> list[TrackedTicker]:
    rows = json.loads(path.read_text())
    return [
        TrackedTicker(
            symbol=row["symbol"],
            label=Label(row["label"]),
            next_earnings_date=_optional_date(row.get("next_earnings_date")),
            last_notified_period=row.get("last_notified_period"),
        )
        for row in rows
    ]


def save(tickers: list[TrackedTicker], path: Path = DEFAULT_PATH) -> None:
    payload = [
        {
            "symbol": t.symbol,
            "label": str(t.label),
            "next_earnings_date": (
                t.next_earnings_date.isoformat() if t.next_earnings_date else None
            ),
            "last_notified_period": t.last_notified_period,
        }
        for t in tickers
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()
