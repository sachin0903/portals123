"""Terms catalogue: applicability, outstanding, publish snapshot, skip-if-accepted."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text

from app import terms as svc
from app.auth import Principal
from app.flows.projection import gates_from_steps
from app.ids import new_id
from app.routers import terms as rt
from tests import email_support

WRITE = ["terms:write"]


def test_applies_always_and_axes() -> None:
    assert svc.applies({"always": True}, product="GIA", has_transfer=False)
    assert svc.applies({}, product=None)
    assert svc.applies({"products": ["ISA"]}, product="ISA")
    assert not svc.applies({"products": ["ISA"]}, product="GIA")
    assert svc.applies({"has_transfer": True}, has_transfer=True)
    assert not svc.applies({"has_transfer": True}, has_transfer=False)
    assert svc.applies(
        {"products": ["ISA"], "has_transfer": True}, product="GIA", has_transfer=True
    )


def test_audience_matches() -> None:
    assert svc.audience_matches("all", "contact")
    assert svc.audience_matches("client", "contact")
    assert not svc.audience_matches("staff", "contact")
    assert svc.audience_matches("staff", "user")
    assert not svc.audience_matches("client", "user")


def test_gates_need_outstanding_empty() -> None:
    steps = [("accept_terms", "fulfilled", "participant")]
    held = gates_from_steps(
        steps, declared=True, has_offshore=False, offshore_approved=False, outstanding_empty=False
    )
    assert not held.terms_ok
    ok = gates_from_steps(
        steps, declared=True, has_offshore=False, offshore_approved=False, outstanding_empty=True
    )
    assert ok.terms_ok


@pytest.fixture
def staff(super_engine: Engine) -> dict[str, str]:
    return email_support.make_firm(super_engine, prefix="tm")


@pytest_asyncio.fixture
async def session_factory(db: dict[str, str], staff: dict[str, str]):
    opener, engine = email_support.session_opener(db, email_support.firm_claims(staff, perms=WRITE))
    yield opener
    await engine.dispose()


def _principal(staff: dict[str, str]) -> Principal:
    return email_support.principal(staff, perms=WRITE)


@pytest.mark.asyncio
async def test_publish_is_immutable_and_v2_reprompts(
    session_factory, staff: dict[str, str], super_engine: Engine
) -> None:
    principal = _principal(staff)
    async with session_factory() as session:
        created = await rt.create_term(
            body=rt.CreateTerm(title="General declaration", body_md="First wording."),
            principal=principal,
            session=session,
        )
        uid = created.uid
        published = await rt.publish_term(uid=uid, principal=principal, session=session)
    assert published.status == "published"
    assert published.body_html is not None
    first_version = published.current_version_uid
    assert first_version

    async with session_factory() as session:
        await rt.update_term(
            uid=uid,
            body=rt.UpdateTerm(body_md="Second wording."),
            principal=principal,
            session=session,
        )
        again = await rt.publish_term(uid=uid, principal=principal, session=session)
    assert again.current_version_uid != first_version
    assert again.current_version_number == 2
    assert again.current_version_published_at is not None

    async with session_factory() as session:
        versions = await rt.list_term_versions(uid=uid, principal=principal, session=session)
    assert again.current_version_published_at == versions[0].published_at

    with super_engine.connect() as conn:
        contact_id = int(
            conn.execute(
                text(
                    "INSERT INTO crm.contacts (uid, tenant_id, type, name) "
                    "VALUES (:u, :t, 'individual', 'Pat Client') RETURNING id"
                ),
                {"u": new_id(), "t": int(staff["tenant_id"])},
            ).scalar_one()
        )
        conn.commit()
    async with session_factory() as session:
        await svc.accept_term(
            session,
            uid,
            subject="contact",
            tenant_id=int(staff["tenant_id"]),
            contact_id=contact_id,
            user_id=None,
            accepted_by=None,
            version_id=None,
        )
        leftover = await svc.outstanding_terms(
            session,
            svc.OutstandingContext(
                required_at="onboarding",
                subject="contact",
                contact_id=contact_id,
            ),
        )
    assert uid not in {row["uid"] for row in leftover}

    async with session_factory() as session:
        await rt.update_term(
            uid=uid,
            body=rt.UpdateTerm(body_md="Third wording."),
            principal=principal,
            session=session,
        )
        await rt.publish_term(uid=uid, principal=principal, session=session)
        leftover = await svc.outstanding_terms(
            session,
            svc.OutstandingContext(
                required_at="onboarding",
                subject="contact",
                contact_id=contact_id,
            ),
        )
    assert uid in {row["uid"] for row in leftover}


@pytest.mark.asyncio
async def test_an_in_person_acceptance_points_at_its_signed_copy(
    session_factory, staff: dict[str, str], super_engine: Engine
) -> None:
    """The register row carries the uploaded signed copy, and the client record's acceptance
    list can open it. A portal acceptance carries none — it is its own evidence."""
    principal = _principal(staff)
    tenant_id = int(staff["tenant_id"])
    async with session_factory() as session:
        created = await rt.create_term(
            body=rt.CreateTerm(title="Platform terms", body_md="Wording."),
            principal=principal,
            session=session,
        )
        await rt.publish_term(uid=created.uid, principal=principal, session=session)
    signed_uid = new_id()
    with super_engine.connect() as conn:
        contact_id = int(
            conn.execute(
                text(
                    "INSERT INTO crm.contacts (uid, tenant_id, type, name) "
                    "VALUES (:u, :t, 'individual', 'Pat Client') RETURNING id"
                ),
                {"u": new_id(), "t": tenant_id},
            ).scalar_one()
        )
        signed_id = conn.execute(
            text(
                "INSERT INTO documents.documents "
                "  (uid, tenant_id, document_type, source, name, status) "
                "VALUES (:u, :t, 'signed_terms', 'uploaded', 'signed.pdf', 'generated') "
                "RETURNING id"
            ),
            {"u": signed_uid, "t": tenant_id},
        ).scalar_one()
        conn.commit()
    async with session_factory() as session:
        accepted = await svc.accept_term(
            session,
            created.uid,
            subject="contact",
            tenant_id=tenant_id,
            contact_id=contact_id,
            user_id=None,
            accepted_by=None,
            channel="in_person",
            evidence_document_id=str(signed_id),
        )
        assert accepted["evidence_document_uid"] == signed_uid
        listed = await svc.list_contact_acceptances(session, contact_id)
    assert [row["evidence_document_uid"] for row in listed] == [signed_uid]
    assert listed[0]["channel"] == "in_person"
    # The API shape carries it too, so the client record's Compliance card can link the file.
    fields = rt.AcceptanceOut.model_fields
    out = rt.AcceptanceOut(**{k: listed[0][k] for k in fields if k in listed[0]})
    assert out.evidence_document_uid == signed_uid


@pytest.mark.asyncio
async def test_switching_a_term_to_pdf_saves_alongside_posted_copy(
    session_factory, staff: dict[str, str]
) -> None:
    """The editor posts `body_md` on every save, including when the kind is PDF.

    That combination emitted `SET body_md = NULL, body_md = :md` — `multiple assignments to same
    column` — so saving or publishing any PDF term was a 500 the editor could only show as a
    failure. The kind wins: the markdown is cleared, not written.
    """
    principal = _principal(staff)
    async with session_factory() as session:
        created = await rt.create_term(
            body=rt.CreateTerm(title="Platform terms", body_md="Draft wording."),
            principal=principal,
            session=session,
        )
        updated = await rt.update_term(
            uid=created.uid,
            body=rt.UpdateTerm(title="Platform terms", body_kind="pdf", body_md=""),
            principal=principal,
            session=session,
        )
    assert updated.body_kind == "pdf"
    assert updated.body_md is None


@pytest.mark.asyncio
async def test_staff_can_preview_draft_pdf_without_exposing_it_as_published(
    session_factory, staff: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    principal = _principal(staff)
    async with session_factory() as session:
        created = await rt.create_term(
            body=rt.CreateTerm(title="Draft PDF"),
            principal=principal,
            session=session,
        )
        await svc.set_pdf_file(
            session,
            created.uid,
            file_key=f"terms/{created.uid}/body.pdf",
            content_type="application/pdf",
            size=17,
        )

        async def fake_store(*_args):
            return object()

        async def fake_object(*_args):
            return b"%PDF-draft bytes", "application/pdf"

        monkeypatch.setattr(rt.tenant_storage, "get_tenant_store", fake_store)
        monkeypatch.setattr(rt.storage, "get_tenant_object", fake_object)

        preview = await rt.get_term_file(
            uid=created.uid,
            draft=True,
            principal=principal,
            session=session,
        )
        assert preview.body == b"%PDF-draft bytes"
        assert preview.media_type == "application/pdf"

        with pytest.raises(HTTPException) as exc:
            await rt.get_term_file(
                uid=created.uid,
                draft=False,
                principal=principal,
                session=session,
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_published_pdf_term_files_a_document_the_client_can_open(
    session_factory, staff: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The onboarding step's PDF half against a real schema: the filed ``terms`` document resolves
    — through the same ``COALESCE(original_file_id, pdf_file_id)`` join the client's target lookup
    uses — to the version's own object in the tenant bucket. A second filing (another client asked
    to accept the same version) reuses the file row and does not touch the object store."""
    from types import SimpleNamespace

    from app.flows import step_handlers

    principal = _principal(staff)
    tenant_id = int(staff["tenant_id"])
    async with session_factory() as session:
        created = await rt.create_term(
            body=rt.CreateTerm(title="Platform terms"), principal=principal, session=session
        )
        key = f"terms/{created.uid}/body.pdf"
        await svc.set_pdf_file(
            session, created.uid, file_key=key, content_type="application/pdf", size=17
        )
        await rt.publish_term(uid=created.uid, principal=principal, session=session)
        term = await svc.get_term(session, created.uid)
        assert term is not None and term["published_file_key"] == key

        fetches: list[str] = []

        async def fake_store(*_args):
            return SimpleNamespace(bucket="t-test")

        async def fake_object(_store, k):
            fetches.append(k)
            return b"%PDF-1.7 published", "application/pdf"

        monkeypatch.setattr(step_handlers.tenant_storage, "get_tenant_store", fake_store)
        monkeypatch.setattr(step_handlers.storage, "get_tenant_object", fake_object)

        first = await step_handlers._store_terms_pdf(
            session, tenant_id, name="Platform terms", term=term
        )
        second = await step_handlers._store_terms_pdf(
            session, tenant_id, name="Platform terms", term=term
        )
        assert first and second and first != second
        assert fetches == [key], "the object is read once; the second filing reuses the file row"

        rows = (
            (
                await session.execute(
                    text(
                        "SELECT d.uid, d.document_type, d.metadata, f.id AS file_id, f.bucket, "
                        "       f.storage_key, f.content_type, f.size_bytes "
                        "FROM documents.documents d "
                        "JOIN documents.files f "
                        "  ON f.id = COALESCE(d.original_file_id, d.pdf_file_id) "
                        "WHERE d.uid = ANY(:u)"
                    ),
                    {"u": [first, second]},
                )
            )
            .mappings()
            .all()
        )
    assert {r["uid"] for r in rows} == {first, second}
    assert {r["file_id"] for r in rows} and len({r["file_id"] for r in rows}) == 1
    for r in rows:
        assert r["document_type"] == "terms"
        assert (r["bucket"], r["storage_key"]) == ("t-test", key)
        assert r["content_type"] == "application/pdf"
        assert r["size_bytes"] == len(b"%PDF-1.7 published")
        assert r["metadata"]["term_uid"] == created.uid
        assert r["metadata"]["term_version_uid"] == term["current_version_uid"]


