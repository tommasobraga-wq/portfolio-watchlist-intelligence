"""Direct REST client for Alpha Vantage.

Deliberately *not* using Alpha Vantage's official MCP server: the ADR assigns this
integration the raw-REST role (auth, rate limiting, parsing). Do not replace it with an
MCP client.
"""

from __future__ import annotations

import time
from datetime import date, datetime

import httpx

from .models import EarningsReport

BASE_URL = "https://www.alphavantage.co/query"

# Free tier: 25 requests/day. There is no documented per-second cap, but a small gap
# between calls keeps us clear of burst throttling on a batch run.
MIN_SECONDS_BETWEEN_CALLS = 1.0


class AlphaVantageError(RuntimeError):
    pass


class RateLimited(AlphaVantageError):
    """Raised when the daily/burst quota is exhausted; the caller should stop the batch."""


class AlphaVantageClient:
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        min_interval: float = MIN_SECONDS_BETWEEN_CALLS,
    ) -> None:
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=30.0)
        # An injected client belongs to the caller, who may reuse it after we are done.
        self._owns_client = client is None
        self._min_interval = min_interval
        self._last_call: float | None = None

    def _throttle(self) -> None:
        if self._last_call is not None:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    def _get(self, params: dict[str, str]) -> dict:
        self._throttle()
        response = self._client.get(BASE_URL, params={**params, "apikey": self._api_key})
        response.raise_for_status()
        payload = response.json()

        # Alpha Vantage signals quota and error states with HTTP 200 and a message key.
        if "Note" in payload or "Information" in payload:
            raise RateLimited(payload.get("Note") or payload["Information"])
        if "Error Message" in payload:
            raise AlphaVantageError(payload["Error Message"])
        return payload

    def earnings(self, symbol: str) -> list[EarningsReport]:
        """Reported quarters, newest first. Empty when the symbol is not covered."""
        payload = self._get({"function": "EARNINGS", "symbol": symbol})
        quarters = payload.get("quarterlyEarnings") or []
        return [_parse_quarter(symbol, raw) for raw in quarters]

    def covers(self, symbol: str) -> bool:
        """Whether Alpha Vantage returns any earnings history for `symbol`.

        Phase 1 exists to answer this for Milan-listed tickers (UCG.MI, FBK.MI, ...);
        the ADR treats their coverage as unverified.
        """
        return bool(self.earnings(symbol))

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> AlphaVantageClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _parse_quarter(symbol: str, raw: dict) -> EarningsReport:
    return EarningsReport(
        symbol=symbol,
        fiscal_date_ending=_parse_date(raw["fiscalDateEnding"]),
        reported_date=_optional_date(raw.get("reportedDate")),
        reported_eps=_optional_float(raw.get("reportedEPS")),
        estimated_eps=_optional_float(raw.get("estimatedEPS")),
        surprise_percentage=_optional_float(raw.get("surprisePercentage")),
    )


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _optional_date(value: str | None) -> date | None:
    if not value or value == "None":
        return None
    return _parse_date(value)


def _optional_float(value: str | None) -> float | None:
    # Missing numerics come back as the literal string "None".
    if value in (None, "", "None"):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except ValueError:
        return None
