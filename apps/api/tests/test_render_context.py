"""The render-context CONTRACT.

The generate gate and the editor's live lint both decide "does this token have a source?" from
``render/sample.py::sample_context`` (a fabricated preview catalogue). The worker builds the REAL
context separately — so any root the sample advertises but the worker doesn't produce passes
generation and then renders blank (the "looks fine, renders blank" bug: adviser/firm/date and the
extended client fields).

These tests pin the worker's ``_client_context`` + ``_actor_firm_context`` output against
``sample_context`` so the two can't drift back apart, and check the real values are the shape the
preview advertises. Real SQL against a real Postgres (testcontainers).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import worker
from app.render import jinja_engine
from app.render.resolve import first_rows
from app.render.sample import sample_context


async def _scalar(sess: AsyncSession, sql: str, **params: Any) -> Any:
    return (await sess.execute(text(sql), params)).scalar_one_or_none()


def test_one_namespace_binds_a_single_dataset_value_without_a_loop() -> None:
    """`one` exposes the first row per dataset so a scalar binds as `{{ one.<code>.<col> }}` — the
    fix for a subject-scoped dataset whose value the author wrote as a flat token. Pure: no DB."""
    one = first_rows({"fees": [{"rate": "1.25", "kind": "ongoing"}], "empty": []})
    assert one == {"fees": {"rate": "1.25", "kind": "ongoing"}, "empty": {}}
    # Renders through the real engine with no loop; an empty dataset renders "" (not an error).
    assert jinja_engine.render("{{ one.fees.rate }}", {"one": one}) == "1.25"
    assert jinja_engine.render("{{ one.empty.missing }}", {"one": one}) == ""


def test_render_with_diagnostics_flags_unsourced_tokens_not_empty_values() -> None:
    """Render-time diagnostics catch tokens that resolve to Undefined (silent blanks), including
    attribute-level misses the static gate can't — but never a defined-but-"" value."""
    html, missing = jinja_engine.render_with_diagnostics(
        "{{ client.name }}|{{ mystery }}|{{ one.fees.typo }}",
        {"client": {"name": "Bo"}, "one": {"fees": {"rate": "1"}}},
    )
    assert html == "Bo||"  # the unsourced tokens rendered blank
    assert "mystery" in missing and "typo" in missing  # a leaf attribute miss is caught too
    # A defined-but-empty value ("") is a real empty field, not a missing binding — not flagged.
    _, empty_ok = jinja_engine.render_with_diagnostics(
        "{{ client.phone }}", {"client": {"phone": ""}}
    )
    assert empty_ok == []


