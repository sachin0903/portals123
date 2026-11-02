"""Security proof for the dataset execution envelope (``app/render/resolve.py``).

A dataset query is Graphene's own SQL — a catalogue entry (``app/render/catalogue.py``) whose
parameters are VALUES bound by the driver, so a firm chooses what Postgres compares against and
never a table, a column, an ordering or a predicate. There is therefore no author SQL to validate,
and what stands between a dataset and the production database is the envelope every one of those
statements runs inside. These tests pin that envelope against a real Postgres:

* the query runs with the CALLER'S OWN claims — a dataset is not a way round one's own book scoping
  — and when that means a firm-wide dataset shows less than the document will, the caller is told;
* a background render resolves each dataset as its entry's ``scope`` DECLARES: firm-wide for
  ``global``, that one client for ``subject``, and never another firm either way. A ``global``
  dataset that cannot be resolved firm-wide is refused rather than resolved partially;
* the two halves of that contract cannot be written down incoherently — ``catalogue.Entry`` refuses
  a per-client entry that scopes by nothing and a firm-wide entry that scopes by a client, so the
  scope beside the SQL and the SQL itself cannot disagree;
* a static (``inline``) source cannot be parameterised by a client, so a ``scope='subject'`` one is
  refused rather than resolving the whole blob into one client's document;
* only a single statement runs: the row cap wraps an entry's SQL in a subquery, so a second
  statement is a syntax error before the driver ever sees it, and the transaction is read-only;
* the statement is timed out and row-capped, and a clipped result is reported;
* and because none of the above says anything about what the SQL itself asks Postgres to do, the
  executor re-reads its role and claims AFTER the statement and discards the rows if either moved
  — so a claims rewrite reached through a function, a view or an operator nobody thought of returns
  nothing.

The adversarial cases reach the executor by REGISTERING A TEST-ONLY CATALOGUE ENTRY (the ``firms``
fixture's ``dataset`` helper), which is the same door the shipped entries come through. Several
include a *control* that runs the same string with no envelope at all, so the exploit is
demonstrated, not assumed.
"""

# ruff: noqa: S608 -- the attack strings here are deliberately assembled SQL; that IS the test.
import json
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import Principal
from app.render import catalogue as cat
from app.render.resolve import (
    DATASET_ROW_LIMIT,
    DATASET_TIMEOUT_MS,
    resolve_datasets,
    resolve_datasets_reporting,
)
from app.routers import datasets as datasets_router


# ── an entry cannot contradict its own scope ──────────────────────────────────
def test_an_entry_cannot_contradict_its_own_scope() -> None:
    """``scope`` and the SQL's own client filter are declared together, so an incoherent pair is
    refused where it is written rather than reconciled at render.

    Both directions are load-bearing. A ``subject`` entry that scopes by nothing puts the whole
    firm's rows in one client's document — and cannot be saved by RLS, which disagrees with itself
    across these tables (``platform.valuations`` has no contact branch at all and returns nothing,
    ``platform.contributions``' tenant branch is not gated on ``auth.can_read_all()`` and returns
    the firm). A ``global`` entry that binds the client is the mirror: it is resolved under
    firm-wide claims and comes back client-shaped, so "firm-wide" quietly means "whichever client
    this document is for"."""
    with pytest.raises(ValueError, match="must bind :subject_id"):
        cat.Entry(
            key="book_test_per_client_scoped_by_nothing",
            name="Per client, scoped by nothing",
            description="Client-scoped in name only.",
            scope="subject",
            sql="SELECT id, email FROM crm.contacts",
            columns=(cat.Column("id", "Id"), cat.Column("email", "Email")),
        )
    with pytest.raises(ValueError, match="must not bind :subject_id"):
        cat.Entry(
            key="book_test_firm_wide_scoped_by_a_client",
            name="Firm-wide, scoped by a client",
            description="Firm-wide in name only.",
            scope="global",
            sql="SELECT id, email FROM crm.contacts WHERE id = :subject_id",
            columns=(cat.Column("id", "Id"), cat.Column("email", "Email")),
        )


