"""Notification text, in Italian. Pure — no SMTP, no clock.

One message per published result: the subject names the ticker, so it is recognisable in
the inbox without opening it, and each result goes out as soon as it is detected. Overdue
warnings are grouped instead — they are maintenance signals, not news, and one mail per
ticker would be noise.

Never prints the fiscal-quarter label: it is derived from the announcement date and sits a
quarter off, so it would read "Q3" over Q2 figures. See the ADR.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .digest import Decision, is_source_stale
from .models import Label, Revenue, TickerEarnings

QUOTE_URL = "https://finance.yahoo.com/quote/{symbol}"
_LABEL_IT = {Label.HELD: "in portafoglio", Label.WATCHING: "osservato"}


def result_subject(decision: Decision, label: Label) -> str:
    report = decision.report
    assert report is not None
    verdict = {True: "sopra le attese", False: "sotto le attese", None: "risultati"}[report.beat]
    surprise = _surprise(report)
    tail = f" ({surprise})" if surprise else ""
    return f"{decision.symbol} — {verdict}{tail}"


def result_body(
    decision: Decision,
    label: Label,
    earnings: TickerEarnings | None = None,
    revenue: Revenue | None = None,
) -> str:
    report = decision.report
    assert report is not None
    lines = [
        f"{decision.symbol} — {_LABEL_IT[label]}",
        "",
        f"Risultati pubblicati il {_day(report.reported_date)}.",
        "",
        "UTILE PER AZIONE",
        f"  consuntivo   {_num(report.reported_eps)}",
        f"  atteso       {_num(report.estimated_eps)}",
    ]
    if surprise := _surprise(report):
        lines.append(f"  scarto       {surprise}")

    if revenue and (revenue.actual is not None or revenue.estimate is not None):
        lines += ["", "RICAVI"]
        if revenue.actual is not None:
            lines.append(f"  consuntivo   {_money(revenue.actual)}")
        else:
            # Worth saying out loud: silence here is a source lag, not a missing result.
            lines.append("  consuntivo   non ancora disponibile presso la fonte")
        if revenue.estimate is not None:
            lines.append(f"  atteso       {_money(revenue.estimate)}")

    if earnings and (record := _track_record(earnings)):
        lines += ["", "STORICO", f"  {record}"]

    lines += ["", f"Fonte: {QUOTE_URL.format(symbol=decision.symbol)}"]
    return "\n".join(lines) + "\n"


def overdue_subject(decisions: list[Decision]) -> str:
    if len(decisions) == 1:
        return f"Risultati attesi e non trovati: {decisions[0].symbol}"
    return f"Risultati attesi e non trovati: {len(decisions)} titoli"


def overdue_body(
    decisions: list[Decision],
    labels: dict[str, Label],
    today: date | None = None,
) -> str:
    today = today or date.today()
    lines = [
        "La data prevista è passata e non risulta alcun risultato pubblicato.",
        "",
    ]
    for d in decisions:
        label = labels.get(d.symbol, Label.WATCHING)
        lines += [
            f"{d.symbol} — {_LABEL_IT[label]}",
            f"  attesi il    {_day(d.expected_on)} ({d.days_overdue} giorni fa)",
        ]
        if is_source_stale(d, today):
            # A years-old last figure is not a delay: the source stopped publishing this
            # security's results. Saying so turns a vague prompt into a specific one.
            lines += [
                "  causa        la fonte non pubblica i consuntivi di questo titolo",
                f"               dal {d.source_last_seen:%m/%Y} — è una lacuna nota,",
                "               non un rinvio della società",
            ]
        else:
            lines.append("  causa        rinvio della società o dati non ancora caricati")
        lines += [f"  fonte        {QUOTE_URL.format(symbol=d.symbol)}", ""]

    lines.append("In entrambi i casi vale una verifica manuale sul sito della società.")
    return "\n".join(lines) + "\n"


def _track_record(earnings: TickerEarnings) -> str | None:
    beats, total = earnings.track_record()
    if total < 2:
        return None
    return f"ha superato le attese in {beats} degli ultimi {total} trimestri"


def _surprise(report) -> str | None:
    """Prefer the source's own percentage; compute it when Yahoo leaves it empty."""
    percent = report.surprise_percentage
    if percent is None and report.reported_eps is not None and report.estimated_eps:
        percent = (report.reported_eps - report.estimated_eps) / abs(report.estimated_eps) * 100
    return None if percent is None else f"{percent:+.2f}%".replace(".", ",")


def _num(value: float | None) -> str:
    return "n.d." if value is None else f"{value:.2f}".replace(".", ",")


def _money(value: Decimal) -> str:
    millions = value / Decimal(1_000_000)
    if millions >= 1000:
        return f"{millions / 1000:,.2f} mld".replace(",", "·").replace(".", ",").replace("·", ".")
    return f"{millions:,.0f} mln".replace(",", ".")


def _day(value) -> str:
    return "data ignota" if value is None else f"{value:%d/%m/%Y}"


def labels_of(watchlist) -> dict[str, Label]:
    return {t.symbol: t.label for t in watchlist}
