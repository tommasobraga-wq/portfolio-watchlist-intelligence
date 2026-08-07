"""Notification text, in Italian. Pure — no SMTP, no clock.

One message per published result: the subject names the ticker, so it is recognisable in
the inbox without opening it, and each result goes out as soon as it is detected. Overdue
warnings are grouped instead — they are maintenance signals, not news, and one mail per
ticker would be noise.

A same-day heads-up with no data yet is also sent per ticker, immediately, the first time
the expected date arrives and nothing has been published — see `due_today_body`. It is
deliberately not batched like overdue: the whole point is not to wait for a periodic digest.

Never prints the fiscal-quarter label: it is derived from the announcement date and sits a
quarter off, so it would read "Q3" over Q2 figures. See the ADR.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .digest import Decision, DividendDecision, PriceReaction, is_source_stale
from .models import EarningsReport, Label, Revenue, TickerEarnings

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
    price: PriceReaction | None = None,
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
    if earnings and (yoy := _year_on_year(earnings, report)):
        lines.append(f"  anno su anno {yoy}")

    if price and price.day_move_pct is not None:
        # Beating the estimate and falling anyway is common, and invisible from EPS alone.
        lines += ["", "REAZIONE DEL TITOLO"]
        lines.append(
            f"  pubblicazione {_pct(price.day_move_pct)}"
            f"   ({_price(price.before)} → {_price(price.on_day)})"
        )
        if price.since_move_pct is not None:
            lines.append(
                f"  da allora     {_pct(price.since_move_pct)}   (→ {_price(price.latest)})"
            )

    if revenue and (revenue.actual is not None or revenue.estimate is not None):
        lines += ["", "RICAVI"]
        if revenue.actual is not None:
            lines.append(f"  consuntivo   {_money(revenue.actual)}")
        else:
            # Worth saying out loud: silence here is a source lag, not a missing result.
            lines.append("  consuntivo   non ancora disponibile presso la fonte")
        if revenue.estimate is not None:
            lines.append(f"  atteso       {_money(revenue.estimate)}")

    if earnings and (table := _recent_quarters(earnings)):
        lines += ["", "ULTIMI TRIMESTRI"] + table

    lines += ["", f"Fonte: {QUOTE_URL.format(symbol=decision.symbol)}"]
    return "\n".join(lines) + "\n"


def _year_on_year(earnings: TickerEarnings, report: EarningsReport) -> str | None:
    previous = earnings.year_before(report)
    if previous is None or previous.reported_eps is None or not report.reported_eps:
        return None
    change = (report.reported_eps - previous.reported_eps) / abs(previous.reported_eps) * 100
    return f"{_num(report.reported_eps)} contro {_num(previous.reported_eps)}   {_pct(change)}"


def _recent_quarters(earnings: TickerEarnings, limit: int = 4) -> list[str]:
    rows = []
    for r in earnings.published[:limit]:
        if r.reported_eps is None:
            continue
        against = f" su {_num(r.estimated_eps)}" if r.estimated_eps is not None else ""
        gap = _surprise(r)
        rows.append(f"  {_day(r.reported_date)}   {_num(r.reported_eps)}{against}   {gap or ''}")
    return rows if len(rows) >= 2 else []


def due_today_subject(decision: Decision, label: Label) -> str:
    return f"{decision.symbol} — risultati attesi oggi"


def due_today_body(decision: Decision, label: Label) -> str:
    lines = [
        f"{decision.symbol} — {_LABEL_IT[label]}",
        "",
        f"Risultati attesi oggi ({_day(decision.expected_on)}), non ancora pubblicati presso",
        "la fonte.",
        "",
        "Avviso preventivo, senza dati: la mail con i numeri arriverà appena la fonte li",
        "pubblica.",
        "",
        f"Fonte: {QUOTE_URL.format(symbol=decision.symbol)}",
    ]
    return "\n".join(lines) + "\n"


def dividend_subject(decision: DividendDecision, label: Label) -> str:
    dividend = decision.dividend
    assert dividend is not None
    return f"{decision.symbol} — dividendo {_share(dividend.amount)}"


def dividend_body(decision: DividendDecision, label: Label) -> str:
    dividend = decision.dividend
    assert dividend is not None
    lines = [
        f"{decision.symbol} — {_LABEL_IT[label]}",
        "",
        f"Stacco il {_day(dividend.ex_date)}.",
        f"  per azione   {_share(dividend.amount)}",
        "",
        f"Fonte: {QUOTE_URL.format(symbol=decision.symbol)}",
    ]
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
        if d.heads_up_already_sent:
            # Otherwise this reads as a second, unrelated alert about the same ticker.
            lines.append(
                "  avviso       già segnalato il giorno stesso, senza dati (vedi mail precedente)"
            )
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


def _surprise(report) -> str | None:
    """Prefer the source's own percentage; compute it when Yahoo leaves it empty."""
    percent = report.surprise_percentage
    if percent is None and report.reported_eps is not None and report.estimated_eps:
        percent = (report.reported_eps - report.estimated_eps) / abs(report.estimated_eps) * 100
    return None if percent is None else f"{percent:+.2f}%".replace(".", ",")


def _pct(value: float | None) -> str:
    return "" if value is None else f"{value:+.2f}%".replace(".", ",")


def _price(value) -> str:
    return "n.d." if value is None else f"{float(value):.2f}".replace(".", ",")


def _num(value: float | None) -> str:
    return "n.d." if value is None else f"{value:.2f}".replace(".", ",")


def _share(value: Decimal) -> str:
    """Per-share amounts run to four decimals (Yahoo reports e.g. 1.7208), unlike prices."""
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _money(value: Decimal) -> str:
    millions = value / Decimal(1_000_000)
    if millions >= 1000:
        return f"{millions / 1000:,.2f} mld".replace(",", "·").replace(".", ",").replace("·", ".")
    return f"{millions:,.0f} mln".replace(",", ".")


def _day(value) -> str:
    return "data ignota" if value is None else f"{value:%d/%m/%Y}"


def labels_of(watchlist) -> dict[str, Label]:
    return {t.symbol: t.label for t in watchlist}