@pytest.mark.asyncio
async def test_isa_applicability_hides_from_gia(
    session_factory, staff: dict[str, str], super_engine: Engine
) -> None:
    principal = _principal(staff)
    async with session_factory() as session:
        created = await rt.create_term(
            body=rt.CreateTerm(
                title="ISA declaration",
                body_md="ISA only.",
                applicability={"always": False, "products": ["ISA"], "has_transfer": False},
            ),
            principal=principal,
            session=session,
        )
        await rt.publish_term(uid=created.uid, principal=principal, session=session)
    with super_engine.connect() as conn:
        contact_id = int(
            conn.execute(
                text(
                    "INSERT INTO crm.contacts (uid, tenant_id, type, name) "
                    "VALUES (:u, :t, 'individual', 'Isa Client') RETURNING id"
                ),
                {"u": new_id(), "t": int(staff["tenant_id"])},
            ).scalar_one()
        )
        conn.commit()
    async with session_factory() as session:
        gia = await svc.outstanding_terms(
            session,
            svc.OutstandingContext(
                required_at="onboarding",
                subject="contact",
                contact_id=contact_id,
                product="GIA",
            ),
        )
        isa = await svc.outstanding_terms(
            session,
            svc.OutstandingContext(
                required_at="onboarding",
                subject="contact",
                contact_id=contact_id,
                product="ISA",
            ),
        )
    assert created.uid not in {row["uid"] for row in gia}
    assert created.uid in {row["uid"] for row in isa}


