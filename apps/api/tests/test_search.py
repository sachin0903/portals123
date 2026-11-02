"""Behaviour + isolation tests for the cross-entity search endpoint (app/routers/search.py).

Drives the router coroutines against a real ``authenticated`` session, so the assertions are about
what RLS actually returns — not about a filter this module could get wrong. Proves the properties
the search bars were fabricating before:

- results are the caller's OWN data (book-scoped adviser, client end-user), never another firm's,
  even when two firms have a client with the same name;
- a group is omitted when the caller could not open its entities (``tasks:read``, ``cases:read``),
  so search never advertises a 403/404 — and is NOT omitted for anything else, e.g. a feature flag
  that gates no read path;
- a client end-user gets no "Clients" group and only the active person's accounts, holdings,
  transactions, delivered documents, eligible insights and open action items — including when the
  ``portal`` claim is missing entirely;
- LIKE wildcards typed by the user are escaped, so ``%`` is a search term and not "match all";
- a too-short query is an honest empty result, never a fallback list.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import Principal
from app.config import settings
from app.routers import search as search_router
from tests.conftest import api_routes


def _principal(perms: list[str]) -> Principal:
    """Satisfies the per-group permission gates; row visibility comes from the session claims."""
    return Principal(user_id="x", organisation_id="x", role="adviser", permissions=perms)


# App-only portal (0107): the platform application's client id, shared by both fixture firms —
# resolve_claims matches it against the CALLER'S OWN tenant's environment row, so reuse is safe.
_APP_PLATFORM = "app_search_platform"


@asynccontextmanager
async def _staff(app_url: str, workos_user: str) -> AsyncIterator[AsyncSession]:
    """A staff session — SET ROLE authenticated + claims minted by auth.resolve_claims through the
    platform application (0107), exactly like db.py::tenant_session. No contacts:read_all, so the
    caller is book-scoped."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        resolved = (
            await session.execute(
                text("SELECT auth.resolve_claims(:w, NULL, :app)"),
                {"w": workos_user, "app": _APP_PLATFORM},
            )
        ).scalar_one()
        claims = {"sub": workos_user, "role": "adviser", "permissions": [], **resolved}
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


@asynccontextmanager
async def _client(
    app_url: str,
    user_uuid: str,
    tenant: int,
    contact_ids: list[int],
    *,
    portal: str | None = "client",
    role: str = "authenticated",
) -> AsyncIterator[AsyncSession]:
    """A CLIENT-portal session: portal='client' empties auth.tenant_lineage() (the 0107 whitelist),
    so only the contact_scope branches can match. Hand-crafted claims — the portal classification
    would need a client application on the tenant's environment row, which this bed doesn't seed.

    ``portal``/``role`` are parameters because the portal claim is NOT guaranteed: resolve_claims
    mints NULL for an unknown application or a missing membership (0107), and the RLS layer must
    fail closed on that rather than default anyone to staff reach."""
    engine = create_async_engine(app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session, session.begin():
        await session.execute(text("SET LOCAL ROLE authenticated"))
        claims: dict[str, object] = {
            "sub": "client",
            "role": role,
            "permissions": [],
            "user_uuid": user_uuid,
            "tenant_id": tenant,
            "tenant_ids": [tenant],
            "contact_ids": contact_ids,
            "account_ids": [],
            "role_ids": [],
        }
        if portal is not None:
            claims["portal"] = portal
        await session.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)}
        )
        yield session
    await engine.dispose()


def _titles(results: search_router.SearchResults, kind: str) -> list[str]:
    return [h.title for g in results.groups if g.type == kind for h in g.hits]


def _ids(results: search_router.SearchResults, kind: str) -> list[str]:
    return [h.id for g in results.groups if g.type == kind for h in g.hits]


def _kind_hits(results: search_router.SearchResults, kind: str) -> list[search_router.SearchHit]:
    return [hit for group in results.groups if group.type == kind for hit in group.hits]


