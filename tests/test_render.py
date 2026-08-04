from datetime import date
from decimal import Decimal

from pwi.digest import Decision, Outcome
from pwi.models import EarningsReport, Label, Revenue, TickerEarnings
from pwi.render import (
    overdue_body,
    overdue_subject,
    result_body,
    result_subject,
)


def report(eps: float = 1.80, estimate: float = 1.79, surprise: float | None = 0.55):
    return EarningsReport("UCG.MI", date(2026, 7, 23), date(2026, 7, 23), eps, estimate, surprise)


def decision(**kw) -> Decision:
    return Decision("UCG.MI", Outcome.NOTIFY, report=report(**kw))


HISTORY = TickerEarnings(
    "UCG.MI",
    published=(
        report(),
        report(2.09, 1.85),
        report(1.09, 1.21),
        report(1.68, 1.59),
    ),
)


class TestSubject:
    def test_a_beat_says_so_with_the_gap(self) -> None:
        assert result_subject(decision(), Label.HELD) == "UCG.MI — sopra le attese (+0,55%)"

    def test_a_miss_says_so(self) -> None:
        subject = result_subject(decision(eps=1.14, estimate=1.24, surprise=-7.66), Label.HELD)

        assert subject == "UCG.MI — sotto le attese (-7,66%)"

    def test_without_comparable_figures_it_stays_neutral(self) -> None:
        assert result_subject(decision(estimate=None, surprise=None), Label.HELD) == (
            "UCG.MI — risultati"
        )


class TestBody:
    def test_shows_the_numbers_the_date_and_the_source(self) -> None:
        text = result_body(decision(), Label.HELD)

        assert "23/07/2026" in text
        assert "1,80" in text and "1,79" in text
        assert "+0,55%" in text
        assert "https://finance.yahoo.com/quote/UCG.MI" in text

    def test_held_and_watching_are_distinguished(self) -> None:
        # The one thing the ADR insists the notification must show.
        assert "in portafoglio" in result_body(decision(), Label.HELD)
        assert "osservato" in result_body(decision(), Label.WATCHING)

    def test_fiscal_quarter_label_is_never_shown(self) -> None:
        # Derived from the announcement date and off by a quarter — it would print "Q3"
        # over Q2 figures.
        text = result_body(decision(), Label.HELD)

        assert "Q3" not in text and "Q2" not in text

    def test_track_record_summarises_recent_quarters(self) -> None:
        text = result_body(decision(), Label.HELD, earnings=HISTORY)

        assert "ha superato le attese in 3 degli ultimi 4 trimestri" in text

    def test_track_record_is_omitted_when_too_thin_to_mean_anything(self) -> None:
        thin = TickerEarnings("UCG.MI", published=(report(),))

        assert "trimestri" not in result_body(decision(), Label.HELD, earnings=thin)

    def test_missing_revenue_is_stated_as_a_source_lag(self) -> None:
        # Silence here would read as "no revenue", which is wrong and alarming.
        text = result_body(
            decision(), Label.HELD, revenue=Revenue(actual=None, estimate=Decimal("6480867890"))
        )

        assert "non ancora disponibile presso la fonte" in text
        assert "6,48 mld" in text

    def test_revenue_in_billions_is_scaled(self) -> None:
        text = result_body(decision(), Label.HELD, revenue=Revenue(actual=Decimal("7073000000")))

        assert "7,07 mld" in text

    def test_surprise_is_computed_when_the_source_omits_it(self) -> None:
        text = result_body(decision(eps=1.14, estimate=1.24, surprise=None), Label.HELD)

        assert "-8,06%" in text

    def test_missing_values_read_as_unknown_rather_than_breaking(self) -> None:
        bare = Decision(
            "X.MI",
            Outcome.NOTIFY,
            report=EarningsReport("X.MI", date(2026, 7, 1), None, None, None, None),
        )

        text = result_body(bare, Label.WATCHING)

        assert "n.d." in text
        assert "data ignota" in text


class TestOverdue:
    LATE = [
        Decision("FBK.MI", Outcome.OVERDUE, expected_on=date(2026, 11, 5), days_overdue=4),
        Decision("PRY.MI", Outcome.OVERDUE, expected_on=date(2026, 11, 3), days_overdue=6),
    ]

    def test_single_ticker_is_named_in_the_subject(self) -> None:
        assert overdue_subject(self.LATE[:1]) == "Risultati attesi e non trovati: FBK.MI"

    def test_several_are_counted(self) -> None:
        assert overdue_subject(self.LATE) == "Risultati attesi e non trovati: 2 titoli"

    def test_body_frames_it_as_possibly_a_data_gap(self) -> None:
        text = overdue_body(self.LATE, {"FBK.MI": Label.WATCHING, "PRY.MI": Label.HELD})

        assert "05/11/2026" in text and "4 giorni fa" in text
        assert "verifica manuale" in text
        assert "FBK.MI — osservato" in text
        assert "PRY.MI — in portafoglio" in text