@pytest.mark.asyncio
async def test_the_onboarding_gate_names_the_terms_still_to_accept(
    session_factory, staff: dict[str, str], super_engine: Engine
) -> None:
    """The account-opening gate answers WHICH of the firm's terms are unaccepted, once each.

    A firm that authors a catalogue term after the journey was seeded has no step that collects
    it, so the adviser reached account setup with a refusal and nothing to say what was missing.
    The refusal now names the term; the list is one row per term however many owners and product
    axes it was asked about."""
    principal = _principal(staff)
    tenant_id = int(staff["tenant_id"])
    with super_engine.connect() as conn:
        contacts = [
            int(
                conn.execute(
                    text(
                        "INSERT INTO crm.contacts (uid, tenant_id, type, name) "
                        "VALUES (:u, :t, 'individual', :n) RETURNING id"
                    ),
                    {"u": new_id(), "t": tenant_id, "n": name},
                ).scalar_one()
            )
            for name in ("Owner One", "Owner Two")
        ]
        conn.commit()
    async with session_factory() as session:
        created = await rt.create_term(
            body=rt.CreateTerm(title="Fee schedule", body_md="<p>Fees.</p>"),
            principal=principal,
            session=session,
        )
        await rt.publish_term(uid=created.uid, principal=principal, session=session)
        unaccepted = await svc.onboarding_outstanding(
            session,
            tenant_id=tenant_id,
            contact_ids=contacts,
            products=["GIA", "ISA"],
            has_transfer=False,
        )
        assert [row["title"] for row in unaccepted].count("Fee schedule") == 1
        assert not await svc.onboarding_outstanding_empty(
            session,
            tenant_id=tenant_id,
            contact_ids=contacts,
            products=["GIA", "ISA"],
            has_transfer=False,
        )
        for contact_id in contacts:
            await svc.accept_term(
                session,
                created.uid,
                subject="contact",
                tenant_id=tenant_id,
                contact_id=contact_id,
                user_id=None,
                accepted_by=None,
                channel="portal",
            )
        assert (
            await svc.onboarding_outstanding(
                session,
                tenant_id=tenant_id,
                contact_ids=contacts,
                products=["GIA", "ISA"],
                has_transfer=False,
            )
            == []
        )


