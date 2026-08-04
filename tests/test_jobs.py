"""Orchestration tests. No network, no SMTP, no clock — everything injected."""

from datetime import date
from pathlib import Path

import pytest

from fakes import FakeEarningsSource
from pwi import jobs, watchlist
from pwi.models import EarningsReport, Label, TickerEarnings, TrackedTicker
from pwi.sources import SourceUnavailable

KEY = "9syDlxKZzB2s0IXSXV0Mbuj78mYvMu7924cMt2s28To="  # test-only, not a real secret
TODAY = date(2026, 10, 22)


class SpyMailer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def send(self, subject: str, body: str) -> None:
        if self.fail:
            raise RuntimeError("smtp is down")
        self.sent.append((subject, body))


def report(when: date, eps: float = 1.80) -> EarningsReport:
    return EarningsReport("UCG.MI", when, when, eps, 1.79, 0.55)


@pytest.fixture
def store(tmp_path: Path):
    path = tmp_path / "watchlist.enc"

    def write(tickers: list[TrackedTicker]) -> Path:
        watchlist.save(tickers, path, key=KEY)
        return path

    return write


def read(path: Path) -> dict[str, TrackedTicker]:
    return {t.symbol: t for t in watchlist.load(path, key=KEY)}


class TestResultsWatch:
    def test_only_tickers_in_window_are_looked_up(self, store) -> None:
        path = store(
            [
                TrackedTicker("UCG.MI", Label.HELD, TODAY, "2026-Q2"),
                TrackedTicker("AAPL", Label.HELD, date(2027, 1, 1)),  # far away
                TrackedTicker("GOAI-EUR.MI", Label.HELD, None),  # never reports
            ]
        )
        source = FakeEarningsSource(
            {"UCG.MI": TickerEarnings("UCG.MI", published=(report(TODAY),))}
        )

        jobs.results_watch(source, SpyMailer(), path, key=KEY, today=TODAY, delay=0)

        # This is what keeps the job nearly free outside earnings season.
        assert source.fetched == ["UCG.MI"]

    def test_a_published_result_is_notified_and_stamped(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD, TODAY, "2026-Q2")])
        source = FakeEarningsSource(
            {"UCG.MI": TickerEarnings("UCG.MI", published=(report(TODAY),))}
        )
        mailer = SpyMailer()

        summary = jobs.results_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert summary.notified == 1
        assert "UCG.MI" in mailer.sent[0][0]
        assert read(path)["UCG.MI"].last_notified_period == "2026-Q4"
        assert read(path)["UCG.MI"].next_earnings_date is None

    def test_a_failed_send_leaves_the_quarter_unnotified(self, store) -> None:
        # The point of the whole ordering: stamping before sending would mark a result as
        # delivered that nobody ever saw, and it would never be retried.
        path = store([TrackedTicker("UCG.MI", Label.HELD, TODAY, "2026-Q2")])
        source = FakeEarningsSource(
            {"UCG.MI": TickerEarnings("UCG.MI", published=(report(TODAY),))}
        )

        summary = jobs.results_watch(
            source, SpyMailer(fail=True), path, key=KEY, today=TODAY, delay=0
        )

        assert summary.notified == 0
        stored = read(path)["UCG.MI"]
        assert stored.last_notified_period == "2026-Q2"  # unchanged
        assert stored.next_earnings_date == TODAY  # still in window, so it retries

    def test_the_same_quarter_is_never_sent_twice(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD, TODAY, "2026-Q2")])
        source = FakeEarningsSource(
            {"UCG.MI": TickerEarnings("UCG.MI", published=(report(TODAY),))}
        )
        mailer = SpyMailer()

        jobs.results_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)
        jobs.results_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert len(mailer.sent) == 1

    def test_first_sighting_is_adopted_without_mail(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD, TODAY, None)])
        old = TickerEarnings("UCG.MI", published=(report(date(2026, 7, 23)),))
        mailer = SpyMailer()

        source = FakeEarningsSource({"UCG.MI": old})

        summary = jobs.results_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert source.fetched == ["UCG.MI"]
        assert summary.adopted == 1
        assert mailer.sent == []
        assert read(path)["UCG.MI"].last_notified_period == "2026-Q3"

    def test_one_throttled_ticker_does_not_stop_the_others(self, store) -> None:
        path = store(
            [
                TrackedTicker("FBK.MI", Label.HELD, TODAY, "2026-Q2"),
                TrackedTicker("UCG.MI", Label.HELD, TODAY, "2026-Q2"),
            ]
        )
        source = FakeEarningsSource(
            {"UCG.MI": TickerEarnings("UCG.MI", published=(report(TODAY),))},
            unavailable={"FBK.MI"},
        )
        mailer = SpyMailer()

        summary = jobs.results_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert summary.unavailable == 1
        assert summary.notified == 1
        # The skipped ticker keeps its date, so the next run picks it up again.
        assert read(path)["FBK.MI"].next_earnings_date == TODAY

    def test_overdue_tickers_are_reported_in_one_mail(self, store) -> None:
        path = store(
            [
                TrackedTicker("FBK.MI", Label.HELD, date(2026, 10, 15), None),
                TrackedTicker("TIT.MI", Label.WATCHING, date(2026, 10, 14), None),
            ]
        )
        mailer = SpyMailer()

        summary = jobs.results_watch(
            FakeEarningsSource(), mailer, path, key=KEY, today=TODAY, delay=0
        )

        assert summary.overdue == 2
        assert len(mailer.sent) == 1  # grouped, not one per ticker
        assert "2 titoli" in mailer.sent[0][0]

    def test_revenue_failure_does_not_block_the_result(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD, TODAY, "2026-Q2")])

        class FlakyRevenue(FakeEarningsSource):
            def revenue(self, symbol: str):
                raise SourceUnavailable("throttled")

        source = FlakyRevenue({"UCG.MI": TickerEarnings("UCG.MI", published=(report(TODAY),))})
        mailer = SpyMailer()

        summary = jobs.results_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert summary.notified == 1
        assert "consuntivo" in mailer.sent[0][1]


class TestCalendarSync:
    def test_expected_dates_are_refreshed(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD, date(2026, 10, 1))])
        source = FakeEarningsSource({"UCG.MI": TickerEarnings("UCG.MI", next_date=TODAY)})

        summary = jobs.calendar_sync(source, path, key=KEY, delay=0)

        assert summary.dates_changed == 1
        assert read(path)["UCG.MI"].next_earnings_date == TODAY

    def test_it_rearms_a_ticker_after_a_notification(self, store) -> None:
        # results_watch clears the date; calendar_sync is what puts the next one back.
        path = store([TrackedTicker("UCG.MI", Label.HELD, None, "2026-Q3")])
        source = FakeEarningsSource({"UCG.MI": TickerEarnings("UCG.MI", next_date=TODAY)})

        jobs.calendar_sync(source, path, key=KEY, delay=0)

        assert read(path)["UCG.MI"].next_earnings_date == TODAY

    def test_an_unavailable_ticker_keeps_its_current_date(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD, TODAY)])
        source = FakeEarningsSource(unavailable={"UCG.MI"})

        summary = jobs.calendar_sync(source, path, key=KEY, delay=0)

        assert summary.unavailable == 1
        assert read(path)["UCG.MI"].next_earnings_date == TODAY

    def test_instruments_without_earnings_are_left_dateless(self, store) -> None:
        path = store([TrackedTicker("GOAI-EUR.MI", Label.HELD, None)])

        jobs.calendar_sync(FakeEarningsSource(), path, key=KEY, delay=0)

        assert read(path)["GOAI-EUR.MI"].next_earnings_date is None
