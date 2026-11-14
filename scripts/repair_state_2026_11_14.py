#!/usr/bin/env python
"""One-off state repair for the three rows damaged by two notification bugs.

Run once, on an up-to-date checkout, after the fixes have landed:

    uv run scripts/repair_state_2026_11_14.py

Idempotent: a row that already holds a value is left alone, so a second run is a no-op and
cannot double-apply. The cleartext never touches disk — the watchlist is decrypted, edited
and re-encrypted in process.

What it repairs:

1. `ACN` and `SCHW` carry `last_notified_period` 2026-Q4 with `last_notified_date` null, the
   signature of the old `apply_dividend`, which re-listed the fields it carried over and
   reset this one. With it null, `digest._is_stale` cannot engage, leaving both rows open to
   the duplicate notification that guard exists to prevent. Each is set to the real
   announcement date of the quarter the row already has stamped.

2. `PRY.MI` reported on 2026-10-29 and was never notified: Calendar Sync advanced its
   expected date to 2027-02-25 on 2026-11-01, closing the window on a published result.
   Clearing the date puts it back on the always-checked path, so the next Results Watch run
   finds the 2026-Q4 report and mails the full digest — no hand-written recovery mail, the
   normal code path does it. The sync after that re-arms the real forward date.
"""

from __future__ import annotations

from datetime import date

from pwi import watchlist
from pwi.config import load_environment

# Announcement date of the quarter each row already carries in `last_notified_period`,
# read from the source on 2026-11-14.
NOTIFIED_ON = {"ACN": date(2026, 10, 1), "SCHW": date(2026, 10, 15)}
REOPEN = {"PRY.MI": "2026-Q3"}  # expected period still stamped, i.e. the unannounced one


def main() -> int:
    load_environment()
    tickers = watchlist.load()
    by_symbol = {t.symbol: t for t in tickers}
    changed = False

    for symbol, notified_on in NOTIFIED_ON.items():
        row = by_symbol.get(symbol)
        if row is None:
            print(f"{symbol}: not in the watchlist, skipped")
            continue
        if row.last_notified_date is not None:
            print(f"{symbol}: last_notified_date already {row.last_notified_date}, left alone")
            continue
        row.last_notified_date = notified_on
        changed = True
        print(f"{symbol}: last_notified_date None -> {notified_on}")

    for symbol, stale_period in REOPEN.items():
        row = by_symbol.get(symbol)
        if row is None:
            print(f"{symbol}: not in the watchlist, skipped")
            continue
        if row.last_notified_period != stale_period:
            print(f"{symbol}: now stamped {row.last_notified_period}, already recovered")
            continue
        if row.next_earnings_date is None:
            print(f"{symbol}: already on the always-checked path, left alone")
            continue
        print(f"{symbol}: next_earnings_date {row.next_earnings_date} -> None (reopen for lookup)")
        row.next_earnings_date = None
        changed = True

    if not changed:
        print("\nnothing to do")
        return 0

    watchlist.save(tickers)
    print(f"\n{len(tickers)} rows written back, encrypted")
    print("Commit data/watchlist.enc, then let the next scheduled run deliver PRY.MI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
