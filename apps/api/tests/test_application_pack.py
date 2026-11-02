"""The onboarding application PDF: questions, answers, the terms, and the signature."""

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi import HTTPException

from app.auth import Principal
from app.db import FirmContext
from app.flows.application_pack import assemble_pack, render_application_html
from app.permissions import CASES_READ, CONTACTS_READ_ALL
from app.routers import applications

GENERATED = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
SIGNED = datetime(2026, 9, 17, 15, 2, 16, tzinfo=UTC)

_ABOUT = [
    {"field_key": "first_name", "label": "First name", "section": "About you", "sort_order": 1},
    {
        "field_key": "dob",
        "label": "Date of birth",
        "section": "About you",
        "sort_order": 2,
    },
    {
        "field_key": "bank_style",
        "label": "Account type",
        "section": "Bank account",
        "sort_order": 3,
        "options": {"options": [{"value": "international", "label": "International"}]},
    },
]


def _pack(**overrides: Any):
    base: dict[str, Any] = {
        "firm_name": "Swissential",
        "uid": "tLNYH2mSjdKK",
        "status": "completed",
        "custody": "offshore",
        "adviser_name": "Carla Roux",
        "adviser_email": "croux@swissential.com",
        "created_at": datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
        "generated_at": GENERATED,
        "support_email": "enquiries@swissential.com",
        "support_phone": "+41 (0) 22 786 20 20",
        "profiles": [
            {
                "case_id": 1,
                "contact_id": 9,
                "uid": "mbATERFhaHQ1",
                "contact_type": "individual",
                "name": "Jonathan Berrar",
                "title": "Mr",
                "email": "berrarjonathan@yahoo.fr",
                "address": {"line1": "Route de Divonne 29", "city": "Nyon", "postcode": "1260"},
                "bank_account": {"name": "Jonathan Berrar", "iban": "LT053250037515861114"},
            }
        ],
        "accounts": [
            {
                "case_id": 2,
                "account_id": 4,
                "name": "Jonathan Berrar GIA",
                "product_type": "GIA",
                "currency": "EUR",
                "investment_capacity": "discretionary",
                "model_name": "New Horizon",
                "dividend_treatment": "reinvest",
                "custody": "offshore",
                "parties": [{"contact_type": "individual"}],
                "transfer": {
                    "intent": True,
                    "transfer_type": "cash",
                    "amount": 10000,
                    "provider_name": "Old scheme",
                },
            }
        ],
        "steps": [
            {
                "case_id": 1,
                "kind": "profile",
                "subject_id": 9,
                "step_type": "fill_form",
                "title": "About you",
                "status": "fulfilled",
                "form_uid": "frm-core",
                "answers": {
                    "first_name": "Jonathan",
                    "dob": "1982-03-18",
                    "bank_style": "international",
                },
            },
            {
                "case_id": 1,
                "kind": "profile",
                "subject_id": 9,
                "step_type": "verify_identity",
                "status": "fulfilled",
            },
            {
                "case_id": 1,
                "kind": "profile",
                "subject_id": 9,
                "step_type": "aml_check",
                "status": "fulfilled",
            },
            {
                "case_id": 2,
                "kind": "account",
                "step_type": "accept_terms",
                "title": "General declaration",
                "status": "fulfilled",
                "participant_subject_id": 9,
                "participant_name": "Jonathan Berrar",
                "term_uid": "term-general",
                "document_name": "General declaration",
                "terms_html": (
                    "<p>I agree to be bound by them.</p><script>alert(1)</script>"
                    '<img src=x onerror="alert(2)">'
                ),
                "completed_at": SIGNED,
            },
        ],
        "fields_by_form": {"frm-core": _ABOUT},
        "fees_by_account": {
            4: [
                {
                    "name": "Asset Management Fee - 0.5% + VAT",
                    "category": "management",
                    "frequency": "ongoing",
                    "fee_type": "percentage",
                    "rule": {"rate": 0.54},
                }
            ]
        },
        "acceptances": [
            {
                "contact_id": 9,
                "accepted_at": SIGNED,
                "channel": "portal",
                "term_uid": "term-general",
                "title": "General declaration",
                "body_kind": "html",
                "body_html": "<p>version copy</p>",
            }
        ],
    }
    base.update(overrides)
    return assemble_pack(**base)