# ── DB-backed fixtures ────────────────────────────────────────────────────────
@pytest.fixture
def firms(super_engine: Engine) -> Iterator[dict[str, Any]]:
    """Two firms; firm A has a staff user, two clients (one in the adviser's book) and a couple of
    datasets. Seeded past RLS with a superuser."""
    s = uuid.uuid4().hex[:8]
    registered: list[str] = []
    try:
        with super_engine.connect() as c:

            def tenant(tag: str) -> int:
                return int(
                    c.execute(
                        text(
                            "INSERT INTO auth.tenants (uid, alias, name) "
                            "VALUES (:uid, :alias, :name) RETURNING id"
                        ),
                        {
                            "uid": f"sqten_{tag}_{s}",
                            "alias": f"sqfirm-{tag}-{s}",
                            "name": f"SQFirm {tag} {s}",
                        },
                    ).scalar_one()
                )

            tenant_a, tenant_b = tenant("a"), tenant("b")
            user = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, email, workos_user_id, first_name, "
                        "  last_name, enabled, tenant_id) "
                        "VALUES (:uid, :email, :wos, 'Ada', 'Author', true, :t) RETURNING id"
                    ),
                    {
                        "uid": f"squsr_{s}",
                        "email": f"author_{s}@a.test",
                        "wos": f"sqwos_{s}",
                        "t": tenant_a,
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, 'platform')"
                ),
                {"u": user, "t": tenant_a},
            )

            def contact(tag: str, tid: int) -> int:
                return int(
                    c.execute(
                        text(
                            "INSERT INTO crm.contacts (uid, tenant_id, type, name, email) "
                            "VALUES (:u, :t, 'organisation', :n, :e) RETURNING id"
                        ),
                        {
                            "u": f"sqct_{tag}_{s}",
                            "t": tid,
                            "n": f"Client {tag}",
                            "e": f"{tag}_{s}@clients.test",
                        },
                    ).scalar_one()
                )

            mine = contact("mine", tenant_a)  # in the adviser's book
            other = contact("other", tenant_a)  # same firm, another adviser's client
            theirs = contact("theirs", tenant_b)  # a different firm entirely
            c.execute(
                text(
                    "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                    "VALUES (:t, :cid, CAST(:u AS uuid), 'adviser')"
                ),
                {"t": tenant_a, "cid": mine, "u": user},
            )

            queries: dict[str, str] = {}

            def dataset(
                code: str, query: str, *, scope: str = "global", enabled: bool = True
            ) -> str:
                """Register ``query`` as a test-only catalogue entry and give firm A a dataset that
                picks it.

                A firm cannot write SQL, so this is how an adversarial or oversized statement
                reaches the executor at all — and it reaches it exactly as a shipped entry does,
                which is what makes the runtime proofs below proofs about production. The entry is
                popped from the registry at teardown so it is invisible to every other test.
                """
                key = f"book_test_{code}"
                cat.register(
                    cat.Entry(
                        key=key,
                        name=code,
                        description="Registered by the dataset-envelope security suite.",
                        scope=scope,
                        sql=query,
                        # An entry must declare a column, and one is enough: rows are keyed by what
                        # the statement actually returns, so nothing here reads the declaration.
                        columns=(cat.Column("value", "Value"),),
                    )
                )
                registered.append(key)
                queries[code] = query
                uid = f"sqds_{code}"
                did = c.execute(
                    text(
                        "INSERT INTO documents.datasets "
                        "  (uid, tenant_id, code, name, format, scope, enabled, created_by) "
                        "VALUES (:uid, :t, :code, :code, 'catalogue', "
                        "  CAST(:scope AS documents.dataset_scope), :en, CAST(:by AS uuid)) "
                        "RETURNING id"
                    ),
                    {
                        "uid": uid,
                        "t": tenant_a,
                        "code": code,
                        "scope": scope,
                        "en": enabled,
                        "by": user,
                    },
                ).scalar_one()
                c.execute(
                    text(
                        "INSERT INTO documents.dataset_versions "
                        "  (uid, tenant_id, dataset_id, version_number, status, format, "
                        "   catalogue_ref, created_by, published_at) "
                        "VALUES (:uid, :t, :did, 1, 'published', 'catalogue', "
                        "  CAST(:ref AS jsonb), CAST(:by AS uuid), now())"
                    ),
                    {
                        "uid": f"sqdsv_{code}",
                        "t": tenant_a,
                        "did": did,
                        "ref": json.dumps({"key": key, "params": {}}),
                        "by": user,
                    },
                )
                return uid

            codes = {
                "contacts": f"all_contacts_{s}",
                "attack": f"claims_attack_{s}",
                "attack_quoted": f"claims_attack_quoted_{s}",
                "dml": f"multi_dml_{s}",
                "settings": f"settings_{s}",
                "big": f"big_{s}",
                "subject": f"subject_ok_{s}",
                "subject_loose": f"subject_loose_{s}",
            }
            uids = {
                "contacts": dataset(codes["contacts"], "SELECT id, email FROM crm.contacts"),
                "attack": dataset(
                    codes["attack"],
                    # The escalation attempt: rewrite the claims mid-statement, then scan contacts.
                    "SELECT c.id, c.email FROM (SELECT set_config('request.jwt.claims', "
                    f'\'{{"tenant_id": {tenant_b}, "tenant_ids": [{tenant_b}], '
                    '"permissions": ["contacts:read_all"], "portal": "platform"}\', true)) s, '
                    "crm.contacts c",
                ),
                "attack_quoted": dataset(
                    codes["attack_quoted"],
                    # The same attack with the function name QUOTED. A quoted lowercase identifier
                    # is THE SAME OBJECT in Postgres, so the two spellings are one attack wearing
                    # two names — which is why the defence is the post-statement re-check and not a
                    # scan for a name.
                    "SELECT c.id, c.email FROM (SELECT \"set_config\"('request.jwt.claims', "
                    f'\'{{"tenant_id": {tenant_b}, "tenant_ids": [{tenant_b}], '
                    '"permissions": ["contacts:read_all"], "portal": "platform"}\', true)) s, '
                    "crm.contacts c",
                ),
                "dml": dataset(
                    codes["dml"],
                    f"SELECT 1 AS ok; UPDATE documents.datasets SET name = 'pwned' "
                    f"WHERE tenant_id = {tenant_a}",
                ),
                "settings": dataset(
                    codes["settings"],
                    "SELECT current_setting('transaction_read_only') AS read_only, "
                    "current_setting('statement_timeout') AS timeout",
                ),
                "big": dataset(
                    codes["big"],
                    f"SELECT g AS n FROM generate_series(1, {DATASET_ROW_LIMIT + 25}) g",
                ),
                "subject": dataset(
                    codes["subject"],
                    "SELECT id, email FROM crm.contacts WHERE id = :subject_id",
                    scope="subject",
                ),
                # Binds :subject_id (so it satisfies the subject-scope rule) but does NOT filter on
                # it, so what comes back is decided by the CLAIMS, not the WHERE clause. That is
                # what makes it a probe for "does `scope` actually pick the identity the query runs
                # as?".
                "subject_loose": dataset(
                    codes["subject_loose"],
                    "SELECT id, email FROM crm.contacts WHERE :subject_id IS NOT NULL",
                    scope="subject",
                ),
            }
            c.commit()

        yield {
            "suffix": s,
            "tenant_a": tenant_a,
            "tenant_b": tenant_b,
            "user": user,
            "wos": f"sqwos_{s}",
            "org": f"sqorg_a_{s}",
            "mine": mine,
            "other": other,
            "theirs": theirs,
            "codes": codes,
            "uids": uids,
            "queries": queries,
        }
    finally:
        for key in registered:
            cat._ENTRIES.pop(key, None)


