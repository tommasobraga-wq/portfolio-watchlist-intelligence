import json
from datetime import date
from pathlib import Path

from pwi.models import Label, TrackedTicker
from pwi.watchlist import load, save


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.json"
    tickers = [
        TrackedTicker("UCG.MI", Label.HELD, date(2026, 2, 10), "2025-Q4", date(2026, 4, 20)),
        TrackedTicker("RACE.MI", Label.WATCHING),
    ]

    save(tickers, path)

    assert load(path) == tickers


def test_a_row_written_before_the_dividend_field_existed_still_loads(tmp_path: Path) -> None:
    # data/watchlist.enc predates last_notified_dividend_date; a real row from before this
    # field was added must load as None, not fail.
    path = tmp_path / "watchlist.json"
    path.write_text(
        json.dumps(
            [
                {
                    "symbol": "UCG.MI",
                    "label": "held",
                    "next_earnings_date": None,
                    "last_notified_period": None,
                }
            ]
        )
    )

    loaded = load(path)

    assert loaded == [TrackedTicker("UCG.MI", Label.HELD)]
    assert loaded[0].last_notified_dividend_date is None