def test_the_pack_is_the_questions_the_terms_and_the_signature() -> None:
    html = render_application_html(_pack())
    assert "Account opening application" in html
    assert 'class="letterhead"' in html
    assert 'class="pill ok"' in html
    assert "Client data" in html
    assert "Swissential" in html
    assert "tLNYH2mSjdKK" in html
    assert "enquiries@swissential.com" in html
    assert "First name" in html and "Jonathan" in html
    assert "Date of birth" in html and "18 March 1982" in html
    assert "International" in html  # the option label, not the stored key
    assert "bank_style" not in html
    # Asked by the form, so the contact record does not repeat the name. The address was not.
    assert "Route de Divonne 29, Nyon, 1260" in html
    assert "LT053250037515861114" in html
    assert "Account 1 — GIA" in html
    assert "Discretionary" in html
    assert "Reinvest" in html
    assert "Re-investment" not in html
    assert "Old scheme" in html
    assert "0.54%" in html
    assert "I agree to be bound by them." in html
    assert "alert(1)" not in html
    assert "onerror" not in html
    assert "Signed by" in html and "Jonathan Berrar" in html
    assert "17 September 2026" in html
    assert "2026-09-17 15:02:16 UTC" in html
    assert "Portal" in html
    assert "Identity verified" in html
    assert html.count('class="tick"') == 4
    assert "javascript:" not in html


def test_a_dotted_bank_answer_is_not_printed_again_from_the_contact() -> None:
    html = render_application_html(
        _pack(
            steps=[
                {
                    "case_id": 1,
                    "kind": "profile",
                    "subject_id": 9,
                    "step_type": "fill_form",
                    "title": "About you",
                    "status": "fulfilled",
                    "form_uid": "frm-core",
                    "answers": {
                        "bank_account.name": "Jonathan Berrar",
                        "bank_account.iban": "LT053250037515861114",
                    },
                }
            ],
            fields_by_form={
                "frm-core": [
                    {
                        "field_key": "bank_account.name",
                        "label": "Account name",
                        "section": "Bank account",
                        "sort_order": 1,
                    },
                    {
                        "field_key": "bank_account.iban",
                        "label": "IBAN",
                        "section": "Bank account",
                        "sort_order": 2,
                    },
                ]
            },
        )
    )
    assert html.count("LT053250037515861114") == 1


def test_a_name_cannot_break_out_of_the_document() -> None:
    html = render_application_html(_pack(firm_name='<script>alert(1)</script> "quoted"'))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&quot;quoted&quot;" in html


def test_the_running_header_leads_with_the_firms_logo_else_its_name() -> None:
    logo = "data:image/png;base64,iVBORw0KGgo="
    html = render_application_html(_pack(logo=logo))
    firm = html.split('<div class="pack-firm">', 1)[1].split("</div>", 1)[0]
    assert f'src="{logo}"' in firm
    assert 'alt="Swissential"' in firm
    when = html.split('<div class="pack-when">', 1)[1].split("</div>", 1)[0]
    assert "<img" not in when
    assert '<div class="pack-firm">Swissential</div>' in render_application_html(_pack())


def test_an_unsigned_declaration_is_not_a_signature() -> None:
    html = render_application_html(
        _pack(
            steps=[
                {
                    "case_id": 2,
                    "kind": "account",
                    "step_type": "accept_terms",
                    "title": "General declaration",
                    "status": "pending",
                    "participant_subject_id": 9,
                    "term_uid": "term-general",
                    "terms_html": "<p>I agree.</p>",
                    "completed_at": SIGNED,
                }
            ],
            acceptances=[],
        )
    )
    assert "Not yet signed" in html
    assert "Signed by" not in html
    assert "2026-09-17 15:02:16 UTC" not in html
    assert "I agree." in html


def test_a_pdf_term_is_named_and_not_given_a_body() -> None:
    html = render_application_html(
        _pack(
            steps=[
                {
                    "case_id": 2,
                    "kind": "account",
                    "step_type": "accept_terms",
                    "status": "fulfilled",
                    "participant_subject_id": 9,
                    "term_uid": "term-pdf",
                    "document_name": "Platform terms",
                    "terms_file": True,
                    "completed_at": SIGNED,
                }
            ],
            acceptances=[
                {
                    "contact_id": 9,
                    "accepted_at": SIGNED,
                    "channel": "in_person",
                    "term_uid": "term-pdf",
                    "title": "Platform terms",
                    "body_kind": "pdf",
                    "body_html": None,
                }
            ],
        )
    )
    assert "Platform terms" in html
    assert "provided as a PDF" in html
    assert "In person" in html