@pytest.mark.asyncio
async def test_the_onboarding_gate_ignores_graphene_platform_terms(
    session_factory, staff: dict[str, str], super_engine: Engine
) -> None:
    """The account-opening gate asks only about the firm's OWN terms.

    ``0184_terms`` seeds Graphene's privacy policy as ``required_at`` onboarding AND login, applying
    always — this builds an equivalent rather than leaning on that stub, so the claim holds whatever
    the platform catalogue happens to contain. Nothing on a firm's journey collects a Graphene term;
    the login gate at ``/terms/required`` does. Counting them here refused every account opening for
    ever, including the adviser-led journey where the client never signs in at all.
    """
    code = f"platform_stub_{new_id()}"
    with super_engine.connect() as conn:
        contact_id = int(
            conn.execute(
                text(
                    "INSERT INTO crm.contacts (uid, tenant_id, type, name) "
                    "VALUES (:u, :t, 'individual', 'Gate Client') RETURNING id"
                ),
                {"u": new_id(), "t": int(staff["tenant_id"])},
            ).scalar_one()
        )
        term_id = int(
            conn.execute(
                text(
                    "INSERT INTO terms.terms "
                    "(uid, owner, tenant_id, code, title, audience, required_at, applicability, "
                    " status, body_kind, body_md, published_at) "
                    "VALUES (:u, 'graphene', NULL, :code, 'Platform stub', 'all', "
                    " ARRAY['onboarding', 'login']::text[], '{\"always\": true}'::jsonb, "
                    " 'published', 'html', '<p>Platform.</p>', now()) RETURNING id"
                ),
                {"u": new_id(), "code": code},
            ).scalar_one()
        )
        version_id = int(
            conn.execute(
                text(
                    "INSERT INTO terms.term_versions "
                    "(uid, tenant_id, term_id, version_number, title, body_kind, body_html, "
                    " content_hash) "
                    "VALUES (:u, NULL, :t, 1, 'Platform stub', 'html', '<p>Platform.</p>', :h) "
                    "RETURNING id"
                ),
                {"u": new_id(), "t": term_id, "h": new_id()},
            ).scalar_one()
        )
        conn.execute(
            text("UPDATE terms.terms SET current_published_version_id = :v WHERE id = :t"),
            {"v": version_id, "t": term_id},
        )
        conn.commit()
    tenant_id = int(staff["tenant_id"])

    def ctx(**extra: object) -> svc.OutstandingContext:
        return svc.OutstandingContext(
            required_at="onboarding",
            subject="contact",
            contact_id=contact_id,
            tenant_id=tenant_id,
            **extra,  # type: ignore[arg-type]
        )

    async with session_factory() as session:
        # Unscoped is what the login gate reads, and it must still see Graphene's terms.
        unscoped = await svc.outstanding_terms(session, ctx())
        scoped = await svc.outstanding_terms(session, ctx(owner="tenant"))
    assert code in {row["code"] for row in unscoped}
    assert "graphene" not in {row["owner"] for row in scoped}
    # Nothing else moves: the two answers differ only by who owns the terms.
    assert {row["uid"] for row in unscoped if row["owner"] == "tenant"} == {
        row["uid"] for row in scoped
    }


