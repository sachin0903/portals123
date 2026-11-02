"""The placeholder illustration path.

Two things are being protected here. First, that an illustration is always produced -- the engine
being off, slow, unreachable, rejecting the request, or naming an unfetchable document must all end
in a filed PDF rather than a dead end. Second, and more important, that the stand-in can never be
mistaken for a quotation: it says PLACEHOLDER, it carries the reason, and a request we could not
legitimately send is still rejected outright rather than papered over.
"""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.config import settings
from app.integrations import ctc
from app.integrations.ctc.placeholder import _wrap, render_placeholder_pdf

SUCCESS = """<message><header><response>SUCCESS</response></header>
<content><results><document_location>{location}</document_location></results></content></message>"""

REJECTED = """<message><header><response>ERROR</response><errors>
<error>Specified fund not recognised. LU0278930234</error></errors></header></message>"""

NOW = datetime(2026, 7, 29, 14, 5, tzinfo=UTC)


def _request(**over: object) -> ctc.IllustrationRequest:
    return ctc.IllustrationRequest(
        effective_date="2026-07-29",
        created_date="2026-07-29 14:05:00",
        output_reference="CON412-20260729140500",
        client_type="Individual",
        investors=[
            ctc.Investor(
                title="Mrs",
                forename="Aoife",
                surname="Brennan",
                life_type="None",
                date_of_birth="1968-03-11",
                exit_age=67,
            )
        ],
        quotations=[
            ctc.Quotation(
                product_ref="SIPPEN",
                product_type="Self Invested Personal Pension",
                quotation_type="Accumulation",
                pension=True,
                plan_streams=[
                    ctc.PlanStream(
                        key="EXISTING",
                        contribution_type="Existing Fund Value",
                        amount=Decimal("184500.00"),
                        frequency="Single",
                    ),
                    ctc.PlanStream(
                        key="MEMBER",
                        contribution_type="Member",
                        amount=Decimal("750.00"),
                        frequency="Monthly",
                        net_gross="Gross",
                    ),
                ],
                annuity_basis=ctc.AnnuityBasis(
                    annuity_frequency="Monthly", annuity_timing="Advance", escalation="Level"
                ),
            )
        ],
        **over,  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def _engine_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to the staging shape: the engine is not enabled."""
    monkeypatch.setattr(settings, "ctc_enabled", False)


# ── the artefact ──────────────────────────────────────────────────────────────


def test_the_pdf_is_a_valid_single_page_pdf() -> None:
    pdf = render_placeholder_pdf(_request(), reason="Engine off.", request_xml="<x/>", now=NOW)

    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    # A reader needs the xref offsets to resolve; a truncated table is the classic hand-rolled bug.
    assert pdf.count(b" 0 obj") == 6
    assert b"/Type /Catalog" in pdf
    assert b"/Count 1" in pdf
    start = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
    assert pdf[start : start + 4] == b"xref"
    assert pdf[int(pdf.split(b"xref\n0 7\n0000000000 65535 f \n")[1][:10]) :].startswith(b"1 0 obj")


def test_the_page_says_placeholder_and_carries_the_reason() -> None:
    pdf = render_placeholder_pdf(
        _request(),
        reason="The CTC calculation engine is not enabled in this environment.",
        request_xml="<x/>",
        now=NOW,
    )

    assert b"(PLACEHOLDER ILLUSTRATION) Tj" in pdf
    assert b"Not a quotation. No projected values. Do not issue to a client." in pdf
    assert b"(PLACEHOLDER) Tj" in pdf  # the watermark
    assert b"not enabled in this environment." in pdf
    assert b"Graphene placeholder" in pdf


def test_the_page_shows_the_request_it_stands_in_for() -> None:
    pdf = render_placeholder_pdf(_request(), reason="Engine off.", request_xml="<x/>", now=NOW)

    assert b"Mrs Aoife Brennan" in pdf
    assert b"1968-03-11" in pdf
    assert b"Self Invested Personal Pension" in pdf
    assert b"CON412-20260729140500" in pdf
    # Contributions are shown as the request states them, not projected forward.
    assert b"184,500.00" in pdf
    assert b"750.00" in pdf
    assert b"Monthly" in pdf


def test_engine_messages_are_printed_on_the_page() -> None:
    pdf = render_placeholder_pdf(
        _request(),
        reason="The CTC calculation engine rejected the request.",
        engine_errors=["Specified fund not recognised. LU0278930234"],
        request_xml="<x/>",
        now=NOW,
    )
    assert b"LU0278930234" in pdf


def test_parentheses_and_non_latin_text_cannot_corrupt_the_stream() -> None:
    """An unescaped ')' from a client's name would end the PDF string early and break the file."""
    request = _request()
    request.investors[0].surname = "O)Neill (né Ó Néill) \\ 東京"

    pdf = render_placeholder_pdf(request, reason="Engine off.", request_xml="", now=NOW)

    assert b"O\\)Neill \\(n" in pdf
    assert pdf.rstrip().endswith(b"%%EOF")
    # Nothing unrepresentable leaks through as raw bytes: every text operator is balanced.
    body = pdf.split(b"stream\n", 1)[1].split(b"\nendstream", 1)[0]
    assert body.count(b" Tj") == body.count(b"BT ")


def test_wrap_never_exceeds_the_line_budget() -> None:
    lines = _wrap("word " * 200, 9.5, 511)
    assert lines
    assert max(len(line) for line in lines) <= int(511 / (9.5 * 0.5))


# ── the fallback decision ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_engine_files_a_placeholder_without_calling_out() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=SUCCESS.format(location="https://ctc.test/a.pdf"))

    outcome = await ctc.generate_or_placeholder(
        _request(), transport=httpx.MockTransport(handler), now=NOW
    )

    assert calls == 0  # switched off means not reached, not "reached and ignored"
    assert outcome.placeholder is True
    assert outcome.placeholder_reason is not None
    assert "CTC_ENABLED" in outcome.placeholder_reason
    assert outcome.pdf.startswith(b"%PDF")
    # The request is still built and returned — it is the thing worth showing.
    assert "<message_version>CTC1</message_version>" in outcome.request_xml
    assert "<surname>Brennan</surname>" in outcome.request_xml


@pytest.mark.asyncio
async def test_an_enabled_engine_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ctc_enabled", True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".pdf"):
            return httpx.Response(200, content=b"%PDF-1.7 the real thing")
        return httpx.Response(200, text=SUCCESS.format(location="https://ctc.test/out/ABC.pdf"))

    outcome = await ctc.generate_or_placeholder(
        _request(), transport=httpx.MockTransport(handler), now=NOW
    )

    assert outcome.placeholder is False
    assert outcome.pdf == b"%PDF-1.7 the real thing"
    assert outcome.document_location == "https://ctc.test/out/ABC.pdf"
    assert outcome.placeholder_reason is None


@pytest.mark.asyncio
async def test_an_unreachable_engine_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ctc_enabled", True)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    outcome = await ctc.generate_or_placeholder(
        _request(), transport=httpx.MockTransport(handler), now=NOW
    )

    assert outcome.placeholder is True
    assert outcome.placeholder_reason is not None
    # The engine's own message, punctuated as a sentence and not prefixed twice.
    assert outcome.placeholder_reason.startswith("Could not reach the CTC illustration engine:")
    assert outcome.placeholder_reason.endswith("no route to host.")
    assert outcome.pdf.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_a_silent_failure_is_named_rather_than_left_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """httpx's ReadError stringifies to nothing, and the reason is printed on the PDF."""
    monkeypatch.setattr(settings, "ctc_enabled", True)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("")

    outcome = await ctc.generate_or_placeholder(
        _request(), transport=httpx.MockTransport(handler), now=NOW
    )

    assert outcome.placeholder_reason == ("Could not reach the CTC illustration engine: ReadError.")