@pytest.fixture
def searchbed(super_engine: Engine) -> dict[str, object]:
    """Two firms whose books deliberately collide on the surname "Cole".

    Firm A: an adviser whose book is Margaret Cole only (Nigel Cole is another adviser's client);
    a document delivered to Margaret, one delivered to Nigel, one firm-internal with no client
    link; a contact-assigned task for Margaret and a role-assigned internal one; a published flow
    with one running execution for Margaret. Firm B: its own "Margaret Cole" and her document.
    """
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def tenant(prefix: str, name: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"{prefix}_{s}", "a": f"{prefix}-{s}", "n": name},
                ).scalar_one()
            )

        def user(tenant_id: int, prefix: str, wos: str) -> str:
            return str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, enabled) "
                        "VALUES (:uid, :t, :e, :w, true) RETURNING id"
                    ),
                    {
                        "uid": f"{prefix}_{s}",
                        "t": tenant_id,
                        "e": f"{prefix}_{s}@firm.test",
                        "w": wos,
                    },
                ).scalar_one()
            )

        def contact(tenant_id: int, prefix: str, first: str, surname: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname, "
                        "  email, mobile, phone) "
                        "VALUES (:t, :u, 'individual', :f, :sn, :e, :m, :pn) "
                        "RETURNING id"
                    ),
                    {
                        "t": tenant_id,
                        "u": f"{prefix}_{s}",
                        "f": first,
                        "sn": surname,
                        "e": f"{prefix}_{s}@client.test",
                        # Staff quote a number off a missed call, so both halves have to be
                        # reachable: the primary mobile and any of the other numbers.
                        "m": "07700900123",
                        "pn": "01179000456",
                    },
                ).scalar_one()
            )

        def document(tenant_id: int, prefix: str, name: str, contact_id: int | None) -> str:
            uid = f"{prefix}_{s}"
            doc = str(
                c.execute(
                    text(
                        "INSERT INTO documents.documents (uid, tenant_id, document_type, name, "
                        "  status) VALUES (:u, :t, 'review', :n, 'generated') RETURNING id"
                    ),
                    {"u": uid, "t": tenant_id, "n": name},
                ).scalar_one()
            )
            if contact_id is not None:
                c.execute(
                    text(
                        "INSERT INTO documents.document_contacts (tenant_id, document_id, "
                        "  contact_id) VALUES (:t, :d, :c)"
                    ),
                    {"t": tenant_id, "d": doc, "c": contact_id},
                )
            return uid

        ten_a, ten_b = tenant("sfa", f"SearchFirm {s}"), tenant("sfb", f"OtherFirm {s}")
        # App-only portal (0107): each firm's environment declares the platform application the
        # staff sessions sign in through; the memberships below are what grant them the portal.
        for ten in (ten_a, ten_b):
            c.execute(
                text(
                    "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                    "  platform_client_id) VALUES (:t, 'dev', :env, :app)"
                ),
                {"t": ten, "env": f"env_search_{ten}_{s}", "app": _APP_PLATFORM},
            )
        role = int(
            c.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, 'compliance', 'Compliance', 'platform') RETURNING id"
                ),
                {"t": ten_a},
            ).scalar_one()
        )
        wos_a, wos_b = f"swosa_{s}", f"swosb_{s}"
        staff_a, staff_b = user(ten_a, "sadv", wos_a), user(ten_b, "sadvb", wos_b)
        for uid, tenant_id, role_id in ((staff_a, ten_a, role), (staff_b, ten_b, None)):
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, "
                    "  role_id, status) VALUES (:u, :t, 'platform', :r, 'active')"
                ),
                {"u": uid, "t": tenant_id, "r": role_id},
            )

        mine = contact(ten_a, "sc1", "Margaret", "Cole")
        theirs = contact(ten_a, "sc2", "Nigel", "Cole")
        other_firm = contact(ten_b, "sc3", "Margaret", "Cole")
        # Firm A's adviser has Margaret in their book only — Nigel belongs to a colleague. Firm B's
        # adviser has their own namesake Margaret, so the cross-tenant test compares like with like.
        for tenant_id, contact_id, adviser in (
            (ten_a, mine, staff_a),
            (ten_b, other_firm, staff_b),
        ):
            c.execute(
                text(
                    "INSERT INTO crm.client_advisers (tenant_id, contact_id, adviser_id, role) "
                    "VALUES (:t, :c, :u, 'adviser')"
                ),
                {"t": tenant_id, "c": contact_id, "u": adviser},
            )
        client_login = user(ten_a, "scli", f"swosc_{s}")
        c.execute(
            text(
                "INSERT INTO crm.contact_users (tenant_id, contact_id, user_id) VALUES (:t, :c, :u)"
            ),
            {"t": ten_a, "c": mine, "u": client_login},
        )

        product = int(
            c.execute(
                text(
                    "INSERT INTO platform.products "
                    "  (tenant_id, product_type, name, code, enabled) "
                    "VALUES (:t, 'ISA', 'Cole Stocks and Shares ISA', 'ISA', true) RETURNING id"
                ),
                {"t": ten_a},
            ).scalar_one()
        )
        account_uid = f"sacc_{s}"
        account = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts "
                    "  (tenant_id, uid, contact_id, product_id, name, status, currency, value) "
                    "VALUES (:t, :u, :c, :p, 'Cole Investment Account', 'active', 'GBP', 12500) "
                    "RETURNING id"
                ),
                {"t": ten_a, "u": account_uid, "c": mine, "p": product},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO platform.accounts "
                "  (tenant_id, uid, contact_id, name, status, currency) "
                "VALUES (:t, :u, :c, :n, 'active', 'GBP')"
            ),
            {
                "t": ten_a,
                "u": f"sacc2_{s}",
                "c": mine,
                "n": f"{account_uid} reserve",
            },
        )
        transaction_id = f"cole_dividend_{s}"
        c.execute(
            text(
                "INSERT INTO platform.transactions "
                "  (tenant_id, account_id, contact_id, source_system, source_transaction_id, "
                "   type, source_type, source_subtype, asset_name, isin, amount, currency, "
                "   transaction_date) "
                "VALUES (:t, :a, :c, 'seccl', :sid, 'dividend', 'Income', 'Cole dividend', "
                "        'Cole Global Equity', 'GB00COLE0001', 84.25, 'GBP', CURRENT_DATE)"
            ),
            {"t": ten_a, "a": account, "c": mine, "sid": transaction_id},
        )
        insight_uid = f"sins_{s}"
        c.execute(
            text(
                "INSERT INTO content.items "
                "  (uid, tenant_id, kind, title, slug, summary, status, audience, created_by, "
                "   published_at) "
                "VALUES (:u, :t, 'article', 'Cole market outlook', :slug, "
                "        'What changing markets mean for the Cole portfolio.', "
                "        'published', 'all', :by, now())"
            ),
            {
                "u": insight_uid,
                "t": ten_a,
                "slug": f"cole-market-outlook-{s}",
                "by": staff_a,
            },
        )

        doc_mine = document(ten_a, "sd1", "Cole Annual Review", mine)
        doc_theirs = document(ten_a, "sd2", "Cole Suitability Report", theirs)
        doc_internal = document(ten_a, "sd3", "Cole Family Policy Note", None)
        doc_other_firm = document(ten_b, "sd4", "Cole Annual Review", other_firm)

        task_mine = str(
            c.execute(
                text(
                    "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_contact_id, "
                    "  title) VALUES (:u, :t, 'contact', :c, 'Confirm the Cole address') "
                    "RETURNING uid"
                ),
                {"u": f"stk1_{s}", "t": ten_a, "c": mine},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_role_id, title) "
                "VALUES (:u, :t, 'role', :r, 'Cole compliance review')"
            ),
            {"u": f"stk2_{s}", "t": ten_a, "r": role},
        )

        flow = int(
            c.execute(
                text(
                    "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                    "VALUES (:u, :t, 'onboarding', 'Cole Onboarding', 'published') RETURNING id"
                ),
                {"u": f"sflw_{s}", "t": ten_a},
            ).scalar_one()
        )
        version = int(
            c.execute(
                text(
                    "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status) "
                    "VALUES (:u, :t, :f, 1, 'published') RETURNING id"
                ),
                {"u": f"sflv_{s}", "t": ten_a, "f": flow},
            ).scalar_one()
        )
        exec_uid = str(
            c.execute(
                text(
                    "INSERT INTO flows.executions (uid, tenant_id, flow_id, flow_version_id, "
                    "  status, subject_type, subject_id) "
                    "VALUES (:u, :t, :f, :v, 'active', 'contact', :c) RETURNING uid"
                ),
                {"u": f"sfex_{s}", "t": ten_a, "f": flow, "v": version, "c": mine},
            ).scalar_one()
        )
        conversation_uid = f"scnv_{s}"
        conversation = int(
            c.execute(
                text(
                    "INSERT INTO messaging.conversations "
                    "  (uid, tenant_id, contact_id, adviser_user_id) "
                    "VALUES (:u, :t, :c, :a) RETURNING id"
                ),
                {"u": conversation_uid, "t": ten_a, "c": mine, "a": staff_a},
            ).scalar_one()
        )
        c.execute(
            text(
                "INSERT INTO messaging.messages "
                "  (tenant_id, conversation_id, contact_id, sender_kind, sender_user_id, body) "
                "VALUES (:t, :conversation, :contact, 'staff', :sender, "
                "        'Cole asked about the annual allowance')"
            ),
            {
                "t": ten_a,
                "conversation": conversation,
                "contact": mine,
                "sender": staff_a,
            },
        )
        review_uid = f"srev_{s}"
        c.execute(
            text(
                "INSERT INTO baton.runs "
                "  (uid, tenant_id, case_no, action_key, meta, subject_kind, subject_uid, "
                "   contact_id, params, justification, requested_by, idempotency_key, "
                "   state_type, state_name) "
                "VALUES (:u, :t, 41, 'account.bank_details.update', "
                '        \'{"event_type": "account.bank_details.updated"}\'::jsonb, '
                "        'account', :account_uid, :contact, '{}'::jsonb, "
                "        'Cole requested a bank detail change', :proposer, :key, "
                "        'paused', 'AwaitingApproval')"
            ),
            {
                "u": review_uid,
                "t": ten_a,
                "account_uid": account_uid,
                "contact": mine,
                "proposer": staff_a,
                "key": f"search-review-{s}",
            },
        )
        c.commit()
    return {
        "sfx": s,
        "tenant_a": ten_a,
        "wos_a": wos_a,
        "wos_b": wos_b,
        "client_login": client_login,
        "mine": mine,
        # The hit id a portal turns into a URL is the contact's UID, never the serial.
        "mine_uid": f"sc1_{s}",
        "theirs": theirs,
        "doc_mine": doc_mine,
        "doc_theirs": doc_theirs,
        "doc_internal": doc_internal,
        "doc_other_firm": doc_other_firm,
        "task_mine": task_mine,
        "exec_uid": exec_uid,
        "account_uid": account_uid,
        "transaction_id": transaction_id,
        "insight_uid": insight_uid,
        "conversation_uid": conversation_uid,
        "review_uid": review_uid,
    }


