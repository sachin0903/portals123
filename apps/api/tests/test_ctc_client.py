"""CTC illustration engine HTTP behaviour.

No credentials are involved (the endpoint is reached with ``nodb=true`` and identifies itself
through ``<agent_name>`` in the XML), so what matters here is: we post well-formed XML with the
right content type, we surface the engine's own error text, and we never present something that
isn't a PDF as one.
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.integrations import ctc
from app.integrations.ctc.client import CtcError, generate

SUCCESS = """<message><header><response>SUCCESS</response></header>
<content><results><document_location>{location}</document_location></results></content></message>"""

REJECTED = """<message><header><response>ERROR</response><errors>
<error>Unknown Product Code</error></errors></header></message>"""


NOW = datetime(2026, 8, 3, 9, 30, tzinfo=UTC)


def _request() -> ctc.IllustrationRequest:
    return ctc.IllustrationRequest(
        effective_date="2026-07-28",
        created_date="2026-07-28 09:30:00",
        investors=[ctc.Investor(surname="Oakden", date_of_birth="1960-04-02", exit_age=67)],
        quotations=[
            ctc.Quotation(
                product_ref="SIPPEN",
                quotation_type="Accumulation",
                plan_streams=[
                    ctc.PlanStream(
                        key="EXISTING",
                        contribution_type="Existing Fund Value",
                        amount=Decimal("125000.00"),
                        frequency="Single",
                    )
                ],
            )
        ],
    )


@pytest.mark.asyncio
async def test_posts_xml_and_returns_the_fetched_pdf() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-1.7 fake")
        return httpx.Response(200, text=SUCCESS.format(location="https://ctc.test/out/ABC.pdf"))

    result = await generate(_request(), transport=httpx.MockTransport(handler))

    assert result.response.success is True
    assert result.pdf == b"%PDF-1.7 fake"
    # The engine call, then the document fetch.
    assert len(seen) == 2
    # Bare `text/xml`, no charset parameter — verified against the live engine, which answers
    # an HTML error page with HTTP 200 when a charset is present. This assertion has been
    # regressed once; it is pinned exactly rather than loosely matched for that reason.
    assert seen[0].headers["content-type"] == "text/xml"
    body = seen[0].content.decode()
    assert "<message_version>CTC1</message_version>" in body
    assert "<surname>Oakden</surname>" in body
    assert result.request_xml == body


@pytest.mark.asyncio
async def test_surfaces_engine_error_descriptions_without_fetching_anything() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=REJECTED)

    result = await generate(_request(), transport=httpx.MockTransport(handler))

    assert result.response.success is False
    assert result.response.errors == ["Unknown Product Code"]
    assert result.pdf is None
    assert calls == 1  # no attempt to fetch a document that was never produced


@pytest.mark.asyncio
async def test_a_bare_filename_is_returned_but_not_fetched() -> None:
    # The spec only promises a "name", which may be a path on a CTC share. Guessing a base URL
    # would risk filing a 404 body as an illustration, so we hand the name back instead.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SUCCESS.format(location="ABCD1234.pdf"))

    result = await generate(_request(), transport=httpx.MockTransport(handler))

    assert result.response.success is True
    assert result.response.document_location == "ABCD1234.pdf"
    assert result.pdf is None


@pytest.mark.asyncio
async def test_fetch_pdf_false_skips_the_document_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=SUCCESS.format(location="https://ctc.test/out/ABC.pdf"))

    result = await generate(_request(), fetch_pdf=False, transport=httpx.MockTransport(handler))
    assert calls == 1
    assert result.pdf is None


@pytest.mark.asyncio
async def test_transport_failure_becomes_a_ctc_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(CtcError, match="Could not reach the CTC illustration engine"):
        await generate(_request(), transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_engine_5xx_becomes_a_ctc_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    with pytest.raises(CtcError):
        await generate(_request(), transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_unreachable_document_does_not_mask_the_illustration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A generated illustration whose PDF can't be retrieved must not look like a success with a
    # zero-byte document — the fetch failure surfaces as a CtcError the router turns into a 502.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return httpx.Response(404)
        return httpx.Response(200, text=SUCCESS.format(location="https://ctc.test/out/gone.pdf"))

    with pytest.raises(CtcError):
        await generate(_request(), transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_missing_endpoint_configuration_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ctc_illustration_url", "")
    with pytest.raises(CtcError, match="CTC_ILLUSTRATION_URL is not set"):
        await generate(_request())


@pytest.mark.asyncio
async def test_an_invalid_request_never_reaches_the_network() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text=SUCCESS.format(location="x.pdf"))

    bad = ctc.IllustrationRequest(
        effective_date="2026-07-28",
        created_date="2026-07-28 09:30:00",
        investors=[],  # invalid
        quotations=[],
    )
    with pytest.raises(ctc.CtcValidationError):
        await generate(bad, transport=httpx.MockTransport(handler))
    assert called is False


def test_the_engines_reply_is_kept_verbatim_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """The raw reply is the only record of what the engine actually said.

    It used to be parsed and thrown away, which left a successful quotation carrying impossible
    figures — a growth rate of 999,996%, a fund of £0 — with no evidence to diagnose from: the
    parser only reads the fields it knows about, and none of them are the projection.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "ctc_enabled", True)
    monkeypatch.setattr(settings, "ctc_illustration_url", "https://ctc.test/quote")
    reply = SUCCESS.format(location="https://ctc.test/out/ABC.pdf").replace(
        "</message>", "<growth_rate>999996</growth_rate></message>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-1.7 real")
        return httpx.Response(200, text=reply)

    outcome = asyncio.run(
        ctc.generate_or_placeholder(_request(), transport=httpx.MockTransport(handler), now=NOW)
    )

    assert outcome.placeholder is False
    assert outcome.response_xml == reply
    # Including the element the parser has no field for — which is the whole point.
    assert "999996" in (outcome.response_xml or "")


def test_a_rejected_request_keeps_the_reply_too(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "ctc_enabled", True)
    monkeypatch.setattr(settings, "ctc_illustration_url", "https://ctc.test/quote")
    reply = (
        "<message><header><mode>Response</mode></header>"
        "<content><quotations><quotation><success>No</success>"
        "<error><description>Unknown product_ref</description></error>"
        "</quotation></quotations></content></message>"
    )
    outcome = asyncio.run(
        ctc.generate_or_placeholder(
            _request(),
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, text=reply)),
            now=NOW,
        )
    )

    assert outcome.placeholder is True
    assert outcome.response_xml == reply


def test_no_call_means_no_reply_to_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled engine: `response_xml` is None, not an empty string — nothing was said, as
    opposed to the engine answering with nothing."""
    from app.config import settings

    monkeypatch.setattr(settings, "ctc_enabled", False)
    outcome = asyncio.run(ctc.generate_or_placeholder(_request(), now=NOW))

    assert outcome.placeholder is True
    assert outcome.response_xml is None
