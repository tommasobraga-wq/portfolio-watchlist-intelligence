from datetime import date

from pwi.digest import Decision, Outcome
from pwi.models import EarningsReport, Label
from pwi.render import body, subject

BEAT = Decision(
    "UCG.MI",
    Outcome.NOTIFY,
    report=EarningsReport(
        symbol="UCG.MI",
        fiscal_date_ending=date(2026, 7, 23),
        reported_date=date(2026, 7, 23),
        reported_eps=1.80,
        estimated_eps=1.79,
        surprise_percentage=0.55,
    ),
)
LATE = Decision(
    "FBK.MI",
    Outcome.OVERDUE,
    expected_on=date(2026, 11, 5),
    days_overdue=4,
)
LABELS = {"UCG.MI": Label.HELD, "FBK.MI": Label.WATCHING}


def test_published_block_shows_the_numbers_and_the_source() -> None:
    text = body([BEAT], [], LABELS)

    assert "23 Jul 2026" in text
    assert "1.80" in text and "1.79" in text
    assert "+0.55%" in text
    assert "https://finance.yahoo.com/quote/UCG.MI" in text


def test_held_and_watching_are_distinguished() -> None:
    # The one thing the ADR insists the notification must show.
    text = body([BEAT], [LATE], LABELS)

    assert "UCG.MI [HELD]" in text
    assert "FBK.MI [WATCHING]" in text


def test_fiscal_quarter_label_is_never_shown() -> None:
    # It is derived from the announcement date and off by a quarter — showing it would
    # print "Q3" over Q2 numbers.
    text = body([BEAT], [], LABELS)

    assert "Q3" not in text
    assert BEAT.report.period not in text


def test_overdue_is_framed_as_possibly_a_data_gap() -> None:
    text = body([], [LATE], LABELS)

    assert "05 Nov 2026" in text
    assert "4 days ago" in text
    assert "manual check" in text


def test_surprise_is_computed_when_the_source_omits_it() -> None:
    silent = Decision(
        "PRY.MI",
        Outcome.NOTIFY,
        report=EarningsReport(
            symbol="PRY.MI",
            fiscal_date_ending=date(2026, 7, 30),
            reported_date=date(2026, 7, 30),
            reported_eps=1.14,
            estimated_eps=1.24,
            surprise_percentage=None,
        ),
    )

    assert "-8.06%" in body([silent], [], {})


def test_missing_values_read_as_na_rather_than_breaking() -> None:
    bare = Decision(
        "X.MI",
        Outcome.NOTIFY,
        report=EarningsReport("X.MI", date(2026, 7, 1), None, None, None, None),
    )

    text = body([bare], [], {})

    assert "n/a" in text


class TestSubject:
    def test_names_the_tickers(self) -> None:
        assert subject([BEAT], []) == "Earnings: UCG.MI"

    def test_flags_overdue_alongside_results(self) -> None:
        assert subject([BEAT], [LATE]) == "Earnings: UCG.MI (1 overdue)"

    def test_overdue_only_still_gets_a_useful_subject(self) -> None:
        assert subject([], [LATE]) == "Earnings: 1 overdue, no results"

    def test_long_lists_are_truncated(self) -> None:
        many = [Decision(f"T{i}", Outcome.NOTIFY) for i in range(5)]

        assert subject(many, []) == "Earnings: T0, T1, T2 +2"
