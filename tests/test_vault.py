from datetime import date
from pathlib import Path

import pytest

from pwi import vault
from pwi.models import Label, TrackedTicker
from pwi.watchlist import load, save

KEY = vault.generate_key()
OTHER_KEY = vault.generate_key()
TICKERS = [
    TrackedTicker("UCG.MI", Label.HELD, date(2026, 10, 22), "2026-Q3"),
    TrackedTicker("RACE.MI", Label.WATCHING),
]


def test_round_trip_through_encryption(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.enc"

    save(TICKERS, path, key=KEY)

    assert load(path, key=KEY) == TICKERS


def test_the_file_on_disk_reveals_nothing(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.enc"

    save(TICKERS, path, key=KEY)
    raw = path.read_bytes()

    # The whole point: neither the symbols nor the held/watching labels are readable.
    assert b"UCG.MI" not in raw
    assert b"held" not in raw


def test_the_wrong_key_fails_loudly(tmp_path: Path) -> None:
    # It must never degrade into "empty watchlist", which would silently stop all alerts.
    path = tmp_path / "watchlist.enc"
    save(TICKERS, path, key=KEY)

    with pytest.raises(vault.VaultError, match="wrong key"):
        load(path, key=OTHER_KEY)


def test_tampering_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.enc"
    save(TICKERS, path, key=KEY)
    raw = bytearray(path.read_bytes())
    raw[-5] ^= 0x01
    path.write_bytes(bytes(raw))

    with pytest.raises(vault.VaultError):
        load(path, key=KEY)


def test_json_suffix_stays_cleartext(tmp_path: Path) -> None:
    # The local convenience path, used by the import tool and by tests.
    path = tmp_path / "watchlist.json"

    save(TICKERS, path)

    assert b"UCG.MI" in path.read_bytes()
    assert load(path) == TICKERS


def test_a_missing_key_is_reported_before_anything_else(monkeypatch) -> None:
    monkeypatch.delenv(vault.KEY_VAR, raising=False)

    with pytest.raises(vault.KeyMissing, match="generate-key"):
        vault.key_from_env()


def test_key_is_read_through_quotes_and_whitespace(monkeypatch) -> None:
    monkeypatch.setenv(vault.KEY_VAR, f'  "{KEY}" ')

    assert vault.key_from_env() == KEY


def test_an_invalid_key_is_rejected() -> None:
    with pytest.raises(vault.VaultError, match="valid Fernet key"):
        vault.encrypt(b"x", "not-a-key")
