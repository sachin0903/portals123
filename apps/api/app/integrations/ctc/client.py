"""HTTP access to the CTC illustration calculation engine.

The engine takes a CTC1 XML message by POST and answers with an XML reply naming the PDF it
generated (``results/document_location``). There are **no credentials**: the endpoint is reached
with ``nodb=true`` and the request identifies itself through ``<agent_name>`` and the product
codes inside the XML. Access is controlled by CTC at the network level, so a failure here is a
connectivity or schema problem, never an auth one.

Endpoint: ``docs/morgan-lloyd/api-reference.md`` (overridable via ``CTC_ILLUSTRATION_URL``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from api_log import ApiCall, Recorder, default_recorder, record

from app.config import settings
from app.integrations.ctc.placeholder import render_placeholder_pdf
from app.integrations.ctc.xml_builder import (
    IllustrationRequest,
    IllustrationResponse,
    build_request,
    parse_response,
)


class CtcError(RuntimeError):
    """The engine could not be reached, or replied with something unusable."""


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IllustrationResult:
    """Outcome of a generation attempt."""

    response: IllustrationResponse
    """The parsed engine reply (success flag, document_location, error descriptions)."""
    pdf: bytes | None
    """The fetched PDF, when ``document_location`` was a retrievable URL."""
    request_xml: str
    """Exactly what we sent -- kept so a rejected illustration can be diagnosed."""
    response_xml: str | None = None
    """Exactly what the engine replied, verbatim and unparsed.

    Kept because the parsed view is not enough to diagnose with: a reply can be `success` with
    figures that are wrong, and the parser only reads the fields it knows about. When an
    illustration comes back with a projection nobody can explain, this is the only record of what
    the engine actually said.
    """


def _looks_like_url(location: str) -> bool:
    return location.startswith(("http://", "https://"))


async def generate(
    request: IllustrationRequest,
    *,
    fetch_pdf: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
    recorder: Recorder | None = None,
) -> IllustrationResult:
    """Send an illustration request and, when the engine returns a retrievable location, fetch
    the PDF.

    ``document_location`` is documented only as "name of the PDF files that has been generated",
    so it may be a bare filename on a CTC share rather than a URL. We fetch it when it is a URL
    and otherwise hand the name back untouched -- the caller decides what to do with it, rather
    than this module guessing at a base path and silently producing a 404 body as a "PDF".

    ``transport`` is a test seam (``httpx.MockTransport``); production passes nothing.

    ``recorder``, when supplied, is handed one ``ApiCall`` -- passed in rather than reached for,
    so this module stays free of a database dependency like the other vendor clients. When it is
    not supplied the process-wide default is used, which is what the API's lifespan installs.
    """
    xml = build_request(request)
    url = settings.ctc_illustration_url
    if not url:
        raise CtcError("CTC_ILLUSTRATION_URL is not set.")

    # Recorded under Morgan Lloyd, service 'ctc'. CTC is reached as one leg of the ML
    # illustration flow and a firm thinks of it as "Morgan Lloyd produced my illustration",
    # not as a fourth counterparty -- but `service` keeps it separable.
    #
    # `ok` is emphatically NOT `status < 400` here: this engine answers 200 with an HTML error
    # page, so success is whether the parsed reply says so.
    call = ApiCall(
        provider="morgan-lloyd",
        service="ctc",
        method="POST",
        url=url,
        operation="run_quote",
        request={"xml": xml},
        entity_type="illustration",
        # The reply carries `document_location`, the only handle on the PDF the engine just
        # produced. If filing it as a document then fails, this row is where it is recovered
        # from -- so it survives the "drop successful responses" rule.
        keep_response=True,
    )

    with record(recorder or default_recorder(), call):
        try:
            async with httpx.AsyncClient(
                timeout=settings.ctc_timeout, transport=transport
            ) as client:
                reply = await client.post(
                    url,
                    content=xml.encode("utf-8"),
                    # Exactly `text/xml`, with NO charset parameter. Load-bearing, and verified
                    # against the live engine: the same document that returns a real quotation
                    # under `text/xml` returns an HTML error page ("An error has occurred. [EXC]")
                    # under `text/xml; charset=utf-8`, with HTTP 200. `application/xml` fails the
                    # same way. Since `generate_or_placeholder` turns any failure into a
                    # placeholder, getting this wrong does not raise — it silently files a
                    # stand-in for every illustration. The encoding still travels in the XML
                    # prolog, which is where a parser is meant to look for it.
                    headers={"content-type": "text/xml", "accept": "text/xml"},
                )
                reply.raise_for_status()
                reply_text = reply.text
                call.status_code = reply.status_code
                call.response = {"xml": reply_text[:8000]}
                parsed = parse_response(reply_text)
                call.ok = parsed.success
                if not parsed.success:
                    call.error = "; ".join(parsed.errors) or "engine rejected the request"

                pdf: bytes | None = None
                if fetch_pdf and parsed.success and parsed.document_location:
                    if _looks_like_url(parsed.document_location):
                        doc = await client.get(parsed.document_location)
                        doc.raise_for_status()
                        pdf = doc.content
        except httpx.HTTPError as exc:
            # Several httpx errors (ReadError, RemoteProtocolError) stringify to nothing,
            # which left the adviser-facing reason reading "...engine: ." Name it instead.
            detail = str(exc).strip() or type(exc).__name__
            call.error = detail
            raise CtcError(f"Could not reach the CTC illustration engine: {detail}") from exc

    logger.debug("ctc request xml: %s", xml)
    logger.debug("ctc response xml: %s", reply_text)
    return IllustrationResult(response=parsed, pdf=pdf, request_xml=xml, response_xml=reply_text)


@dataclass(slots=True)
class IllustrationOutcome:
    """What the caller files. There is always a PDF: either the engine's, or a placeholder."""

    pdf: bytes
    request_xml: str
    response_xml: str | None
    placeholder: bool
    placeholder_reason: str | None = None
    engine_errors: list[str] = field(default_factory=list)
    """The engine's own descriptions, when it answered and rejected the request."""
    document_location: str | None = None


