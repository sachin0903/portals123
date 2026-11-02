"""The Morgan Lloyd benefits seam: credential detection and the drawdown SSO response.

Drawdown needs Morgan Lloyd credentials but NO CTC credentials — ML mints the DCube link with
the loginId/ssoToken already embedded. What matters here is that a missing credential produces a
clean "not configured" (which the router turns into a 503) rather than a vendor auth error, and
that both spellings of the SSO key in the vendor docs are accepted.
"""

import logging
from typing import Any

import httpx
import pytest

from app.integrations import morgan_lloyd
from app.integrations.morgan_lloyd import benefits

# Referenced rather than inlined: a string literal assigned to `client_secret` trips ruff's
# hardcoded-credential check (S105), even in a test stub.
_PRESENT = "configured"
_ABSENT = ""


class _FakeApi:
    """Stands in for MorganLloydClient as an async context manager."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.sent: dict[str, Any] | None = None

    async def __aenter__(self) -> "_FakeApi":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def create_benefit_application(self, application: dict[str, Any]) -> dict[str, Any]:
        self.sent = application
        return self._response


def _patch_client(monkeypatch: pytest.MonkeyPatch, api: _FakeApi) -> None:
    """Stub the lazily-imported vendor client and pretend credentials are present."""
    monkeypatch.setattr(benefits, "configured", lambda: True)

    class _Settings:
        tenant_id = client_id = client_secret = _PRESENT

    monkeypatch.setattr(benefits, "_settings", lambda: _Settings())
    import morgan_lloyd.client as vendor

    monkeypatch.setattr(vendor, "MorganLloydClient", lambda settings=None: api)


class TestConfigured:
    def test_blank_credentials_read_as_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Blank:
            tenant_id = client_id = client_secret = _ABSENT

        monkeypatch.setattr(benefits, "_settings", lambda: _Blank())
        assert benefits.configured() is False

    def test_a_partial_triple_is_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A tenant id without a secret would fail at the token endpoint; refuse earlier.
        class _Partial:
            tenant_id = "t"
            client_id = "c"
            client_secret = _ABSENT

        monkeypatch.setattr(benefits, "_settings", lambda: _Partial())
        assert benefits.configured() is False

    def test_a_full_triple_is_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Full:
            tenant_id = client_id = client_secret = _PRESENT

        monkeypatch.setattr(benefits, "_settings", lambda: _Full())
        assert benefits.configured() is True


@pytest.mark.asyncio
async def test_unconfigured_raises_before_any_vendor_call(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(benefits, "configured", lambda: False)
    # The setting is LOGGED, not shown: an adviser reads this message and cannot set a credential,
    # while an operator can — and "unavailable" with nothing behind it is indistinguishable from a
    # vendor outage.
    with caplog.at_level(logging.WARNING):
        with pytest.raises(
            morgan_lloyd.MorganLloydNotConfigured, match="unavailable for this firm"
        ) as raised:
            await morgan_lloyd.drawdown_session("contact-guid")
    assert "MORGAN_LLOYD" not in str(raised.value)
    assert "MORGAN_LLOYD_TENANT_ID" in caplog.text


@pytest.mark.asyncio
async def test_accepts_the_camelcase_sso_url_from_the_example_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi({"benefitApplicationId": "ba-1", "ssoUrl": "https://dcube.test/x"})
    _patch_client(monkeypatch, api)

    result = await morgan_lloyd.drawdown_session("contact-guid")

    assert result == {"sso_url": "https://dcube.test/x", "benefit_application_id": "ba-1"}


@pytest.mark.asyncio
async def test_accepts_the_uppercase_sso_url_from_the_docs_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The docs table says ssoURL, the example JSON says ssoUrl — take whichever arrives.
    api = _FakeApi({"benefitApplicationId": "ba-2", "ssoURL": "https://dcube.test/y"})
    _patch_client(monkeypatch, api)

    result = await morgan_lloyd.drawdown_session("contact-guid")

    assert result["sso_url"] == "https://dcube.test/y"


@pytest.mark.asyncio
async def test_sends_only_the_optional_fields_that_were_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi({"ssoUrl": "https://dcube.test/z"})
    _patch_client(monkeypatch, api)

    await morgan_lloyd.drawdown_session(
        "contact-guid", scheme_ref="ML-SCH-1", asset_valuation=125000.0
    )

    assert api.sent == {
        "contactId": "contact-guid",
        "advised": True,
        "schemeRef": "ML-SCH-1",
        "assetValuation": 125000.0,
    }


@pytest.mark.asyncio
async def test_sends_the_illustration_template_when_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi({"ssoUrl": "https://dcube.test/t"})
    _patch_client(monkeypatch, api)
    xml = morgan_lloyd.drawdown_illustration_template(
        title="Mr",
        first_name="Arthur",
        surname="Bramley",
        gender="male",
        marital_status="married",
        date_of_birth="1959-04-12",
        retirement_age=65,
        fund_value=150_000.0,
        output_reference="GRAPHENE-TEST-0001",
        agent_name="Graphene Platforms (test)",
        effective_date="2026-07-14",
    )

    await morgan_lloyd.drawdown_session(
        "contact-guid",
        scheme_ref="ML-SCH-1",
        asset_valuation=150_000.0,
        cash_valuation=7_500.0,
        crystallised_value=0.0,
        illustration_template=xml,
    )

    assert api.sent is not None
    assert api.sent["illustrationTemplate"] == xml
    assert api.sent["cashValuation"] == 7_500.0
    assert api.sent["crystallisedValue"] == 0.0


def test_drawdown_kwargs_omit_cash_when_the_book_has_none() -> None:
    params = morgan_lloyd.drawdown_kwargs(
        {
            "uid": "acc000000001",
            "value": 50_000,
            "title": "Mr",
            "first_name": "Tomasz",
            "surname": "Achebe",
            "ml_scheme_ref": "MLSIPP/02039",
            "ml_policy_ref": "acc000000001",
        },
        agent_name="Graphene Platforms",
    )
    assert params["asset_valuation"] == 50_000.0
    assert "cash_valuation" not in params
    assert "crystallised_value" not in params
    xml = params["illustration_template"]
    assert "<quotation_type>Flexi Access Drawdown</quotation_type>" in xml
    assert "1960-01-01" not in xml
    assert "<sex>" not in xml


def test_drawdown_illustration_omits_unknown_sex_and_date_of_birth() -> None:
    xml = morgan_lloyd.drawdown_illustration_template(
        title="Mx",
        first_name="Tomasz",
        surname="Achebe",
        gender=None,
        marital_status=None,
        date_of_birth=None,
        retirement_age=None,
        fund_value=50_000.0,
        output_reference="GRAPHENE-TEST-0003",
        agent_name="Graphene Platforms",
        effective_date="2026-07-14",
    )
    assert "<date_of_birth>" not in xml
    assert "<sex>" not in xml
    assert "1960-01-01" not in xml
    assert "<forename>Tomasz</forename>" in xml


def test_drawdown_illustration_template_is_a_ctc1_drawdown() -> None:
    xml = morgan_lloyd.drawdown_illustration_template(
        title="Mrs",
        first_name="Isla",
        surname="Carrington",
        gender="female",
        marital_status="cohabiting",
        date_of_birth="1963-09-03",
        retirement_age=65,
        fund_value=187_500.0,
        output_reference="GRAPHENE-TEST-0002",
        agent_name="Graphene Platforms (test)",
        effective_date="2026-07-14",
    )
    assert "<message_version>CTC1</message_version>" in xml
    assert "<quotation_type>Flexi Access Drawdown</quotation_type>" in xml
    assert not xml.startswith("<?xml")
    assert "<forename>Isla</forename>" in xml
    assert "<sex>Female</sex>" in xml
    assert "<marital_status>Partnered</marital_status>" in xml
    assert "<antytxfcsh" in xml
    assert "GRAPHENE-GENERIC" in xml
    # Birthday 3 September has not arrived by 14 July, so she is 62 and 65 is still ahead.
    assert "<exit_age>65</exit_age>" in xml


def test_drawdown_illustration_for_someone_already_retired_runs_forward() -> None:
    # Born 1959, retirement age 65, quoted on 14 July 2026: already 67. Sending 65 would
    # print a term that ended in 2024.
    xml = morgan_lloyd.drawdown_illustration_template(
        title="Mr",
        first_name="Arthur",
        surname="Bramley",
        gender="male",
        marital_status="married",
        date_of_birth="1959-04-12",
        retirement_age=65,
        fund_value=150_000.0,
        output_reference="GRAPHENE-TEST-0001",
        agent_name="Graphene Platforms (test)",
        effective_date="2026-07-14",
    )
    assert "<exit_age>99</exit_age>" in xml


def test_sso_url_gains_the_policy_number_on_the_hash_query() -> None:
    url = (
        "https://digitaltest.ctc.uk.com/DCube/~MorganLloyd/"
        "#/sso?providerSSO=abc&loginId=def&ssoToken=ghi"
    )
    out = morgan_lloyd.sso_url_with_policy(url, "BkpmtLMzaEnh")
    assert out is not None
    assert "policyNumber=BkpmtLMzaEnh" in out
    assert out.startswith("https://digitaltest.ctc.uk.com/DCube/~MorganLloyd/#/sso?")


@pytest.mark.asyncio
async def test_drawdown_session_appends_the_policy_number_to_the_sso_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi(
        {
            "ssoUrl": (
                "https://digitaltest.ctc.uk.com/DCube/~MorganLloyd/"
                "#/sso?providerSSO=p&loginId=l&ssoToken=t"
            )
        }
    )
    _patch_client(monkeypatch, api)
    result = await morgan_lloyd.drawdown_session("contact-guid", policy_number="BkpmtLMzaEnh")
    assert result["sso_url"] is not None
    assert "policyNumber=BkpmtLMzaEnh" in str(result["sso_url"])


@pytest.mark.asyncio
async def test_a_response_without_an_sso_url_yields_none_rather_than_a_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = _FakeApi({"benefitApplicationId": "ba-3"})
    _patch_client(monkeypatch, api)

    result = await morgan_lloyd.drawdown_session("contact-guid")

    assert result["sso_url"] is None
    assert result["benefit_application_id"] == "ba-3"


class TestExplain:
    """Vendor failures become one safe line — never the raw Azure body, which echoes the request."""

    def _status_error(self, body: str) -> httpx.HTTPStatusError:
        request = httpx.Request("POST", "https://login.microsoftonline.com/t/oauth2/token")
        return httpx.HTTPStatusError(
            "400", request=request, response=httpx.Response(400, text=body, request=request)
        )

    def test_conditional_access_is_named_as_a_network_restriction(self) -> None:
        # The real body seen from a laptop with valid credentials. Reading this as "bad secret"
        # would send someone hunting a rotation that never happened.
        error = self._status_error(
            '{"error":"invalid_grant","error_description":"AADSTS53003: Access has been blocked '
            "by Conditional Access policies. The access policy does not allow token issuance. "
            'Trace ID: abc Correlation ID: def Timestamp: 2026-07-28 09:29:13Z"}'
        )
        message = benefits._explain(error)
        assert "AADSTS53003" in message
        assert "credentials are accepted" in message
        assert "allowlisted network" in message

    def test_a_bad_secret_is_reported_as_a_credential_rejection(self) -> None:
        error = self._status_error(
            '{"error_description":"AADSTS7000215: Invalid client secret provided. Trace ID: x"}'
        )
        message = benefits._explain(error)
        assert "AADSTS7000215" in message
        assert "rejected the Morgan Lloyd credentials" in message

    def test_a_body_with_no_aadsts_code_still_produces_a_line(self) -> None:
        assert "could not be reached" in benefits._explain(self._status_error("<html>502</html>"))

    def test_a_transport_error_has_no_response_body_to_read(self) -> None:
        message = benefits._explain(httpx.ConnectError("no route to host"))
        assert "could not be reached" in message

    def test_trace_and_correlation_ids_are_not_echoed(self) -> None:
        # They are noise to an adviser and tie the message to one request.
        error = self._status_error(
            '{"error_description":"AADSTS53003: Access has been blocked by Conditional Access '
            'policies. Trace ID: 2f6d611b Correlation ID: 589cdd5c"}'
        )
        message = benefits._explain(error)
        assert "2f6d611b" not in message and "589cdd5c" not in message
