#!/usr/bin/env python
"""Manage the encrypted watchlist.

    uv run scripts/watchlist_tool.py generate-key      # once, then store it
    uv run scripts/watchlist_tool.py edit              # decrypt, open $EDITOR, re-encrypt
    uv run scripts/watchlist_tool.py show              # print to stdout
    uv run scripts/watchlist_tool.py encrypt <file>    # import a cleartext JSON

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
        print(f"{t.symbol:<10} {held:<9} next={t.next_earnings_date} last={t.last_notified_period}")
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
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]
    if command == "encrypt":
        if len(argv) < 3:
            print("usage: watchlist_tool.py encrypt <file.json>", file=sys.stderr)
            return 2
        return encrypt_file(argv[2])
    if command not in COMMANDS:
        print(f"unknown command: {command}\n\n{__doc__}", file=sys.stderr)
        return 2
    return COMMANDS[command]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