def _claims(firms: dict[str, Any], *, read_all: bool) -> dict[str, Any]:
    """Firm-A staff claims. ``read_all`` is the difference between a full-book staffer and an
    adviser confined to their own clients."""
    return {
        "sub": firms["wos"],
        "role": "adviser",
        "portal": "platform",
        "permissions": ["datasets:read", "contacts:read_all"] if read_all else ["datasets:read"],
        "org_id": firms["org"],
        "user_uuid": firms["user"],
        "tenant_id": firms["tenant_a"],
        "tenant_ids": [firms["tenant_a"]],
        "contact_ids": [firms["mine"]],
        "account_ids": [],
    }


@pytest_asyncio.fixture
async def sessions(db: dict[str, str]):
    """Open an ``authenticated`` session with the given claims — exactly what db.py::tenant_session
    publishes for a request."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _open(claims: dict[str, Any]) -> AsyncIterator:
        async with maker() as session, session.begin():
            # Claims first, THEN the role — as the `app` login role, which is how db.py should order
            # it too (see the handoff on locking set_config down). The GUC is role-independent, so
            # this is identical today; it means these fixtures survive
            # `REVOKE EXECUTE ON FUNCTION set_config(...) FROM PUBLIC`, after which publishing
            # claims while already `authenticated` is refused — which is the point: it is the only
            # way a dataset's SELECT can be stopped from rewriting them mid-scan.
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"),
                {"c": json.dumps(claims)},
            )
            await session.execute(text("SET LOCAL ROLE authenticated"))
            yield session

    yield _open
    await engine.dispose()


# ── the headline finding: the caller's own scope must apply ───────────────────
@pytest.mark.asyncio
async def test_book_scoped_caller_cannot_read_the_whole_firm(
    sessions, firms: dict[str, Any]
) -> None:
    """A dataset is not a privilege escalation: an adviser holding only ``datasets:read`` resolves
    a firm-wide ``crm.contacts`` dataset under THEIR OWN claims, so they see their book only.

    The resolver mints claims for a session that has none. A session that already carries an
    identity keeps it, whatever the dataset's own label says — otherwise this endpoint is a
    firm-wide client-book dump for anyone who can read a dataset."""
    code = firms["codes"]["contacts"]
    async with sessions(_claims(firms, read_all=False)) as session:
        scoped = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
    emails = {r["email"] for r in scoped.rows[code]}
    assert emails == {f"mine_{firms['suffix']}@clients.test"}  # their book, nothing else
    assert not scoped.blocking  # their rows are genuine, so a preview must not fail

    async with sessions(_claims(firms, read_all=True)) as session:
        full = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
    assert len(full.rows[code]) == 2  # a full-book staffer legitimately sees both
    assert not full.problems  # nothing to caveat: they see what the document will


@pytest.mark.asyncio
async def test_a_preview_that_falls_short_of_a_firm_wide_dataset_says_so(
    sessions, firms: dict[str, Any]
) -> None:
    """The caller-facing half of the scope contract. The same ``global`` dataset resolves firm-wide
    in a document, so an adviser previewing it must be TOLD that what they are looking at is their
    book — advisory, because the rows are genuine and a preview must not fail, but said out loud,
    because a subset that reads like the whole answer is what this module exists to stop."""
    code = firms["codes"]["contacts"]
    principal = Principal(
        user_id=firms["wos"],
        organisation_id=firms["org"],
        role="adviser",
        permissions=["datasets:read"],
    )
    async with sessions(_claims(firms, read_all=False)) as session:
        scoped = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
        # …and through the router, which is where an author actually reads it (the page renders
        # `notice`).
        data = await datasets_router.get_dataset_data(
            uid=firms["uids"]["contacts"], principal=principal, session=session
        )
    assert [p.reason for p in scoped.problems] == ["preview_scope"]
    assert not scoped.blocking
    assert "fewer rows" in scoped.problems[0].detail
    assert data.notice and "fewer rows" in data.notice


@pytest.mark.asyncio
async def test_dataset_data_endpoint_respects_caller_scope(sessions, firms: dict[str, Any]) -> None:
    """The same guarantee through the router (GET /datasets/{uid}/data), which is the route the
    audit found: datasets:read alone must not widen the caller's client scope."""
    principal = Principal(
        user_id=firms["wos"],
        organisation_id=firms["org"],
        role="adviser",
        permissions=["datasets:read"],
    )
    async with sessions(_claims(firms, read_all=False)) as session:
        data = await datasets_router.get_dataset_data(
            uid=firms["uids"]["contacts"], principal=principal, session=session
        )
    assert data.rows == [[firms["mine"], f"mine_{firms['suffix']}@clients.test"]]


