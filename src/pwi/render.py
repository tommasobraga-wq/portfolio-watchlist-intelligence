"""Turn decisions into the text of a notification. Pure — no SMTP, no clock.

Deliberately never prints the fiscal-quarter label: it is derived from the announcement
date, so it is off by a quarter and would read as wrong. The publication date is exact and
says the same thing more usefully. See the ADR.
"""

from __future__ import annotations

from .digest import Decision
from .models import Label, TrackedTicker

QUOTE_URL = "https://finance.yahoo.com/quote/{symbol}"


def subject(published: list[Decision], overdue: list[Decision]) -> str:
    if published:
        head = ", ".join(d.symbol for d in published[:3])
        if len(published) > 3:
            head += f" +{len(published) - 3}"
        suffix = f" ({len(overdue)} overdue)" if overdue else ""
        return f"Earnings: {head}{suffix}"
    return f"Earnings: {len(overdue)} overdue, no results"


def body(
    published: list[Decision],
    overdue: list[Decision],
    labels: dict[str, Label],
) -> str:
    blocks: list[str] = []

    if published:
        blocks.append("RESULTS PUBLISHED")
        blocks.extend(_published_block(d, labels) for d in published)

    if overdue:
        blocks.append(
            "\nEXPECTED BUT NOT PUBLISHED\n"
            "The reporting date has passed and no result was found. The source may be\n"
            "missing the data rather than the company having delayed — worth a manual check."
        )
        blocks.extend(_overdue_block(d, labels) for d in overdue)

    return "\n\n".join(blocks) + "\n"


def _published_block(decision: Decision, labels: dict[str, Label]) -> str:
    report = decision.report
    assert report is not None  # NOTIFY always carries one
    lines = [f"{decision.symbol} [{_label(decision.symbol, labels)}]"]

    if report.reported_date:
        lines.append(f"  published    {report.reported_date:%d %b %Y}")
    lines.append(f"  EPS actual   {_num(report.reported_eps)}")
    lines.append(f"  EPS estimate {_num(report.estimated_eps)}")

    surprise = _surprise(report.reported_eps, report.estimated_eps, report.surprise_percentage)
    if surprise:
        lines.append(f"  surprise     {surprise}")

    lines.append(f"  source       {QUOTE_URL.format(symbol=decision.symbol)}")
    return "\n".join(lines)


def _overdue_block(decision: Decision, labels: dict[str, Label]) -> str:
    return (
        f"{decision.symbol} [{_label(decision.symbol, labels)}]\n"
        f"  expected     {decision.expected_on:%d %b %Y}"
        f" ({decision.days_overdue} days ago)\n"
        f"  source       {QUOTE_URL.format(symbol=decision.symbol)}"
    )


def _label(symbol: str, labels: dict[str, Label]) -> str:
    # The held/watching distinction is the one thing the ADR insists the notification show.
    return labels.get(symbol, Label.WATCHING).upper()


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _surprise(actual: float | None, estimate: float | None, given: float | None) -> str | None:
    """Prefer the source's own percentage; fall back to computing it.

    Yahoo leaves Surprise(%) empty on some rows even when both EPS values are present,
    and a beat is exactly what the reader wants to see at a glance.
    """
    percent = given
    if percent is None and actual is not None and estimate not in (None, 0):
        percent = (actual - estimate) / abs(estimate) * 100
    if percent is None:
        return None
    return f"{percent:+.2f}%"


def labels_of(watchlist: list[TrackedTicker]) -> dict[str, Label]:
    return {t.symbol: t.label for t in watchlist}
