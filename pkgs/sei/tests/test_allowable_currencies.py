"""The add-allowable-currency body. SEI's example is the contract for the flag's type."""

from __future__ import annotations

import json

import httpx

from sei import SeiClient, SeiSettings

TOKEN_PATH = "/oauthsecurity/access_token"


def _token() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})


async def test_adding_a_currency_sends_the_override_flag_and_not_a_rate_schedule(
    settings: SeiSettings,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith(TOKEN_PATH):
            return _token()
        return httpx.Response(
            200,
            json={
                "data": {
                    "type": "AddAllowableCurrenciesResponse",
                    "attributes": {
                        "status": "SUCCESS",
                        "message": "Allowable currencies successfully added.",
                    },
                }
            },
        )

    async with SeiClient(settings, transport=httpx.MockTransport(handler)) as api:
        await api.add_allowable_currencies("3108", "AIO00213", ["EUR", "USD"])

    body = json.loads(seen[-1].content)
    attributes = body["data"]["attributes"]
    assert seen[-1].url.path.endswith(
        "/addallowablecurrencyservice/allowableCurrencies/addAllowableCurrencyToAccount"
    )
    assert attributes == {
        "accountNumber": "AIO00213",
        "allowTransactionLevelOverride": "true",
        "allowableCurrenciesList": [
            {"isoCurrencyCode": "EUR"},
            {"isoCurrencyCode": "USD"},
        ],
        "firmId": "3108",
    }