@pytest.mark.asyncio
async def test_dataset_data_resolves_a_provided_catalogue_entry_by_its_key(
    sessions, firms: dict[str, Any]
) -> None:
    """A PROVIDED catalogue entry has no row in ``documents.datasets`` -- its ``uid`` IS its
    catalogue key (``DatasetSummary.uid``) -- so resolving one by uid has to ask the registry as
    well as the table.

    THE DEFECT: the table lookup alone 404'd every Graphene-provided dataset. The chart panel lists
    a source and its columns straight from the catalogue and then asked this route for the rows, so
    an author got the source, the column names, and "Dataset not found" underneath them.
    """
    key = "book_fees"  # global scope, so it needs no subject
    assert cat.by_key(key) is not None, "the shipped entry this pins has been renamed"
    principal = Principal(
        user_id=firms["wos"],
        organisation_id=firms["org"],
        role="adviser",
        permissions=["datasets:read"],
    )
    async with sessions(_claims(firms, read_all=False)) as session:
        data = await datasets_router.get_dataset_data(uid=key, principal=principal, session=session)
    # The rows are whatever the firm holds; what is pinned is that it RESOLVED rather than 404'd.
    assert isinstance(data.rows, list)


@pytest.mark.asyncio
async def test_dataset_data_still_refuses_an_unknown_uid(sessions, firms: dict[str, Any]) -> None:
    """The registry fallback must not become "anything goes": a key it does not know is still a 404,
    and the key is never used to build a query."""
    principal = Principal(
        user_id=firms["wos"],
        organisation_id=firms["org"],
        role="adviser",
        permissions=["datasets:read"],
    )
    async with sessions(_claims(firms, read_all=False)) as session:
        with pytest.raises(HTTPException) as exc:
            await datasets_router.get_dataset_data(
                uid="book_not_a_real_entry", principal=principal, session=session
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_background_render_resolves_a_global_dataset_firm_wide(
    service_session, firms: dict[str, Any]
) -> None:
    """A ``global`` dataset means firm-wide, and a background render must produce firm-wide rows.

    THE REGRESSION THIS PINS: the render worker's session is ``service_role`` with no principal, so
    the resolver mints the claims — and minting one narrow set for every dataset silently narrows a
    firm-wide dataset to the document's own client, one row where the firm has twelve, with nothing
    said, because a PARTIAL result is indistinguishable from a correct one. Scope decides the
    identity: ``global`` resolves with ``contacts:read_all`` for the dataset's OWN tenant.

    The firm boundary is the half that must not move: firm B's client is still unreachable."""
    codes = firms["codes"]
    s = firms["suffix"]
    resolution = await resolve_datasets_reporting(
        service_session, firms["tenant_a"], [codes["contacts"]], subject_id=firms["mine"]
    )
    emails = {r["email"] for r in resolution.rows[codes["contacts"]]}
    # BOTH of firm A's clients — not just the render's subject.
    assert emails == {f"mine_{s}@clients.test", f"other_{s}@clients.test"}
    assert f"theirs_{s}@clients.test" not in emails  # RLS still held under BYPASSRLS
    assert not resolution.problems  # a faithful resolve reports nothing


@pytest.mark.asyncio
async def test_background_render_scopes_a_subject_dataset_to_the_subject(
    service_session, firms: dict[str, Any]
) -> None:
    """…and the other half: a ``subject`` dataset gets claims carrying ONLY that contact, so a
    per-client dataset cannot come back firm-wide now that ``global`` can.

    The query here binds ``:subject_id`` without filtering on it, so the rows are decided by the
    claims rather than by its WHERE clause — if the resolver minted firm-wide claims for every
    dataset (the mirror-image bug of the one above) this would return the whole firm."""
    codes = firms["codes"]
    s = firms["suffix"]
    resolution = await resolve_datasets_reporting(
        service_session, firms["tenant_a"], [codes["subject_loose"]], subject_id=firms["mine"]
    )
    assert {r["email"] for r in resolution.rows[codes["subject_loose"]]} == {
        f"mine_{s}@clients.test"
    }


@pytest.mark.asyncio
async def test_minting_never_widens_a_session_that_already_has_an_identity(
    db: dict[str, str], firms: dict[str, Any]
) -> None:
    """The obvious way round the firm-wide mint: get to the resolver on a session whose DB role is
    NOT ``authenticated`` but which does carry a principal's claims — ``db.py::firm_session`` and
    the client-portal paths are exactly that shape (``service_role`` + published claims).

    If minting keyed only on the role, a global dataset resolved there would be handed
    ``contacts:read_all`` and hand a book-scoped principal the firm's book. It must instead keep the
    identity it was given — and, because nobody is previewing anything on that path, fall short of a
    ``global`` dataset's declared scope LOUDLY rather than returning the subset."""
    code = firms["codes"]["contacts"]
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session, session.begin():
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"),
                {"c": json.dumps(_claims(firms, read_all=False))},
            )
            await session.execute(text("SET LOCAL ROLE service_role"))
            narrowed = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
        # Not widened (no firm-wide rows) and not silently narrowed either.
        assert narrowed.rows[code] == []
        assert [p.reason for p in narrowed.problems] == ["scope_narrowed"]
        assert narrowed.blocking

        # A full-book principal on the same shape of session resolves it properly — the guard bites
        # on the shortfall, not on the session type.
        async with maker() as session, session.begin():
            await session.execute(
                text("SELECT set_config('request.jwt.claims', :c, true)"),
                {"c": json.dumps(_claims(firms, read_all=True))},
            )
            await session.execute(text("SET LOCAL ROLE service_role"))
            full = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
        assert len(full.rows[code]) == 2
        assert not full.problems
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_narrowed_background_resolve_of_a_global_dataset_is_refused(
    service_session, firms: dict[str, Any]
) -> None:
    """The fail-closed half of the scope contract.

    Minted claims match the dataset's scope by construction, so the interesting case is a
    background resolve under claims supplied from outside (``claims=`` — how a future caller would
    run a render as the generating user). If those claims cannot see the firm, a ``global`` dataset
    comes back as a SUBSET, and a subset in a client document is exactly the silent wrongness this
    module exists to stop. The rows are discarded and the problem BLOCKS."""
    code = firms["codes"]["contacts"]
    narrow = _claims(firms, read_all=False)
    resolution = await resolve_datasets_reporting(
        service_session, firms["tenant_a"], [code], subject_id=firms["mine"], claims=narrow
    )
    assert resolution.rows[code] == []  # NOT the one row the narrow claims could see
    assert [p.reason for p in resolution.problems] == ["scope_narrowed"]
    assert resolution.blocking
    assert "subset" in resolution.problems[0].detail
    # …and the same claims on a SUBJECT dataset are fine: there is no firm-wide claim to fall short
    # of, so the guard doesn't fire where it has no business firing.
    ok = await resolve_datasets_reporting(
        service_session,
        firms["tenant_a"],
        [firms["codes"]["subject"]],
        subject_id=firms["mine"],
        claims=narrow,
    )
    assert [r["id"] for r in ok.rows[firms["codes"]["subject"]]] == [firms["mine"]]
    assert not ok.blocking