@asynccontextmanager
async def _svc(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """One service_role transaction — mirrors how the worker itself reads."""
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE service_role"))
        yield session


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed_document(
    sess: AsyncSession, *, schema: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A firm (tenant), a NAMED adviser (auth.users), a client with the extended fields
    (dob/title/phone/address), a published template version, and a generated document linking
    ``generated_by`` -> adviser and ``intended_contact_id`` -> client.

    ``schema`` is set at INSERT rather than patched afterwards: a PUBLISHED version's content is
    immutable (``documents.reject_published_version_edit``), so a test that needs particular
    components has to seed them here."""
    s = uuid.uuid4().hex[:8]
    tenant = int(
        await _scalar(
            sess,
            "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
            u=f"rc_{s}",
            a=f"rc-{s}",
            n="Ridley & Co",
        )
    )
    adviser = await _scalar(
        sess,
        "INSERT INTO auth.users (uid, tenant_id, email, first_name, last_name) "
        "VALUES (:u, :t, :e, 'Ada', 'Adviser') RETURNING id",
        u=f"u_{s}",
        t=tenant,
        e=f"ada_{s}@t.test",
    )
    client = int(
        await _scalar(
            sess,
            "INSERT INTO crm.contacts "
            "  (tenant_id, uid, type, email, name, first_name, surname, title, mobile, phone, dob, "
            "   address) "
            "VALUES (:t, :u, 'individual', :e, 'Bo Client', 'Bo', 'Client', 'Mrs', "
            "  '+44 7700 900000', '01179000000', '1980-05-14', CAST(:addr AS jsonb)) RETURNING id",
            t=tenant,
            u=f"c_{s}",
            e=f"bo_{s}@t.test",
            addr=json.dumps({"line1": "1 Sample Street", "city": "London", "postcode": "SW1A 1AA"}),
        )
    )
    tpl = int(
        await _scalar(
            sess,
            "INSERT INTO documents.templates "
            "  (uid, tenant_id, code, name, document_type, created_by) "
            "VALUES (:u, :t, :c, 'Pack', 'suitability', :by) RETURNING id",
            u=f"tpl_{s}",
            t=tenant,
            c=f"tc_{s}",
            by=adviser,
        )
    )
    vid = int(
        await _scalar(
            sess,
            "INSERT INTO documents.template_versions "
            "  (uid, tenant_id, template_id, version_number, status, schema, created_by) "
            "VALUES (:u, :t, :tpl, 1, 'published', CAST(:sch AS jsonb), :by) RETURNING id",
            u=f"tv_{s}",
            t=tenant,
            tpl=tpl,
            sch=json.dumps(schema or {"html": "<p>x</p>", "css": "", "components": []}),
            by=adviser,
        )
    )
    doc = await _scalar(
        sess,
        "INSERT INTO documents.documents "
        "  (uid, tenant_id, document_type, source, name, status, template_version_id, "
        "   generated_by, intended_contact_id) "
        "VALUES (:u, :t, 'suitability', 'generated', 'Pack', 'generated', :v, :by, :c) "
        "RETURNING id",
        u=f"doc_{s}",
        t=tenant,
        v=vid,
        by=adviser,
        c=client,
    )
    return {"tenant": tenant, "adviser": adviser, "client": client, "document_id": doc}


@pytest.mark.asyncio
async def test_client_context_matches_the_sample_client_shape(maker) -> None:
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        client = (await worker._client_context(sess, fx["document_id"]))["client"]
    # Every field the preview's fake client advertises now has a real value.
    for key in (
        "name",
        "first_name",
        "surname",
        "title",
        "email",
        "phone",
        "telephone",
        "other_numbers",
        "address",
        "date_of_birth",
    ):
        assert key in client, f"client.{key} missing from the render context"
    assert client["date_of_birth"] == "14 May 1980"  # formatted like the preview, not ISO
    assert client["title"] == "Mrs"
    assert "1 Sample Street" in client["address"] and "SW1A 1AA" in client["address"]
    # No sample client sub-key may drift ahead of the worker either (roots are checked elsewhere).
    assert set(sample_context([])["client"]) <= set(client)


@pytest.mark.asyncio
async def test_missing_client_fields_render_empty_not_none(maker) -> None:
    """A client with no dob/title/phone/address must yield "" — Jinja renders `None` as the literal
    'None', which would otherwise ship inside a client document."""
    async with _svc(maker) as sess:
        s = uuid.uuid4().hex[:8]
        tenant = int(
            await _scalar(
                sess,
                "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :a, :n) RETURNING id",
                u=f"rx_{s}",
                a=f"rx-{s}",
                n="Firm",
            )
        )
        client = int(
            await _scalar(
                sess,
                "INSERT INTO crm.contacts (tenant_id, uid, type, email, name) "
                "VALUES (:t, :u, 'individual', :e, 'Nomi Nal') RETURNING id",
                t=tenant,
                u=f"c_{s}",
                e=f"n_{s}@t.test",
            )
        )
        doc = await _scalar(
            sess,
            "INSERT INTO documents.documents "
            "  (uid, tenant_id, document_type, source, name, status, intended_contact_id) "
            "VALUES (:u, :t, 'report', 'uploaded', 'D', 'generated', :c) RETURNING id",
            u=f"doc_{s}",
            t=tenant,
            c=client,
        )
        client_ctx = (await worker._client_context(sess, doc))["client"]
    for key in ("title", "phone", "address", "date_of_birth"):
        assert client_ctx[key] == "", f"client.{key} should be '' not None/absent"


@pytest.mark.asyncio
async def test_adviser_and_firm_are_produced(maker) -> None:
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        ctx = await worker._actor_firm_context(sess, fx["adviser"], fx["tenant"])
    assert ctx["adviser_name"] == "Ada Adviser"
    assert ctx["adviser"]["name"] == "Ada Adviser"
    assert ctx["firm_name"] == "Ridley & Co"
    assert ctx["firm"]["name"] == "Ridley & Co"


@pytest.mark.asyncio
async def test_worker_context_covers_every_advertised_root(maker) -> None:
    """The anti-drift lock: every ROOT the gate + editor lint advertise (from ``sample_context``) is
    produced by the worker — so a token can no longer pass generation and then render blank."""
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        produced = set(await worker._client_context(sess, fx["document_id"]))
        produced |= set(await worker._actor_firm_context(sess, fx["adviser"], fx["tenant"]))
        produced |= set(await worker._client_accounts_context(sess, fx["client"]))
    # `_render_pdf` adds these unconditionally on top of the context.
    produced |= {"answers", "datasets", "today", "date", "document_name"}
    advertised = set(sample_context([]))  # no form fields → just the common roots
    missing = advertised - produced
    assert not missing, f"worker context is missing advertised roots: {sorted(missing)}"


# -- the client's own book ------------------------------------------------------------------------
# `accounts` and `portfolio` are the only roots resolved from the BOOK rather than from the document
# and its actor, so they get their own seeding. The parity lock above covers their PRESENCE; these
# cover what a client would actually read off the page.


async def _seed_account(
    sess: AsyncSession,
    tenant: int,
    client: int,
    *,
    name: str,
    status: str = "active",
    value: float | None = 1000.0,
    currency: str | None = "GBP",
    identifier: str | None = None,
    valued_at: str | None = "2026-07-31T00:00:00Z",
) -> int:
    return int(
        await _scalar(
            sess,
            "INSERT INTO platform.accounts "
            "  (tenant_id, uid, contact_id, name, status, value, currency, platform_identifier, "
            "   opened_at, data) "
            "VALUES (:t, :u, :c, :n, CAST(:st AS platform.account_status), :v, :cur, :pid, "
            "  '2019-04-12T00:00:00Z', CAST(:d AS jsonb)) RETURNING id",
            t=tenant,
            u=uuid.uuid4().hex[:12],
            c=client,
            n=name,
            st=status,
            v=value,
            cur=currency,
            pid=identifier,
            d=json.dumps({"valued_at": valued_at} if valued_at else {}),
        )
    )


@pytest.mark.asyncio
async def test_accounts_are_the_clients_own_largest_first(maker) -> None:
    """The loop source: a row per account the client holds, biggest first -- the order a valuation
    table reads in, rather than the order the accounts happened to be created."""
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        await _seed_account(sess, fx["tenant"], fx["client"], name="ISA", value=1000.0)
        await _seed_account(sess, fx["tenant"], fx["client"], name="SIPP", value=90000.0)
        ctx = await worker._client_accounts_context(sess, fx["client"])
    assert [a["name"] for a in ctx["accounts"]] == ["SIPP", "ISA"]
    assert ctx["portfolio"]["account_count"] == 2
    assert ctx["portfolio"]["total_value"] == 91000.0
    assert ctx["portfolio"]["currency"] == "GBP"


@pytest.mark.asyncio
async def test_a_draft_account_never_reaches_a_client_document(maker) -> None:
    """The one exclusion that matters: a draft is an account being set up, not one the client holds,
    and this data goes into client-facing PDFs. A closed one IS included -- an annual review
    legitimately reports on an account that closed during the year."""
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        await _seed_account(sess, fx["tenant"], fx["client"], name="Being opened", status="draft")
        await _seed_account(sess, fx["tenant"], fx["client"], name="Closed one", status="closed")
        ctx = await worker._client_accounts_context(sess, fx["client"])
    assert [a["name"] for a in ctx["accounts"]] == ["Closed one"]
    assert ctx["portfolio"]["account_count"] == 1


@pytest.mark.asyncio
async def test_another_clients_accounts_are_not_included(maker) -> None:
    """The scoping that would be a disclosure if wrong: one client's document must never carry
    another client's accounts."""
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        other = int(
            await _scalar(
                sess,
                "INSERT INTO crm.contacts (tenant_id, uid, type, name) "
                "VALUES (:t, :u, 'individual', 'Someone Else') RETURNING id",
                t=fx["tenant"],
                u=uuid.uuid4().hex[:12],
            )
        )
        await _seed_account(sess, fx["tenant"], fx["client"], name="Ours")
        await _seed_account(sess, fx["tenant"], other, name="Theirs")
        ctx = await worker._client_accounts_context(sess, fx["client"])
    assert [a["name"] for a in ctx["accounts"]] == ["Ours"]


@pytest.mark.asyncio
async def test_values_are_raw_and_dates_are_iso_for_the_filters(maker) -> None:
    """Money is a number and dates are ISO, because `| currency` / `| date` are what format them --
    a pre-formatted value makes its own filter a no-op and `sum_currency` impossible."""
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        await _seed_account(
            sess, fx["tenant"], fx["client"], name="ISA", value=1234.5, identifier="ISA-1"
        )
        ctx = await worker._client_accounts_context(sess, fx["client"])
    (account,) = ctx["accounts"]
    assert account["value"] == 1234.5
    assert account["number"] == "ISA-1"
    assert account["opened"] == "2019-04-12"
    assert account["value_as_of"] == "2026-07-31"
    # And the filters actually consume them.
    rendered, _ = jinja_engine.render_with_diagnostics(
        "{{ accounts[0].value | currency }} on {{ accounts[0].opened | date }}", ctx
    )
    assert "1,234.50" in rendered
    assert "12 April 2019" in rendered


@pytest.mark.asyncio
async def test_an_unvalued_account_keeps_no_value_rather_than_zero(maker) -> None:
    """A missing valuation stays missing. 0.00 in a client's valuation table asserts the account is
    empty, which is not the same as not knowing what it holds."""
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        await _seed_account(
            sess, fx["tenant"], fx["client"], name="Unvalued", value=None, valued_at=None
        )
        ctx = await worker._client_accounts_context(sess, fx["client"])
    (account,) = ctx["accounts"]
    assert account["value"] is None
    # The roll-up totals what it could read, and still counts the account.
    assert ctx["portfolio"]["total_value"] == 0.0
    assert ctx["portfolio"]["account_count"] == 1


@pytest.mark.asyncio
async def test_a_mixed_currency_book_withholds_the_symbol_rather_than_guessing(maker) -> None:
    """Two currencies have no single total. The number is still given -- an author gets a figure
    with no symbol -- but stamping one row's code onto a sum across both would misstate it."""
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        await _seed_account(sess, fx["tenant"], fx["client"], name="Pounds", currency="GBP")
        await _seed_account(sess, fx["tenant"], fx["client"], name="Dollars", currency="USD")
        ctx = await worker._client_accounts_context(sess, fx["client"])
    assert ctx["portfolio"]["currency"] == ""
    assert ctx["portfolio"]["total_value"] == 2000.0


@pytest.mark.asyncio
async def test_as_at_is_the_stalest_valuation(maker) -> None:
    """A roll-up is only as current as its oldest input; the freshest date would overstate it."""
    async with _svc(maker) as sess:
        fx = await _seed_document(sess)
        await _seed_account(
            sess, fx["tenant"], fx["client"], name="Fresh", valued_at="2026-07-31T00:00:00Z"
        )
        await _seed_account(
            sess, fx["tenant"], fx["client"], name="Stale", valued_at="2026-06-30T00:00:00Z"
        )
        ctx = await worker._client_accounts_context(sess, fx["client"])
    assert ctx["portfolio"]["as_at"] == "2026-06-30"


@pytest.mark.asyncio
async def test_a_document_with_no_client_gets_an_empty_book(maker) -> None:
    """A staff-only document has no subject, so it has no accounts -- and `portfolio` is still
    present, because a template that prints a total must render 0 rather than a blank."""
    async with _svc(maker) as sess:
        ctx = await worker._client_accounts_context(sess, None)
    assert ctx["accounts"] == []
    assert ctx["portfolio"]["account_count"] == 0
    assert ctx["portfolio"]["total_value"] == 0.0
    assert ctx["portfolio"]["as_at"] == ""


@pytest.mark.asyncio
async def test_a_chart_bound_to_accounts_is_supplied_not_looked_up(maker) -> None:
    """A built-in collection is offered in the same source pickers as a dataset, so authors bind
    charts and tables to it — and a component binding is a CONTRACT: an unresolvable one fails the
    whole render. The catalogue holds no row for `accounts`, so resolving it there answers "no
    dataset with code 'accounts' in this firm" and the document dies. It has to arrive as ROWS,
    both so the render survives and so the chart shows real figures rather than an empty frame."""
    # A chart component bound to the built-in, exactly as the editor's picker writes it.
    async with _svc(maker) as sess:
        fx = await _seed_document(
            sess,
            schema={
                "html": "<p>{{ client.name }}</p>",
                "css": "",
                "components": [
                    # The real serialised shape: a component names its source through
                    # `binding._resolved_data`, which is what `dataset_codes` reads.
                    {"type": "chart", "binding": {"_resolved_data": "{{ accounts }}"}}
                ],
            },
        )
        await _seed_account(sess, fx["tenant"], fx["client"], name="ISA", value=2500.0)
        job = {"document_id": fx["document_id"], "tenant_id": fx["tenant"]}
        loaded = await worker._load_inputs(sess, job)

    # Supplied under its own code, as rows — which is what `substitute_components` reads.
    assert [r["name"] for r in loaded["datasets"]["accounts"]] == ["ISA"]
    assert loaded["datasets"]["accounts"][0]["value"] == 2500.0
    # And nothing was reported as an unresolved dataset.
    assert loaded["warnings"] == []


@pytest.mark.asyncio
async def test_a_real_dataset_binding_still_has_to_resolve(maker) -> None:
    """The guard above must not have turned every unresolvable binding into a shrug: a component
    bound to a dataset that does not exist still fails the render, because the author said the
    document draws on it and an empty table would ship silently."""
    async with _svc(maker) as sess:
        fx = await _seed_document(
            sess,
            schema={
                "html": "<p>x</p>",
                "css": "",
                "components": [
                    {"type": "chart", "binding": {"_resolved_data": "{{ no_such_dataset }}"}}
                ],
            },
        )
        job = {"document_id": fx["document_id"], "tenant_id": fx["tenant"]}
        with pytest.raises(worker.DatasetUnresolved):
            await worker._load_inputs(sess, job)
