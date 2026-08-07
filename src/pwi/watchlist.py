"""Watchlist persistence.

A single JSON document is the v1 store: the watchlist is ~10-25 manually maintained rows.
It is written **encrypted** (`data/watchlist.enc`) because it names which securities are
actually held — see `vault`. Cleartext is a local convenience only, and gitignored.

Phase 3 replaces the aggregation/state logic around it with a custom MCP server; the record
shape stays the one the ADR fixes.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from . import vault
from .models import Label, TrackedTicker

DEFAULT_PATH = Path("data/watchlist.enc")
PLAINTEXT_PATH = Path("data/watchlist.json")


def load(path: Path = DEFAULT_PATH, key: str | None = None) -> list[TrackedTicker]:
    """Read the watchlist, decrypting unless the path is explicitly cleartext `.json`."""
    raw = path.read_bytes()
    if path.suffix != ".json":
        raw = vault.decrypt(raw, key or vault.key_from_env())
    return [_from_row(row) for row in json.loads(raw)]


def save(
    tickers: list[TrackedTicker],
    path: Path = DEFAULT_PATH,
    key: str | None = None,
) -> None:
    raw = (json.dumps([_to_row(t) for t in tickers], indent=2) + "\n").encode()
    if path.suffix != ".json":
        raw = vault.encrypt(raw, key or vault.key_from_env())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _to_row(t: TrackedTicker) -> dict:
    return {
        "symbol": t.symbol,
        "label": str(t.label),
        "next_earnings_date": (t.next_earnings_date.isoformat() if t.next_earnings_date else None),
        "last_notified_period": t.last_notified_period,
        "last_notified_dividend_date": (
            t.last_notified_dividend_date.isoformat() if t.last_notified_dividend_date else None
        ),
        "last_notified_due_date": (
            t.last_notified_due_date.isoformat() if t.last_notified_due_date else None
        ),
        "last_notified_date": (t.last_notified_date.isoformat() if t.last_notified_date else None),
    }


def _from_row(row: dict) -> TrackedTicker:
    return TrackedTicker(
        symbol=row["symbol"],
        label=Label(row["label"]),
        next_earnings_date=_optional_date(row.get("next_earnings_date")),
        last_notified_period=row.get("last_notified_period"),
        # Absent on rows written before this field existed — a real watchlist entry, not a
        # bug, so it must default to None rather than fail the load.
        last_notified_dividend_date=_optional_date(row.get("last_notified_dividend_date")),
        last_notified_due_date=_optional_date(row.get("last_notified_due_date")),
        last_notified_date=_optional_date(row.get("last_notified_date")),
    )


def _optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()
