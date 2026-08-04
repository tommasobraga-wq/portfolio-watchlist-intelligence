"""Entry point for the scheduled jobs.

python -m pwi calendar-sync
python -m pwi results-watch
python -m pwi results-watch --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
import warnings

from . import jobs
from .config import load_environment
from .mailer import Mailer
from .yahoo import YahooEarnings, YahooPrices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pwi")
    parser.add_argument("job", choices=["calendar-sync", "results-watch"])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent instead of sending it, and do not persist state",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    # yfinance writes "No earnings dates found" to stderr for ETFs, every single run.
    # Expected and harmless, but it buries the real log lines in CI.
    warnings.filterwarnings("ignore")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    load_environment()
    source = YahooEarnings()

    if args.job == "calendar-sync":
        jobs.calendar_sync(source, persist=not args.dry_run)
        return 0

    mailer = _PrintingMailer() if args.dry_run else Mailer.from_env()
    jobs.results_watch(source, mailer, persist=not args.dry_run, prices=YahooPrices())
    return 0


class _PrintingMailer:
    """Stands in for `Mailer` under --dry-run: same `send`, no delivery."""

    def send(self, subject: str, body: str) -> None:
        print(f"\n{'=' * 70}\nSUBJECT: {subject}\n{'-' * 70}\n{body}{'=' * 70}")


if __name__ == "__main__":
    raise SystemExit(main())
