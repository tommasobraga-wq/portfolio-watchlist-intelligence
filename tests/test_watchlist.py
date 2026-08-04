from datetime import date
from pathlib import Path

from pwi.models import Label, TrackedTicker
from pwi.watchlist import load, save


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.json"
    tickers = [
        TrackedTicker("UCG.MI", Label.HELD, date(2026, 2, 10), "2025-Q4"),
        TrackedTicker("RACE.MI", Label.WATCHING),
    ]

    save(tickers, path)

    assert load(path) == tickers