def test_the_same_declaration_is_printed_once_for_two_applicants() -> None:
    pack = _pack(
        profiles=[
            {
                "case_id": 1,
                "contact_id": 9,
                "uid": "a",
                "contact_type": "individual",
                "name": "Jonathan Berrar",
            },
            {
                "case_id": 3,
                "contact_id": 10,
                "uid": "b",
                "contact_type": "individual",
                "name": "Ada Berrar",
            },
        ],
        steps=[
            {
                "case_id": 2,
                "kind": "account",
                "step_type": "accept_terms",
                "status": "fulfilled",
                "participant_subject_id": 9,
                "term_uid": "term-general",
                "document_name": "General declaration",
                "terms_html": "<p>Shared wording.</p>",
                "completed_at": SIGNED,
            },
            {
                "case_id": 2,
                "kind": "account",
                "step_type": "accept_terms",
                "status": "pending",
                "participant_subject_id": 10,
                "term_uid": "term-general",
                "document_name": "General declaration",
                "terms_html": "<p>Shared wording.</p>",
            },
        ],
    )
    html = render_application_html(pack)
    assert html.count("Shared wording.") == 1
    assert "Signed by" in html and "Jonathan Berrar" in html
    assert "Not yet signed" in html
    ada = next(signature for signature in pack.signatures if signature.name == "Ada Berrar")
    assert ada.accepted is False


def _principal(*slugs: str) -> Principal:
    # The route checks the book before it loads the pack. These tests stub that load and have
    # no session, so a caller who may read cases is also allowed to see the firm.
    permissions = list(slugs)
    if slugs and CONTACTS_READ_ALL.slug not in permissions:
        permissions.append(CONTACTS_READ_ALL.slug)
    return Principal(user_id="user-1", permissions=permissions)


def _ctx(principal: Principal) -> FirmContext:
    return FirmContext(session=cast(Any, None), tenant_id=1, principal=principal)


@pytest.mark.asyncio
async def test_the_route_downloads_the_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    pack = _pack()

    async def load(*_args: object, **_kwargs: object):
        return pack

    async def render(got: object) -> bytes:
        assert got is pack
        return b"%PDF-1.4 application"

    monkeypatch.setattr(applications, "load_application_pack", load)
    monkeypatch.setattr(applications, "render_pack_pdf", render)
    principal = _principal(CASES_READ.slug)
    response = await applications.application_pack_pdf("tLNYH2mSjdKK", principal, _ctx(principal))
    assert response.status_code == 200
    assert response.body == b"%PDF-1.4 application"
    assert response.media_type == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert "application_tLNYH2mSjdKK.pdf" in disposition
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_the_route_refuses_a_caller_who_cannot_read_cases() -> None:
    principal = _principal()
    with pytest.raises(HTTPException) as raised:
        await applications.application_pack_pdf("tLNYH2mSjdKK", principal, _ctx(principal))
    assert raised.value.status_code == 403


@pytest.mark.asyncio
async def test_a_missing_journey_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def load(*_args: object, **_kwargs: object):
        return None

    monkeypatch.setattr(applications, "load_application_pack", load)
    principal = _principal(CASES_READ.slug)
    with pytest.raises(HTTPException) as raised:
        await applications.application_pack_pdf("missing", principal, _ctx(principal))
    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_a_render_failure_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def load(*_args: object, **_kwargs: object):
        return _pack()

    async def render(_pack_obj: object) -> bytes:
        raise OSError("weasyprint missing")

    monkeypatch.setattr(applications, "load_application_pack", load)
    monkeypatch.setattr(applications, "render_pack_pdf", render)
    principal = _principal(CASES_READ.slug)
    with pytest.raises(HTTPException) as raised:
        await applications.application_pack_pdf("tLNYH2mSjdKK", principal, _ctx(principal))
    assert raised.value.status_code == 503
    assert "could not be rendered" in str(raised.value.detail)
