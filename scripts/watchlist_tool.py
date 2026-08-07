#!/usr/bin/env python
"""Manage the encrypted watchlist.

    uv run scripts/watchlist_tool.py generate-key                    # once, then store it
    uv run scripts/watchlist_tool.py add UCG.MI:held RACE.MI:watching
    uv run scripts/watchlist_tool.py remove FBK.MI
    uv run scripts/watchlist_tool.py show
    uv run scripts/watchlist_tool.py edit                            # $EDITOR, full JSON
    uv run scripts/watchlist_tool.py encrypt <file.json>             # import cleartext

`edit` writes the cleartext to a temporary file that is removed afterwards, so the holdings
never sit on disk in the repository.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from pwi import vault
from pwi.config import load_environment
from pwi.models import Label, TrackedTicker
from pwi.watchlist import DEFAULT_PATH, load, save


def generate_key() -> int:
    print(vault.generate_key())
    print(
        f"\nStore it as {vault.KEY_VAR} in .env and as a GitHub Actions secret.\n"
        "Without it the watchlist cannot be read — keep a copy somewhere safe.",
        file=sys.stderr,
    )
    return 0


def show() -> int:
    for t in load():
        held = "held" if t.label == "held" else "watching"
        print(f"{t.symbol:<10} {held:<9} next={t.next_earnings_date} last={t.last_notified_date}")
    return 0


def edit() -> int:
    key = vault.key_from_env()
    current = DEFAULT_PATH.read_bytes() if DEFAULT_PATH.exists() else None
    body = vault.decrypt(current, key).decode() if current else _TEMPLATE

    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
        tmp.write(body)
        path = Path(tmp.name)
    try:
        subprocess.run([os.environ.get("EDITOR", "vi"), str(path)], check=True)
        edited = path.read_text()
        json.loads(edited)  # reject malformed JSON before it becomes an opaque blob
        DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_PATH.write_bytes(vault.encrypt(edited.encode(), key))
    finally:
        path.unlink(missing_ok=True)

    print(f"encrypted -> {DEFAULT_PATH}")
    return 0


def add(pairs: list[str]) -> int:
    """Add or relabel tickers: `add UCG.MI:held RACE.MI:watching`.

    Existing rows keep their accumulated state; only the label is updated, so relabelling
    a ticker after a trade never resets its notification history.
    """
    key = vault.key_from_env()
    current = load(DEFAULT_PATH, key=key) if DEFAULT_PATH.exists() else []
    by_symbol = {t.symbol: t for t in current}

    for pair in pairs:
        symbol, _, raw_label = pair.partition(":")
        symbol = symbol.strip().upper()
        label_text = (raw_label or "watching").strip().lower()
        if label_text not in ("held", "watching"):
            print(f"{pair}: label must be 'held' or 'watching'", file=sys.stderr)
            return 2
        label = Label(label_text)

        existing = by_symbol.get(symbol)
        if existing is None:
            by_symbol[symbol] = TrackedTicker(symbol, label)
            print(f"added    {symbol} ({label_text})")
        elif existing.label != label:
            existing.label = label
            print(f"relabel  {symbol} -> {label_text}")
        else:
            print(f"unchanged {symbol}")

    save(list(by_symbol.values()), DEFAULT_PATH, key=key)
    print(f"\n{len(by_symbol)} tickers -> {DEFAULT_PATH} (encrypted)")
    return 0


def remove(symbols: list[str]) -> int:
    key = vault.key_from_env()
    current = load(DEFAULT_PATH, key=key)
    wanted = {s.strip().upper() for s in symbols}
    kept = [t for t in current if t.symbol not in wanted]

    for missing in wanted - {t.symbol for t in current}:
        print(f"not present: {missing}", file=sys.stderr)
    save(kept, DEFAULT_PATH, key=key)
    print(f"{len(current) - len(kept)} removed, {len(kept)} remaining")
    return 0


def encrypt_file(source: str) -> int:
    save(load(Path(source), key="unused"), DEFAULT_PATH)
    print(f"{source} -> {DEFAULT_PATH} (encrypted)")
    print("Delete the cleartext file once you have verified the result.", file=sys.stderr)
    return 0


_TEMPLATE = """[
  {
    "symbol": "UCG.MI",
    "label": "held",
    "next_earnings_date": null,
    "last_notified_period": null
  }
]
"""

COMMANDS = {"generate-key": generate_key, "show": show, "edit": edit}


def main(argv: list[str]) -> int:
    load_environment()
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]
    rest = argv[2:]
    if command == "encrypt":
        if not rest:
            print("usage: watchlist_tool.py encrypt <file.json>", file=sys.stderr)
            return 2
        return encrypt_file(rest[0])
    if command in ("add", "remove"):
        if not rest:
            print(f"usage: watchlist_tool.py {command} <symbol...>", file=sys.stderr)
            return 2
        return add(rest) if command == "add" else remove(rest)
    if command not in COMMANDS:
        print(f"unknown command: {command}\n\n{__doc__}", file=sys.stderr)
        return 2
    return COMMANDS[command]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