# ── the set_config claims-rewrite vector ──────────────────────────────────────
@pytest.mark.parametrize("spelling", ["attack", "attack_quoted"])
@pytest.mark.asyncio
async def test_a_claims_rewrite_is_caught_after_the_statement(
    sessions, firms: dict[str, Any], spelling: str
) -> None:
    """THE BACKSTOP, and now the only thing standing between a mid-scan ``set_config`` and a
    cross-tenant read.

    The statement DOES run — nothing inspects it beforehand — and the executor must still hand back
    no rows: after the statement it re-reads the session's role and claims, sees the identity moved,
    and discards what was scanned under it. The savepoint then puts the caller back as themselves
    for the rest of the request.

    Both spellings are driven because ``"set_config"`` and ``set_config`` are one database object,
    so a defence that reads the query text has to know that and a defence that reads the SESSION
    does not. The control below proves the vector is real."""
    code = firms["codes"][spelling]
    theirs = f"theirs_{firms['suffix']}@clients.test"
    async with sessions(_claims(firms, read_all=True)) as session:
        resolution = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
        tenant_after = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one()
    assert resolution.rows[code] == []
    assert [p.reason for p in resolution.problems] == ["rejected"]
    assert "identity" in resolution.problems[0].detail
    assert theirs not in str(resolution.rows[code])
    assert tenant_after == firms["tenant_a"]


@pytest.mark.asyncio
async def test_control_unguarded_set_config_rewrites_the_claims(
    sessions, firms: dict[str, Any]
) -> None:
    """CONTROL for the test above: run the same string with NO envelope and pin what Postgres
    actually does with a mid-statement ``set_config`` on the GUC every RLS predicate reads.

    MEASURED (Postgres 18): the vector works, and completely. The ``set_config`` in the FROM list is
    evaluated before the ``crm.contacts`` scan, so the statement itself returns the OTHER firm's
    client book and none of the caller's own — cross-tenant exfiltration from a single "SELECT" —
    and the rewritten claims then persist for the rest of the transaction, re-scoping every later
    read in the same request. That is why the post-statement re-check exists rather than a trust in
    planner order; this test fails loudly if that behaviour ever changes.
    """
    query = firms["queries"][firms["codes"]["attack"]]
    theirs = f"theirs_{firms['suffix']}@clients.test"
    async with sessions(_claims(firms, read_all=True)) as session:
        rows = (await session.execute(text(query))).mappings().all()
        leaked_during = {r["email"] for r in rows}
        # The GUC the whole tenant-isolation design rests on, AFTER the statement.
        after = (
            await session.execute(text("SELECT current_setting('request.jwt.claims', true)"))
        ).scalar_one()
        tenant_after = (await session.execute(text("SELECT auth.tenant_id()"))).scalar_one()
        # Anything read later in this transaction is scoped by the rewritten claims.
        leaked_after = {
            r[0] for r in (await session.execute(text("SELECT email FROM crm.contacts"))).all()
        }

    # The rewrite sticks: the caller's identity has been replaced by the query's own choice.
    assert json.loads(after)["tenant_id"] == firms["tenant_b"]
    assert tenant_after == firms["tenant_b"]
    # …and the other firm's client book is then readable in the same transaction.
    assert theirs in leaked_after
    # The statement's OWN scan already returns the other firm's rows (and only those).
    assert leaked_during == {theirs}