@pytest.mark.asyncio
async def test_an_acceptance_opens_the_version_the_client_accepted(
    session_factory, staff: dict[str, str], super_engine: Engine, db: dict[str, str]
) -> None:
    """A newer version is published after the client accepted: the acceptance still opens the
    wording they read. Another client's acceptance, or a text version's file, is not found."""
    principal = _principal(staff)
    async with session_factory() as session:
        created = await rt.create_term(
            body=rt.CreateTerm(title="General declaration", body_md="First wording."),
            principal=principal,
            session=session,
        )
        await rt.publish_term(uid=created.uid, principal=principal, session=session)
    contact_uid = new_id()
    with super_engine.connect() as conn:
        contact_id = int(
            conn.execute(
                text(
                    "INSERT INTO crm.contacts (uid, tenant_id, type, name) "
                    "VALUES (:u, :t, 'individual', 'Pat Client') RETURNING id"
                ),
                {"u": contact_uid, "t": int(staff["tenant_id"])},
            ).scalar_one()
        )
        conn.commit()
    async with session_factory() as session:
        await svc.accept_term(
            session,
            created.uid,
            subject="contact",
            tenant_id=int(staff["tenant_id"]),
            contact_id=contact_id,
            user_id=None,
            accepted_by=None,
            version_id=None,
        )
    async with session_factory() as session:
        await rt.update_term(
            uid=created.uid,
            body=rt.UpdateTerm(body_md="Second wording."),
            principal=principal,
            session=session,
        )
        await rt.publish_term(uid=created.uid, principal=principal, session=session)

    perms = ["contacts:read", "contacts:read_all"]
    reader = email_support.principal(staff, perms=perms)
    opener, engine = email_support.session_opener(db, email_support.firm_claims(staff, perms=perms))
    async with opener() as session:
        (accepted,) = await rt.list_contact_term_acceptances(
            uid=contact_uid, principal=reader, session=session
        )
        opened = await rt.get_contact_term_acceptance(
            uid=contact_uid, acceptance_uid=accepted.uid, principal=reader, session=session
        )
        assert opened.version_number == 1
        assert opened.body_html is not None and "First wording." in opened.body_html
        assert opened.has_file is False
        for missing in (
            rt.get_contact_term_acceptance(
                uid=contact_uid, acceptance_uid="not-theirs", principal=reader, session=session
            ),
            rt.get_contact_term_acceptance_file(
                uid=contact_uid, acceptance_uid=accepted.uid, principal=reader, session=session
            ),
        ):
            with pytest.raises(HTTPException) as raised:
                await missing
            assert raised.value.status_code == 404
        with pytest.raises(HTTPException) as raised:
            await rt.get_contact_term_acceptance(
                uid=contact_uid,
                acceptance_uid=accepted.uid,
                principal=email_support.principal(staff, perms=[]),
                session=session,
            )
        assert raised.value.status_code == 403
    await engine.dispose()
