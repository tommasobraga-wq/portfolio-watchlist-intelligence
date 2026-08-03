from datetime import date

import httpx
import pytest
import respx

from pwi.alphavantage import BASE_URL, AlphaVantageClient, RateLimited

EARNINGS_PAYLOAD = {
    "symbol": "IBM",
    "quarterlyEarnings": [
        {
            "fiscalDateEnding": "2025-09-30",
            "reportedDate": "2025-10-22",
            "reportedEPS": "2.30",
            "estimatedEPS": "2.11",
            "surprisePercentage": "9.0047",
        },
        {
            "fiscalDateEnding": "2025-06-30",
            "reportedDate": "2025-07-23",
            "reportedEPS": "2.80",
            "estimatedEPS": "None",
            "surprisePercentage": "None",
        },
    ],
}


@pytest.fixture
def client() -> AlphaVantageClient:
    return AlphaVantageClient("test-key", client=httpx.Client(), min_interval=0.0)


@respx.mock
def test_parses_quarters(client: AlphaVantageClient) -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=EARNINGS_PAYLOAD))

    quarters = client.earnings("IBM")

    assert len(quarters) == 2
    assert quarters[0].fiscal_date_ending == date(2025, 9, 30)
    assert quarters[0].reported_eps == 2.30
    assert quarters[0].period == "2025-Q3"
    # "None" strings are the API's way of omitting a value.
    assert quarters[1].estimated_eps is None


@respx.mock
def test_sends_api_key(client: AlphaVantageClient) -> None:
    route = respx.get(BASE_URL).mock(return_value=httpx.Response(200, json=EARNINGS_PAYLOAD))

    client.earnings("IBM")

    assert route.calls.last.request.url.params["apikey"] == "test-key"


@respx.mock
def test_uncovered_symbol_returns_empty(client: AlphaVantageClient) -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json={"symbol": "UCG.MI"}))

    assert client.earnings("UCG.MI") == []
    assert client.covers("UCG.MI") is False


@respx.mock
def test_quota_message_raises_rate_limited(client: AlphaVantageClient) -> None:
    # The quota response is HTTP 200 with an informational key, not an error status.
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json={"Information": "rate limit is 25 requests/day"})
    )

    with pytest.raises(RateLimited):
        client.earnings("IBM")
