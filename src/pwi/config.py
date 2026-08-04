"""Environment loading for entry points.

Every command needs credentials from `.env` locally and from secrets in CI. Requiring the
caller to source the file by hand is a step that gets forgotten, and the resulting failure
("key is not set") points at the wrong problem, so entry points load it themselves.

`override=False` is the important part: a real environment variable always wins over the
file, so CI secrets are never shadowed by a stray `.env` that happens to be checked out.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(".env")


def load_environment(path: Path = ENV_FILE) -> None:
    """Load `.env` if present. A missing file is normal — in CI there isn't one."""
    if path.exists():
        load_dotenv(path, override=False)