def _sentence(text: str) -> str:
    """The reason is printed on the illustration and read by an adviser, so it ends like prose."""
    text = text.strip()
    return text if not text or text[-1] in ".!?" else f"{text}."


def _deadline() -> float:
    """Hard ceiling on the whole attempt (engine POST + document fetch).

    ``ctc_timeout`` is httpx's per-operation budget, so two sequential requests can otherwise take
    twice it. An adviser watching a spinner needs one bound, not two.
    """
    return max(settings.ctc_deadline, 1.0)


async def generate_or_placeholder(
    request: IllustrationRequest,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    now: datetime | None = None,
    recorder: Recorder | None = None,
) -> IllustrationOutcome:
    """Produce an illustration, falling back to a marked placeholder rather than failing.

    The CTC1 message is built and schema-validated first, so a request we could never legitimately
    send still fails loudly (``CtcValidationError``) instead of quietly producing a placeholder.
    Everything after that -- the engine being switched off, unreachable, slow, rejecting the
    request, or naming a document we cannot fetch -- yields a placeholder carrying the reason.

    The real path takes precedence whenever ``CTC_ENABLED`` is set and the engine answers with a
    retrievable PDF.
    """
    xml = build_request(request)
    stamped = now or datetime.now(UTC)

    def fallback(
        reason: str,
        *,
        errors: list[str] | None = None,
        location: str | None = None,
        response_xml: str | None = None,
    ) -> IllustrationOutcome:
        return IllustrationOutcome(
            pdf=render_placeholder_pdf(
                request,
                reason=reason,
                engine_errors=errors,
                document_location=location,
                request_xml=xml,
                now=stamped,
            ),
            request_xml=xml,
            response_xml=response_xml,
            placeholder=True,
            placeholder_reason=reason,
            engine_errors=errors or [],
            document_location=location,
        )

    if not settings.ctc_enabled:
        return fallback(
            "The CTC calculation engine is not enabled in this environment, so no quotation was "
            "requested (set CTC_ENABLED to call it)."
        )
    if not settings.ctc_illustration_url:
        return fallback("No CTC endpoint is configured (CTC_ILLUSTRATION_URL).")

    try:
        async with asyncio.timeout(_deadline()):
            result = await generate(request, transport=transport, recorder=recorder)
    except TimeoutError:
        return fallback(
            f"The CTC calculation engine did not answer within {_deadline():.0f} seconds."
        )
    except CtcError as exc:
        # Already reads as a sentence about the engine; prefixing it would say it twice.
        return fallback(_sentence(str(exc)))
    # Deliberately broad: this function's whole purpose is that nothing on the far side of an
    # external call can stop an illustration being produced. CancelledError is a BaseException,
    # so a real shutdown still propagates.
    except Exception as exc:  # noqa: BLE001
        return fallback(f"The CTC calculation engine could not be reached: {_sentence(str(exc))}")

    if result.response.success and result.pdf is not None:
        return IllustrationOutcome(
            pdf=result.pdf,
            request_xml=result.request_xml,
            response_xml=result.response_xml,
            placeholder=False,
            document_location=result.response.document_location,
        )

    if not result.response.success:
        return fallback(
            "The CTC calculation engine rejected the request.",
            errors=result.response.errors,
            location=result.response.document_location,
            response_xml=result.response_xml,
        )
    return fallback(
        "The CTC calculation engine produced an illustration but named a document that could not "
        "be retrieved.",
        errors=result.response.errors,
        location=result.response.document_location,
        response_xml=result.response_xml,
    )