# ── single statement, read-only, bounded ──────────────────────────────────────
@pytest.mark.asyncio
async def test_a_second_statement_never_reaches_the_driver(sessions, firms: dict[str, Any]) -> None:
    """``SELECT …; UPDATE …`` cannot execute, because the row cap wraps the statement in a
    subquery: the semicolon becomes a syntax error in the wrapper, reported as an unreadable
    dataset. Belt and braces behind it, the transaction is read-only, so the UPDATE would be
    refused even spelled in a way the wrapper accepted.

    The CONTROL half proves the driver would otherwise run both statements — with no bind
    parameters psycopg uses the simple query protocol, which takes as many statements as it is
    given, so this is the one place a dataset could have issued DML."""
    code = firms["codes"]["dml"]
    async with sessions(_claims(firms, read_all=False)) as session:
        resolution = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
        renamed = (
            await session.execute(
                text("SELECT count(*) FROM documents.datasets WHERE name = 'pwned'")
            )
        ).scalar_one()
    assert resolution.rows[code] == []
    assert [p.reason for p in resolution.problems] == ["error"]
    assert renamed == 0, "the second statement must not have run"

    # CONTROL: the same string, unguarded, with no bind parameters. It executes the UPDATE.
    async with sessions(_claims(firms, read_all=False)) as session:
        await session.execute(text(firms["queries"][code]))
        renamed = (
            await session.execute(
                text("SELECT count(*) FROM documents.datasets WHERE name = 'pwned'")
            )
        ).scalar_one()
        await session.rollback()
    assert renamed > 0, "control: multi-statement DML through a dataset query really does execute"


@pytest.mark.asyncio
async def test_dataset_sql_runs_read_only_and_timed_out(sessions, firms: dict[str, Any]) -> None:
    """The statement runs in a read-only transaction with a statement timeout — and the ambient
    transaction is read-write again afterwards (the savepoint unwinds both settings)."""
    code = firms["codes"]["settings"]
    async with sessions(_claims(firms, read_all=False)) as session:
        resolution = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
        row = resolution.rows[code][0]
        assert row["read_only"] == "on"
        assert row["timeout"] == f"{DATASET_TIMEOUT_MS // 1000}s"
        # The caller's own transaction is untouched: a write still works after resolution.
        await session.execute(
            text("UPDATE documents.datasets SET description = 'after' WHERE code = :c"),
            {"c": code},
        )
        await session.rollback()


@pytest.mark.asyncio
async def test_row_cap_clips_and_reports(sessions, firms: dict[str, Any]) -> None:
    """A huge result is capped by the database (not by the worker's memory), and the truncation is
    reported so the author is told rather than silently handed a partial dataset."""
    code = firms["codes"]["big"]
    async with sessions(_claims(firms, read_all=False)) as session:
        resolution = await resolve_datasets_reporting(session, firms["tenant_a"], [code])
    assert len(resolution.rows[code]) == DATASET_ROW_LIMIT
    truncated = [p for p in resolution.problems if p.reason == "truncated"]
    assert truncated and str(DATASET_ROW_LIMIT) in truncated[0].detail
    assert not resolution.blocking  # advisory, not a reason to fail the render
    # …and the one-line summary a consumer puts on a job/document/toast carries it.
    assert "more than" in (resolution.message() or "")


@pytest.mark.asyncio
async def test_snapshot_notice_reports_every_advisory_problem(
    sessions, firms: dict[str, Any]
) -> None:
    """``GET /datasets/{uid}/data`` reports the truncation in its ``notice`` (via
    ``DatasetResolution.message()``), so "Run query" tells the author the grid is clipped instead of
    looking like the whole answer."""
    principal = Principal(
        user_id=firms["wos"],
        organisation_id=firms["org"],
        role="adviser",
        permissions=["datasets:read"],
    )
    async with sessions(_claims(firms, read_all=False)) as session:
        data = await datasets_router.get_dataset_data(
            uid=firms["uids"]["big"], principal=principal, session=session
        )
    assert data.truncated and len(data.rows) == DATASET_ROW_LIMIT
    assert data.notice and str(DATASET_ROW_LIMIT) in data.notice


