"""Orchestration tests. No network, no SMTP, no clock — everything injected."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from fakes import FakeDividendSource, FakeEarningsSource
from pwi import jobs, watchlist
from pwi.models import Dividend, EarningsReport, Label, TickerEarnings, TrackedTicker
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


def dividend(symbol: str, ex_date: date, amount: str = "1.72") -> Dividend:
    return Dividend(symbol, ex_date, Decimal(amount))


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
            ]
        )
        source = FakeEarningsSource(
            {"UCG.MI": TickerEarnings("UCG.MI", published=(report(TODAY),))}
        )

        jobs.results_watch(source, SpyMailer(), path, key=KEY, today=TODAY, delay=0)

        # This is what keeps the job nearly free outside earnings season.
        assert source.fetched == ["UCG.MI"]

    def test_a_ticker_with_no_expected_date_is_checked_every_run(self, store) -> None:
        # Yahoo does not always expose a forward date (TEAM reported 2026-08-06 with no
        # scheduled row ever listed beforehand). Unlike a genuinely far-off date, `None`
        # must not be treated as "not due" — that would exclude the ticker permanently
        # instead of temporarily, since nothing ever re-arms it.
        path = store([TrackedTicker("GOAI-EUR.MI", Label.HELD, None, "2026-Q1")])
        source = FakeEarningsSource(
            {"GOAI-EUR.MI": TickerEarnings("GOAI-EUR.MI", published=(report(TODAY),))}
        )
        mailer = SpyMailer()

        summary = jobs.results_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert source.fetched == ["GOAI-EUR.MI"]
        assert summary.notified == 1

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
        assert read(path)["UCG.MI"].last_notified_date == TODAY
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
        assert summary.delivery_failed == 1
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

    def test_a_failed_overdue_send_does_not_lose_other_tickers_state(self, store) -> None:
        # The overdue mail used to be the one send site with no try/except: a failure there
        # propagated out of results_watch and skipped the persist step entirely, discarding
        # every other ticker's already-computed state from the same run.
        path = store(
            [
                TrackedTicker("FBK.MI", Label.HELD, date(2026, 10, 15), None),  # goes overdue
                TrackedTicker("UCG.MI", Label.HELD, TODAY, "2026-Q2"),  # goes notified
            ]
        )
        source = FakeEarningsSource(
            {"UCG.MI": TickerEarnings("UCG.MI", published=(report(TODAY),))}
        )

        class FailOnlyOverdue(SpyMailer):
            def send(self, subject: str, body: str) -> None:
                if "Risultati attesi e non trovati" in subject:
                    raise RuntimeError("smtp is down")
                super().send(subject, body)

        summary = jobs.results_watch(source, FailOnlyOverdue(), path, key=KEY, today=TODAY, delay=0)

        assert summary.overdue == 0  # never confirmed sent
        assert summary.notified == 1  # unaffected by the other ticker's failure
        assert summary.delivery_failed == 1
        # The point of the regression: UCG.MI's successful result still made it to disk.
        assert read(path)["UCG.MI"].last_notified_period == "2026-Q4"

    def test_overdue_mentions_a_prior_due_today_notice(self, store) -> None:
        path = store(
            [
                TrackedTicker(
                    "FBK.MI",
                    Label.HELD,
                    date(2026, 10, 15),
                    None,
                    last_notified_due_date=date(2026, 10, 15),
                )
            ]
        )
        mailer = SpyMailer()

        jobs.results_watch(FakeEarningsSource(), mailer, path, key=KEY, today=TODAY, delay=0)

        assert "già segnalato" in mailer.sent[0][1]

    def test_a_ticker_due_today_gets_an_immediate_notice_and_is_stamped(self, store) -> None:
        path = store([TrackedTicker("SPCX", Label.WATCHING, TODAY, None)])
        mailer = SpyMailer()

        summary = jobs.results_watch(
            FakeEarningsSource(), mailer, path, key=KEY, today=TODAY, delay=0
        )

        assert summary.due_today == 1
        assert len(mailer.sent) == 1
        assert "SPCX" in mailer.sent[0][0]
        assert read(path)["SPCX"].last_notified_due_date == TODAY
        assert read(path)["SPCX"].next_earnings_date == TODAY  # stays in window

    def test_the_due_today_notice_is_not_repeated_within_the_same_day(self, store) -> None:
        path = store([TrackedTicker("SPCX", Label.WATCHING, TODAY, None)])
        mailer = SpyMailer()

        jobs.results_watch(FakeEarningsSource(), mailer, path, key=KEY, today=TODAY, delay=0)
        jobs.results_watch(FakeEarningsSource(), mailer, path, key=KEY, today=TODAY, delay=0)

        assert len(mailer.sent) == 1

    def test_a_failed_due_today_send_is_retried_next_run(self, store) -> None:
        path = store([TrackedTicker("SPCX", Label.WATCHING, TODAY, None)])

        summary = jobs.results_watch(
            FakeEarningsSource(), SpyMailer(fail=True), path, key=KEY, today=TODAY, delay=0
        )

        assert summary.due_today == 0
        assert summary.delivery_failed == 1
        assert read(path)["SPCX"].last_notified_due_date is None

    def test_a_real_result_on_the_due_date_is_notified_not_just_flagged_due(self, store) -> None:
        path = store([TrackedTicker("SPCX", Label.WATCHING, TODAY, "2026-Q2")])
        source = FakeEarningsSource({"SPCX": TickerEarnings("SPCX", published=(report(TODAY),))})
        mailer = SpyMailer()

        summary = jobs.results_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert summary.notified == 1
        assert summary.due_today == 0

    def test_revenue_failure_does_not_block_the_result(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD, TODAY, "2026-Q2")])

        class FlakyRevenue(FakeEarningsSource):
            def revenue(self, symbol: str, reported_date=None):
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


class TestDividendWatch:
    def test_only_held_tickers_are_looked_up(self, store) -> None:
        path = store(
            [
                TrackedTicker("UCG.MI", Label.HELD),
                TrackedTicker("ENEL.MI", Label.WATCHING),  # not held, pays nothing to us
            ]
        )
        source = FakeDividendSource({"UCG.MI": (dividend("UCG.MI", TODAY),)})

        jobs.dividend_watch(source, SpyMailer(), path, key=KEY, today=TODAY, delay=0)

        assert source.fetched == ["UCG.MI"]

    def test_a_fresh_first_sighting_is_notified_and_stamped(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD)])
        source = FakeDividendSource({"UCG.MI": (dividend("UCG.MI", TODAY),)})
        mailer = SpyMailer()

        summary = jobs.dividend_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert summary.notified == 1
        assert "UCG.MI" in mailer.sent[0][0]
        assert read(path)["UCG.MI"].last_notified_dividend_date == TODAY

    def test_a_stale_first_sighting_is_adopted_without_mail(self, store) -> None:
        # A newly tracked ticker with years of dividend history must not fire off a
        # notification about an old payment — the same bootstrap rule as earnings.
        path = store([TrackedTicker("UCG.MI", Label.HELD)])
        old = dividend("UCG.MI", date(2020, 1, 1))
        mailer = SpyMailer()

        summary = jobs.dividend_watch(
            FakeDividendSource({"UCG.MI": (old,)}), mailer, path, key=KEY, today=TODAY, delay=0
        )

        assert summary.adopted == 1
        assert mailer.sent == []
        assert read(path)["UCG.MI"].last_notified_dividend_date == date(2020, 1, 1)

    def test_the_same_dividend_is_never_sent_twice(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD)])
        source = FakeDividendSource({"UCG.MI": (dividend("UCG.MI", TODAY),)})
        mailer = SpyMailer()

        jobs.dividend_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)
        jobs.dividend_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert len(mailer.sent) == 1

    def test_a_failed_send_leaves_the_dividend_unstamped(self, store) -> None:
        path = store([TrackedTicker("UCG.MI", Label.HELD)])
        source = FakeDividendSource({"UCG.MI": (dividend("UCG.MI", TODAY),)})

        summary = jobs.dividend_watch(
            source, SpyMailer(fail=True), path, key=KEY, today=TODAY, delay=0
        )

        assert summary.notified == 0
        assert summary.delivery_failed == 1
        assert read(path)["UCG.MI"].last_notified_dividend_date is None

    def test_one_throttled_ticker_does_not_stop_the_others(self, store) -> None:
        path = store([TrackedTicker("FBK.MI", Label.HELD), TrackedTicker("UCG.MI", Label.HELD)])
        source = FakeDividendSource(
            {"UCG.MI": (dividend("UCG.MI", TODAY),)}, unavailable={"FBK.MI"}
        )
        mailer = SpyMailer()

        summary = jobs.dividend_watch(source, mailer, path, key=KEY, today=TODAY, delay=0)

        assert summary.unavailable == 1
        assert summary.notified == 1

    def test_no_dividend_ever_is_silent(self, store) -> None:
        path = store([TrackedTicker("GOAI-EUR.MI", Label.HELD)])  # ETF, pays nothing
        mailer = SpyMailer()

        summary = jobs.dividend_watch(
            FakeDividendSource(), mailer, path, key=KEY, today=TODAY, delay=0
        )

        assert summary.notified == 0
        assert mailer.sent == []