@pytest.mark.asyncio
async def test_staff_search_is_book_scoped(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """A book-scoped adviser searching "Cole" gets their own client and the documents they may
    open — never the colleague's client or that client's document."""
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        results = await search_router.search(
            q="cole", limit=10, principal=_principal(["tasks:read"]), session=session
        )
    assert _titles(results, "client") == ["Margaret Cole"]
    assert _ids(results, "client") == [str(searchbed["mine_uid"])]
    docs = _ids(results, "document")
    assert searchbed["doc_mine"] in docs
    # Firm-internal (no client link) is readable by any staffer; the colleague's client's is not.
    assert searchbed["doc_internal"] in docs
    assert searchbed["doc_theirs"] not in docs
    assert searchbed["doc_other_firm"] not in docs
    assert set(_titles(results, "task")) == {"Confirm the Cole address", "Cole compliance review"}
    assert results.query == "cole"


@pytest.mark.asyncio
async def test_staff_group_contract_and_permission_omission(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """Returned groups keep catalogue order; absent matches and unauthorised groups vanish."""
    all_permissions = ["tasks:read", "cases:read", "messages:read"]
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        complete = await search_router.search(
            q="cole", limit=10, principal=_principal(all_permissions), session=session
        )
        restricted = await search_router.search(
            q="cole", limit=10, principal=_principal([]), session=session
        )

    assert [group.type for group in complete.groups] == [
        "client",
        "account",
        "document",
        "task",
        "case",
        "transaction",
        "message",
        "review",
    ]
    assert _ids(complete, "message") == [searchbed["conversation_uid"]]
    assert _kind_hits(complete, "message")[0].target == searchbed["conversation_uid"]
    assert _ids(complete, "review") == [searchbed["review_uid"]]
    # Groups remain in the response for contextual filtering, but every hit carries one global
    # order so the palette does not render eight category blocks.
    ranks = [hit.rank for group in complete.groups for hit in group.hits]
    assert all(rank is not None for rank in ranks)
    assert sorted(ranks) == list(range(1, len(ranks) + 1))
    # Reviews follow the queue: any staff member can open them. The decision is the
    # role on the action, so a permissionless search still returns the review.
    assert {"task", "case", "message"}.isdisjoint(group.type for group in restricted.groups)
    assert _ids(restricted, "review") == [searchbed["review_uid"]]


@pytest.mark.asyncio
async def test_staff_scope_executes_only_the_requested_authorised_group(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        accounts = await search_router.search(
            q="cole",
            limit=10,
            scope="accounts",
            principal=_principal(["tasks:read", "messages:read"]),
            session=session,
        )
        forbidden_tasks = await search_router.search(
            q="cole",
            limit=10,
            scope="tasks",
            principal=_principal([]),
            session=session,
        )

    assert [group.type for group in accounts.groups] == ["account"]
    assert forbidden_tasks.groups == []


@pytest.mark.asyncio
async def test_staff_transactions_match_reference_only(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """The largest fact table does not join ordinary name search; its provider reference remains
    available and is printed in the row so the reason for the match is visible."""
    reference = str(searchbed["transaction_id"])
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        by_reference = await search_router.search(
            q=reference,
            limit=10,
            scope="transactions",
            principal=_principal([]),
            session=session,
        )
        by_account_name = await search_router.search(
            q="investment",
            limit=10,
            scope="transactions",
            principal=_principal([]),
            session=session,
        )

    hits = _kind_hits(by_reference, "transaction")
    assert [hit.id for hit in hits] == [f"seccl:{reference}"]
    assert hits[0].subtitle is not None
    assert reference in hits[0].subtitle
    assert by_account_name.groups == []


@pytest.mark.asyncio
async def test_exact_identifier_ranks_before_prefix_and_two_characters_do_not_fuzzy_match(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    account_uid = str(searchbed["account_uid"])
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        ranked = await search_router.search(
            q=account_uid,
            limit=10,
            scope="accounts",
            principal=_principal([]),
            session=session,
        )
        prefix = await search_router.search(
            q="co", limit=10, scope="clients", principal=_principal([]), session=session
        )
        infix = await search_router.search(
            q="ol", limit=10, scope="clients", principal=_principal([]), session=session
        )
        fuzzy = await search_router.search(
            q="colr", limit=10, scope="clients", principal=_principal([]), session=session
        )
        typo = await search_router.search(
            q="margret", limit=10, scope="clients", principal=_principal([]), session=session
        )

    assert _ids(ranked, "account")[:2] == [account_uid, f"sacc2_{searchbed['sfx']}"]
    assert _titles(prefix, "client") == ["Margaret Cole"]
    assert _titles(infix, "client") == []
    assert _titles(fuzzy, "client") == ["Margaret Cole"]
    assert _titles(typo, "client") == ["Margaret Cole"]


@pytest.mark.asyncio
async def test_a_client_is_found_by_any_of_their_numbers(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """The mobile AND the other numbers, because a staffer searches with whatever is on the screen.

    This searched `crm.contacts.phone` alone, which held the LANDLINE — so the number a client had
    just rung in on found nobody, which reads as "search is broken" and is really "search looks in
    one of the two columns". 0164 left one scalar and one array; both are searched.
    """
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        by_mobile = await search_router.search(
            q="07700 900123", limit=10, principal=_principal([]), session=session
        )
        by_other = await search_router.search(
            q="+44 117 900 0456", limit=10, principal=_principal([]), session=session
        )
        by_neither = await search_router.search(
            q="02079460000", limit=10, principal=_principal([]), session=session
        )
    assert _titles(by_mobile, "client") == ["Margaret Cole"]
    assert _titles(by_other, "client") == ["Margaret Cole"]
    # And it is a real match, not "any number matches anything".
    assert _titles(by_neither, "client") == []


@pytest.mark.asyncio
async def test_cross_tenant_namesake_is_invisible(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """The other firm has its own "Margaret Cole": each firm sees only its own row, and neither
    sees the other's document. This is the failure the fabricated search bars simulated."""
    async with _staff(db["app"], str(searchbed["wos_b"])) as session:
        results = await search_router.search(
            q="cole", limit=10, principal=_principal([]), session=session
        )
    assert _titles(results, "client") == ["Margaret Cole"]
    assert _ids(results, "client") != [str(searchbed["mine_uid"])]
    assert _ids(results, "document") == [searchbed["doc_other_firm"]]


@pytest.mark.asyncio
async def test_client_portal_sees_only_its_own(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """A client end-user gets no Clients group at all, only documents actually delivered to them,
    and only their own open action items."""
    async with _client(
        db["app"],
        str(searchbed["client_login"]),
        int(searchbed["tenant_a"]),  # type: ignore[call-overload]
        [int(searchbed["mine"])],  # type: ignore[call-overload]
    ) as session:
        results = await search_router.search(
            q="cole", limit=10, principal=_principal([]), session=session
        )
    assert [g.type for g in results.groups if g.type == "client"] == []
    assert _ids(results, "account") == [searchbed["account_uid"]]
    transaction = _kind_hits(results, "transaction")
    assert [hit.id for hit in transaction] == [f"seccl:{searchbed['transaction_id']}"]
    assert transaction[0].target == searchbed["account_uid"]
    assert _ids(results, "document") == [searchbed["doc_mine"]]
    assert _ids(results, "insight") == [searchbed["insight_uid"]]
    assert _ids(results, "task") == [searchbed["task_mine"]]


@pytest.mark.asyncio
async def test_client_holding_hits_keep_their_identity_and_target_the_account(
    db: dict[str, str],
    searchbed: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A holding is a selectable result in its own right, but opens the account that owns it."""

    async def holdings(*_: object, **__: object) -> list[dict[str, object]]:
        return [
            {
                "asset_id": "asset_global",
                "asset_name": "Cole Global Equity",
                "isin": "GB00COLE0001",
                "account_name": "Cole Investment Account",
                "account_uid": searchbed["account_uid"],
                "value": 6125.5,
            },
            {
                "asset_id": "asset_global",
                "asset_name": "Cole Global Equity",
                "isin": "GB00COLE0001",
                "account_name": "Cole ISA",
                "account_uid": "acc_other",
                "value": 100.0,
            },
        ]

    monkeypatch.setattr("app.routers.crm.search_holdings", holdings)
    async with _client(
        db["app"],
        str(searchbed["client_login"]),
        int(searchbed["tenant_a"]),  # type: ignore[call-overload]
        [int(searchbed["mine"])],  # type: ignore[call-overload]
    ) as session:
        results = await search_router.search(
            q="global",
            limit=10,
            contact_id=int(searchbed["mine"]),  # type: ignore[call-overload]
            principal=_principal([]),
            session=session,
        )

    account = searchbed["account_uid"]
    hit = _kind_hits(results, "holding")
    # The same instrument on two accounts is two hits. A shared asset id collapsed them
    # into one list key and the palette threw, leaving "Searching…" up.
    assert [(item.id, item.target, item.meta) for item in hit] == [
        (f"{account}:asset_global", account, "£6,125.50"),
        ("acc_other:asset_global", "acc_other", "£100.00"),
    ]


@pytest.mark.asyncio
async def test_an_unresponsive_lake_does_not_hold_the_whole_search_open(
    db: dict[str, str],
    searchbed: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never_finishes(*_: object, **__: object) -> list[dict[str, object]]:
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr("app.routers.crm.search_holdings", never_finishes)
    monkeypatch.setattr(search_router, "HOLDINGS_TIMEOUT_SECONDS", 0.001)
    async with _client(
        db["app"],
        str(searchbed["client_login"]),
        int(searchbed["tenant_a"]),  # type: ignore[call-overload]
        [int(searchbed["mine"])],  # type: ignore[call-overload]
    ) as session:
        results = await search_router.search(
            q="cole",
            limit=10,
            contact_id=int(searchbed["mine"]),  # type: ignore[call-overload]
            principal=_principal([]),
            session=session,
        )

    assert _ids(results, "account") == [searchbed["account_uid"]]
    assert _kind_hits(results, "holding") == []


@pytest.mark.asyncio
async def test_the_active_contact_narrows_a_multi_client_login(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """A login linked to TWO clients searches the one it is currently acting for.

    The client portal has an active-contact switcher and every other read on it takes
    ``?contact_id=``; search did not, so a guardian acting for two people was offered both
    households' documents in one list — and a hit carries no owner, so opening it was the only
    way to find out whose it was. Un-narrowed still answers over the whole scope, which is what
    a single-client login (and every staff caller) gets.
    """
    both = [int(searchbed["mine"]), int(searchbed["theirs"])]  # type: ignore[call-overload]
    async with _client(
        db["app"],
        str(searchbed["client_login"]),
        int(searchbed["tenant_a"]),  # type: ignore[call-overload]
        both,
    ) as session:
        everything = await search_router.search(
            q="cole", limit=10, principal=_principal([]), session=session
        )
        margaret = await search_router.search(
            q="cole",
            limit=10,
            contact_id=int(searchbed["mine"]),  # type: ignore[call-overload]
            principal=_principal([]),
            session=session,
        )
        nigel = await search_router.search(
            q="cole",
            limit=10,
            contact_id=int(searchbed["theirs"]),  # type: ignore[call-overload]
            principal=_principal([]),
            session=session,
        )

    # Un-narrowed: both clients' post in one list, which is the defect.
    assert set(_ids(everything, "document")) == {searchbed["doc_mine"], searchbed["doc_theirs"]}
    # Narrowed: each client's own, and the action item follows the same rule — Margaret has one
    # and Nigel has none, so an empty group is the correct answer rather than a missing filter.
    assert _ids(margaret, "document") == [searchbed["doc_mine"]]
    assert _ids(margaret, "task") == [searchbed["task_mine"]]
    assert _ids(margaret, "account") == [searchbed["account_uid"]]
    assert _ids(margaret, "transaction") == [f"seccl:{searchbed['transaction_id']}"]
    assert _ids(nigel, "document") == [searchbed["doc_theirs"]]
    assert _ids(nigel, "task") == []
    assert _ids(nigel, "account") == []
    assert _ids(nigel, "transaction") == []


@pytest.mark.asyncio
async def test_a_staff_caller_is_unaffected_by_a_contact_id(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """The parameter is the CLIENT portal's. Staff never send it and their groups stay book-wide —
    narrowing a firm search by one contact would quietly hide the rest of an adviser's book."""
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        plain = await search_router.search(
            q="cole", limit=10, principal=_principal(["tasks:read"]), session=session
        )
        narrowed = await search_router.search(
            q="cole",
            limit=10,
            contact_id=int(searchbed["theirs"]),  # type: ignore[call-overload]
            principal=_principal(["tasks:read"]),
            session=session,
        )
    # Identical, group for group: the same book-wide answer, including the firm-internal document
    # that is filed against no client at all and would vanish under a contact filter.
    assert [(g.type, _ids(narrowed, g.type)) for g in narrowed.groups] == [
        (g.type, _ids(plain, g.type)) for g in plain.groups
    ]
    assert _titles(narrowed, "client") == ["Margaret Cole"]
    assert searchbed["doc_internal"] in _ids(narrowed, "document")


@pytest.mark.asyncio
async def test_tasks_group_needs_tasks_read(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """Staff without tasks:read get no task hits — the /tasks endpoints would 403, so advertising
    them in search would be a dead result."""
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        results = await search_router.search(
            q="cole", limit=10, principal=_principal([]), session=session
        )
    assert _titles(results, "task") == []
    assert _titles(results, "client") == ["Margaret Cole"]


@pytest.mark.asyncio
async def test_cases_group_needs_only_cases_read(
    db: dict[str, str], searchbed: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Journeys appear on ``cases:read`` alone — the same gate GET /applications/{uid} enforces
    (applications.py::get_application), so a hit is always openable.

    It must NOT depend on ``flows_orchestrator_v2``: that flag guards no read path and is set
    nowhere in the deploy compose, so gating on it made the group permanently invisible on every
    real deployment. This asserts both flag values, with the DEFAULT (False) first — that is the
    assertion the previous implementation failed."""
    monkeypatch.setattr(settings, "flows_orchestrator_v2", False)
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        flag_off = await search_router.search(
            q="cole", limit=10, principal=_principal(["cases:read"]), session=session
        )
        no_permission = await search_router.search(
            q="cole", limit=10, principal=_principal([]), session=session
        )
        monkeypatch.setattr(settings, "flows_orchestrator_v2", True)
        flag_on = await search_router.search(
            q="cole", limit=10, principal=_principal(["cases:read"]), session=session
        )
    assert _ids(flag_off, "case") == [searchbed["exec_uid"]]
    assert _titles(flag_off, "case") == ["Cole Onboarding"]
    assert _ids(flag_on, "case") == [searchbed["exec_uid"]]
    assert _ids(no_permission, "case") == []


@pytest.mark.asyncio
async def test_client_without_a_portal_claim_still_gets_no_clients_group(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """A missing portal claim fails CLOSED, never up. ``auth.portal()`` is NULL whenever
    resolve_claims cannot classify the application (0107), so keying the Clients group solely on
    ``portal != 'client'`` would offer a client end-user the firm-wide client group on a missing
    claim — under 0107 ``auth.tenant_lineage()`` is a whitelist (empty without a portal), so the
    staff branch must surface nothing here."""
    async with _client(
        db["app"],
        str(searchbed["client_login"]),
        int(searchbed["tenant_a"]),  # type: ignore[call-overload]
        [int(searchbed["mine"])],  # type: ignore[call-overload]
        portal=None,
        role="client",
    ) as session:
        results = await search_router.search(
            q="cole", limit=10, principal=_principal([]), session=session
        )
    assert [g.type for g in results.groups if g.type == "client"] == []
    # Still their own documents and only their own OPEN action items — the client branch, not the
    # staff one, is what a missing portal claim must fall back to.
    assert _ids(results, "document") == [searchbed["doc_mine"]]
    assert _ids(results, "task") == [searchbed["task_mine"]]


@pytest.mark.asyncio
async def test_wildcards_are_escaped_and_short_queries_are_empty(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """``%%`` searches for two per-cent signs (matching nothing) rather than every row, and a
    one-character query returns an honest empty result instead of a fallback list."""
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        wildcard = await search_router.search(
            q="%%", limit=10, principal=_principal(["tasks:read"]), session=session
        )
        underscore = await search_router.search(
            q="c_le", limit=10, principal=_principal(["tasks:read"]), session=session
        )
        short = await search_router.search(
            q="c", limit=10, principal=_principal(["tasks:read"]), session=session
        )
    assert wildcard.groups == []
    assert underscore.groups == []
    assert short.groups == []
    assert short.query == "c"


@pytest.mark.asyncio
async def test_group_cap_and_truncation_flag(
    db: dict[str, str], searchbed: dict[str, object]
) -> None:
    """``limit`` caps each group and ``truncated`` says so, so the UI never implies it showed
    everything."""
    async with _staff(db["app"], str(searchbed["wos_a"])) as session:
        capped = await search_router.search(
            q="cole", limit=1, principal=_principal(["tasks:read"]), session=session
        )
    assert len(_ids(capped, "document")) == 1
    assert capped.truncated is True


@pytest.mark.asyncio
async def test_operations_firm_search(
    searchbed: dict[str, object], service_session: AsyncSession
) -> None:
    """The ops bar returns real firms (the fabricated one listed five invented ones), hides the
    model tenant, and refuses a caller without tenants:read."""
    # This bed's own suffix, so the assertion is independent of firms other tests leave behind.
    suffix = str(searchbed["sfx"])
    results = await search_router.search_tenants(
        q=suffix, limit=10, principal=_principal(["tenants:read"]), session=service_session
    )
    hits = results.groups[0].hits
    assert {h.title for h in hits} == {f"OtherFirm {suffix}", f"SearchFirm {suffix}"}
    # The hit's id is the firm's UID, not its serial id: the bar navigates straight to
    # `/tenants/{uid}`, which is how the admin portal addresses a firm. Returning the serial id
    # here sent every search result to a URL that 404s.
    assert next(h.id for h in hits if h.title.startswith("SearchFirm")) == f"sfa_{suffix}"

    hidden = await search_router.search_tenants(
        q="model tenant", limit=10, principal=_principal(["tenants:read"]), session=service_session
    )
    assert hidden.groups == []

    with pytest.raises(HTTPException) as refused:
        await search_router.search_tenants(
            q=suffix, limit=10, principal=_principal([]), session=service_session
        )
    assert refused.value.status_code == 403


# Every table whose fuzzy predicate reads `_search`, with the index 0211 gave it.
_SEARCH_COLUMNS = {
    "crm.contacts": "contacts_search_trgm",
    "platform.accounts": "accounts_search_trgm",
    "platform.products": "products_search_trgm",
    "documents.documents": "documents_search_trgm",
    "tasks.tasks": "tasks_search_trgm",
    "flows.executions": "executions_search_trgm",
    "flows.flows": "flows_search_trgm",
    "messaging.groups": "groups_search_trgm",
    "baton.runs": "baton_runs_search_trgm",
    "auth.tenants": "tenants_search_trgm",
}


def test_search_columns_are_generated_and_trigram_indexed(super_engine: Engine) -> None:
    """``_search`` is a stored generated column with its own trigram index on every searched table,
    and the 0182 expression indexes it replaced are gone.

    Generated is the property that matters: a plain column would go stale the first time a writer
    forgot to maintain it, and search would quietly stop finding renamed clients."""
    with super_engine.connect() as conn:
        for table, index in _SEARCH_COLUMNS.items():
            schema, name = table.split(".")
            generated = conn.execute(
                text(
                    "SELECT a.attgenerated FROM pg_attribute a "
                    "WHERE a.attrelid = CAST(:t AS regclass) AND a.attname = '_search' "
                    "  AND NOT a.attisdropped"
                ),
                {"t": table},
            ).scalar_one_or_none()
            assert generated == "s", f"{table}._search is not a stored generated column"
            definition = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE schemaname = :s AND indexname = :i"),
                {"s": schema, "i": index},
            ).scalar_one_or_none()
            assert definition is not None, f"{index} is missing"
            assert "gin (_search gin_trgm_ops)" in definition
        replaced = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname LIKE '%\\_spotlight\\_trgm'")
        ).scalars()
        assert list(replaced) == []


def test_contact_search_text_keeps_numbers_and_drops_phone_labels(
    super_engine: Engine, searchbed: dict[str, object]
) -> None:
    """List search matches ``_search`` with LIKE, so a number is in it and a label word is not.

    The fixture's other number is ``01179000456``. Folding a labelled list used to make a search
    for "work" return every contact with a phone.
    """
    with super_engine.connect() as conn:
        folded = conn.execute(
            text("SELECT _search FROM crm.contacts WHERE uid = :u"),
            {"u": searchbed["mine_uid"]},
        ).scalar_one()
    assert "01179000456" in folded
    assert "07700900123" in folded
    words = set(folded.split())
    assert words.isdisjoint({"label", "number", "work", "home", "landline"})


def test_the_audit_trail_records_the_edit_not_its_search_text(
    super_engine: Engine, searchbed: dict[str, object]
) -> None:
    """``_search`` follows a rename, and the audit diff logs the rename once — not again as the
    regenerated search text. Rolled back, so the shared bed keeps its names."""
    with super_engine.connect() as conn, conn.begin() as tx:
        contact_id = conn.execute(
            text("SELECT id FROM crm.contacts WHERE uid LIKE :u ORDER BY id LIMIT 1"),
            {"u": f"%{searchbed['sfx']}"},
        ).scalar_one()
        conn.execute(
            text("UPDATE crm.contacts SET surname = 'Renamedsurname' WHERE id = :id"),
            {"id": contact_id},
        )
        regenerated = conn.execute(
            text("SELECT _search FROM crm.contacts WHERE id = :id"), {"id": contact_id}
        ).scalar_one()
        changed = conn.execute(
            text(
                "SELECT detail -> 'changed' FROM audit.log "
                "WHERE entity_type = 'crm.contacts' AND entity_id = :id "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"id": contact_id},
        ).scalar_one()
        tx.rollback()
    assert "renamedsurname" in regenerated
    assert "surname" in changed
    assert "_search" not in changed


def test_the_fuzzy_predicate_is_served_by_the_search_index(super_engine: Engine) -> None:
    """The point of one column: search's predicate and the index are the same expression, so the
    planner can use the index. With sequential scans priced out, the plan must name it."""
    with super_engine.connect() as conn:
        # Session-level, not SET LOCAL: this engine may autocommit, and the connection is pooled.
        conn.execute(text("SET enable_seqscan = off"))
        try:
            plan = "\n".join(
                conn.execute(
                    text(
                        "EXPLAIN SELECT 1 FROM crm.contacts c "
                        "WHERE c.deleted_at IS NULL AND 'margret' <% c._search"
                    )
                ).scalars()
            )
        finally:
            conn.execute(text("RESET enable_seqscan"))
    assert "contacts_search_trgm" in plan


def test_pattern_neutralises_like_metacharacters() -> None:
    """The escaping contract the SQL's ``ESCAPE '\\'`` depends on."""
    assert search_router._pattern("a%b") == "%a\\%b%"
    assert search_router._pattern("a_b") == "%a\\_b%"
    assert search_router._pattern("a\\b") == "%a\\\\b%"


def test_router_is_mounted_and_not_shadowed() -> None:
    """Both endpoints are reachable over HTTP, on this module's handlers, and nothing matches
    their paths first.

    Every other test in this file awaits the handler coroutines directly, so they all stay green
    if ``app.include_router(search.router)`` goes missing from ``app/main.py`` — while all three
    portals' search bars 404. Starlette matches routes in *registration* order, so a dynamic
    sibling at the root (``/{uid}``) registered before this router would swallow ``/search``
    just as silently; the same applies to ``/search/{something}`` versus ``/search/tenants``.
    Both failure modes are asserted here rather than assumed.
    """
    from fastapi.routing import APIRoute
    from starlette.routing import Match

    from app.main import app

    def first_match(path: str) -> object | None:
        """The route Starlette would actually dispatch a GET of ``path`` to."""
        scope = {"type": "http", "method": "GET", "path": path, "root_path": ""}
        for route in api_routes(app):
            match, _ = route.matches(scope)
            if match is Match.FULL:
                return route
        return None

    mounted = first_match("/search")
    tenants = first_match("/search/tenants")
    assert isinstance(mounted, APIRoute), "GET /search resolves to no route -- router not included"
    assert isinstance(tenants, APIRoute), "GET /search/tenants resolves to no route"
    # Identity, not just the path: a same-path route from another module would be a different bug
    # with the same 404-free symptom.
    assert mounted.endpoint is search_router.search
    assert tenants.endpoint is search_router.search_tenants

    # The generated TS client keys off these operation ids; the portals' proxies key off the paths.
    paths = app.openapi()["paths"]
    assert paths["/search"]["get"]["operationId"] == "search-search"
    assert paths["/search/tenants"]["get"]["operationId"] == "search-search-tenants"
    scope_parameter = next(
        parameter
        for parameter in paths["/search"]["get"]["parameters"]
        if parameter["name"] == "scope"
    )
    scope_values = next(
        option["enum"] for option in scope_parameter["schema"]["anyOf"] if "enum" in option
    )
    assert scope_values == list(search_router._SEARCH_SCOPES)