# ── scope is real, and failures are reported ──────────────────────────────────
@pytest.mark.asyncio
async def test_subject_scope_is_enforced(sessions, firms: dict[str, Any]) -> None:
    """A client-scoped dataset resolves for the render's client; with no client it fails loudly
    instead of binding NULL."""
    codes = firms["codes"]
    async with sessions(_claims(firms, read_all=False)) as session:
        ok = await resolve_datasets_reporting(
            session, firms["tenant_a"], [codes["subject"]], subject_id=firms["mine"]
        )
        no_subject = await resolve_datasets_reporting(
            session, firms["tenant_a"], [codes["subject"]]
        )
    assert [r["id"] for r in ok.rows[codes["subject"]]] == [firms["mine"]]
    assert [p.reason for p in no_subject.problems] == ["missing_subject"]


@pytest.mark.asyncio
async def test_subject_scope_on_an_inline_dataset_is_refused(
    sessions, super_engine: Engine, firms: dict[str, Any]
) -> None:
    """An inline source cannot be parameterised by a client, so a ``scope='subject'`` one would
    resolve the WHOLE blob into a single client's document. The API refuses the combination on
    save; the resolver refuses it too, because the API is not the only writer — the dev seed script
    inserts directly as an elevated role."""
    s = firms["suffix"]
    code = f"inline_subject_{s}"
    with super_engine.connect() as c:
        did = c.execute(
            text(
                "INSERT INTO documents.datasets "
                "  (uid, tenant_id, code, name, format, scope, created_by) "
                "VALUES (:u, :t, :code, 'Blob', 'inline', 'subject', CAST(:by AS uuid)) "
                "RETURNING id"
            ),
            {"u": f"sqinlsub_{s}", "t": firms["tenant_a"], "code": code, "by": firms["user"]},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.dataset_versions "
                "  (uid, tenant_id, dataset_id, version_number, status, format, inline_data, "
                "   created_by, published_at) VALUES (:u, :t, :did, 1, 'published', 'inline', "
                '  \'{"columns": ["fee"], "rows": [["everyones fee"]]}\'::jsonb, '
                "  CAST(:by AS uuid), now())"
            ),
            {"u": f"sqinlsubv_{s}", "t": firms["tenant_a"], "did": did, "by": firms["user"]},
        )
        c.commit()

    async with sessions(_claims(firms, read_all=False)) as session:
        resolution = await resolve_datasets_reporting(
            session, firms["tenant_a"], [code], subject_id=firms["mine"]
        )
    assert resolution.rows[code] == []  # the blob did NOT reach this client's document
    assert [p.reason for p in resolution.problems] == ["scope_mismatch"]
    assert resolution.blocking  # worth failing a render over, not advisory


@pytest.mark.asyncio
async def test_rows_only_resolver_logs_every_problem(sessions, firms: dict[str, Any]) -> None:
    """``resolve_datasets`` (the contract the render worker still uses) drops the problems, so the
    advisory ones must at least be traceable: a truncated or empty dataset previously left NO log
    line anywhere, so a document shipped short/blank tables with nothing recorded.

    The handler is attached to the resolver's own logger rather than using ``caplog``, and that
    logger is re-enabled first: the test DB bootstrap runs Alembic, whose ``fileConfig`` disables
    every existing logger (and ``configure_logging`` replaces the root handlers), so in this process
    both ``caplog`` and propagation are dead ends."""
    codes = firms["codes"]
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    log = logging.getLogger("document_worker")
    handler = _Collect(level=logging.INFO)
    previous, was_disabled = log.level, log.disabled
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.disabled = False
    try:
        async with sessions(_claims(firms, read_all=False)) as session:
            rows = await resolve_datasets(
                session,
                firms["tenant_a"],
                [codes["big"], codes["subject"]],
                subject_id=firms["theirs"],  # another firm's contact → this dataset returns nothing
            )
    finally:
        log.removeHandler(handler)
        log.setLevel(previous)
        log.disabled = was_disabled

    assert len(rows[codes["big"]]) == DATASET_ROW_LIMIT
    assert rows[codes["subject"]] == []
    logged = "\n".join(r.getMessage() for r in records)
    # Each advisory problem is logged where it happens…
    assert "truncated" in logged and "returned no rows" in logged
    # …and the rows-only wrapper summarises all of them in one WARNING, as its docstring promises.
    summary = [
        r for r in records if r.levelno >= logging.WARNING and "problem(s)" in r.getMessage()
    ]
    assert summary, "the rows-only wrapper logged no summary of the dropped problems"
    assert f"{codes['big']}=truncated" in summary[0].getMessage()
    assert f"{codes['subject']}=empty" in summary[0].getMessage()