@pytest.mark.asyncio
async def test_a_hanging_engine_is_cut_off_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the deadline: nobody watches a spinner while a third party thinks about it."""
    monkeypatch.setattr(settings, "ctc_enabled", True)
    monkeypatch.setattr(settings, "ctc_deadline", 1.0)

    async def handler(request: httpx.Request) -> httpx.Response:
        import asyncio

        await asyncio.sleep(30)
        return httpx.Response(200, text=SUCCESS.format(location="https://ctc.test/a.pdf"))

    started = datetime.now(UTC)
    outcome = await ctc.generate_or_placeholder(
        _request(), transport=httpx.MockTransport(handler), now=NOW
    )
    elapsed = (datetime.now(UTC) - started).total_seconds()

    assert elapsed < 5
    assert outcome.placeholder is True
    assert outcome.placeholder_reason is not None
    assert "did not answer within 1 seconds" in outcome.placeholder_reason


@pytest.mark.asyncio
async def test_a_rejection_keeps_the_engine_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ctc_enabled", True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=REJECTED)

    outcome = await ctc.generate_or_placeholder(
        _request(), transport=httpx.MockTransport(handler), now=NOW
    )

    assert outcome.placeholder is True
    assert outcome.engine_errors == ["Specified fund not recognised. LU0278930234"]
    assert b"LU0278930234" in outcome.pdf


@pytest.mark.asyncio
async def test_an_unfetchable_document_location_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    """The open question in docs: `document_location` may be a bare filename on a CTC share."""
    monkeypatch.setattr(settings, "ctc_enabled", True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SUCCESS.format(location="ABCD1234.pdf"))

    outcome = await ctc.generate_or_placeholder(
        _request(), transport=httpx.MockTransport(handler), now=NOW
    )

    assert outcome.placeholder is True
    assert outcome.document_location == "ABCD1234.pdf"
    assert b"ABCD1234.pdf" in outcome.pdf


@pytest.mark.asyncio
async def test_an_invalid_request_still_fails_loudly() -> None:
    """A placeholder is a fallback for the engine being absent, never for a bad request."""
    request = _request()
    request.investors[0].surname = "B" * 80  # the spec caps a surname at 50

    with pytest.raises(ctc.CtcValidationError):
        await ctc.generate_or_placeholder(request, now=NOW)