@pytest.mark.asyncio
async def test_unresolvable_datasets_are_reported_not_swallowed(
    sessions, super_engine: Engine, firms: dict[str, Any]
) -> None:
    """Missing, disabled and unpublished datasets each come back with their own reason. Previously
    all three resolved to ``[]`` with nothing recorded anywhere — the silent-empty document."""
    s = firms["suffix"]
    with super_engine.connect() as c:
        did = c.execute(
            text(
                "INSERT INTO documents.datasets "
                "  (uid, tenant_id, code, name, format, scope, enabled, created_by) "
                "VALUES (:u, :t, :code, 'Off', 'inline', 'global', false, CAST(:by AS uuid)) "
                "RETURNING id"
            ),
            {"u": f"sqoff_{s}", "t": firms["tenant_a"], "code": f"off_{s}", "by": firms["user"]},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.dataset_versions "
                "  (uid, tenant_id, dataset_id, version_number, status, format, inline_data, "
                "   created_by) VALUES (:u, :t, :did, 1, 'published', 'inline', "
                '  \'{"columns": ["a"], "rows": [[1]]}\'::jsonb, CAST(:by AS uuid))'
            ),
            {"u": f"sqoffv_{s}", "t": firms["tenant_a"], "did": did, "by": firms["user"]},
        )
        draft = c.execute(
            text(
                "INSERT INTO documents.datasets "
                "  (uid, tenant_id, code, name, format, scope, created_by) "
                "VALUES (:u, :t, :code, 'Draft', 'inline', 'global', CAST(:by AS uuid)) "
                "RETURNING id"
            ),
            {"u": f"sqdrf_{s}", "t": firms["tenant_a"], "code": f"draft_{s}", "by": firms["user"]},
        ).scalar_one()
        c.execute(
            text(
                "INSERT INTO documents.dataset_versions "
                "  (uid, tenant_id, dataset_id, version_number, status, format, inline_data, "
                "   created_by) VALUES (:u, :t, :did, 1, 'draft', 'inline', "
                '  \'{"columns": ["a"], "rows": [[1]]}\'::jsonb, CAST(:by AS uuid))'
            ),
            {"u": f"sqdrfv_{s}", "t": firms["tenant_a"], "did": draft, "by": firms["user"]},
        )
        c.commit()

    async with sessions(_claims(firms, read_all=False)) as session:
        resolution = await resolve_datasets_reporting(
            session,
            firms["tenant_a"],
            [f"nope_{s}", f"off_{s}", f"draft_{s}"],
        )
    reasons = {p.code: p.reason for p in resolution.problems}
    assert reasons[f"nope_{s}"] == "missing"
    assert reasons[f"off_{s}"] == "disabled"
    # A draft is unfinished authoring: it must never reach a client document.
    assert reasons[f"draft_{s}"] == "unpublished"
    assert all(resolution.rows[c] == [] for c in reasons)


@pytest.mark.asyncio
async def test_empty_result_is_reported_as_a_problem(sessions, firms: dict[str, Any]) -> None:
    """An empty (but valid) result is advisory rather than blocking — it is still reported, because
    it is exactly what produces a plausible-looking document with blank tables."""
    code = firms["codes"]["subject"]
    async with sessions(_claims(firms, read_all=False)) as session:
        resolution = await resolve_datasets_reporting(
            session, firms["tenant_a"], [code], subject_id=firms["theirs"]
        )
    assert resolution.rows[code] == []
    assert [p.reason for p in resolution.problems] == ["empty"]
    assert not resolution.blocking


@pytest.mark.asyncio
async def test_dataset_data_endpoint_fails_loudly(sessions, firms: dict[str, Any]) -> None:
    """The snapshot endpoint 422s with the reason instead of handing back an empty grid."""
    principal = Principal(
        user_id=firms["wos"],
        organisation_id=firms["org"],
        role="adviser",
        permissions=["datasets:read"],
    )
    async with sessions(_claims(firms, read_all=False)) as session:
        with pytest.raises(HTTPException) as ei:
            await datasets_router.get_dataset_data(
                uid=firms["uids"]["attack"], principal=principal, session=session
            )
    assert ei.value.status_code == 422
    assert "identity" in str(ei.value.detail)


# ── declared contract vs discovered guess ─────────────────────────────────────
# `loop_sources` cannot tell a dataset code from a form answer: it returns every bare
# `{% for x in <name> %}` identifier in the template. Once unresolved datasets started failing the
# render, that heuristic made any template looping over its own inputs fail permanently. Provenance
# is the fix -- a declared link is a contract, a loop identifier is a guess.
@pytest.mark.asyncio
async def test_a_declared_link_that_is_missing_blocks_the_render(
    sessions, firms: dict[str, Any]
) -> None:
    """A code the author declared (a template link or a chart binding) must fail the render: the
    document would otherwise ship to a client with silently empty tables."""
    async with sessions(_claims(firms, read_all=False)) as session:
        resolution = await resolve_datasets_reporting(session, firms["tenant_a"], ["no_such_code"])
    assert [p.reason for p in resolution.problems] == ["missing"]
    assert resolution.blocking, "a declared dataset that does not exist must block"


@pytest.mark.asyncio
async def test_a_speculative_loop_source_that_is_not_a_dataset_is_advisory(
    sessions, firms: dict[str, Any]
) -> None:
    """`{% for h in holdings %}` over a form answer is normal authoring, not a fault. It is reported
    (so a genuine typo is still traceable) but must never block -- the same code passed as a
    declared link DOES block, which is the only difference between the two cases."""
    async with sessions(_claims(firms, read_all=False)) as session:
        resolution = await resolve_datasets_reporting(
            session, firms["tenant_a"], ["holdings"], speculative={"holdings"}
        )
    assert [p.reason for p in resolution.problems] == ["not_a_dataset"]
    assert not resolution.blocking, "a bare loop identifier must not fail the render"
    assert resolution.rows["holdings"] == []  # the loop renders empty rather than erroring
