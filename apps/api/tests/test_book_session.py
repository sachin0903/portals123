"""The portal waterfall: Graphene operations reading — and acting on — a tenant's book.

``db.book_session`` / ``db.firm_book_session`` are the book READS' session dependencies and
``db.firm_book_write_session`` is the proxy WRITES'. Without ``?tenant=<uid>`` they delegate
verbatim to ``tenant_session`` / ``firm_session`` (the platform and client portals must not be
able to tell the difference); with it, an operations principal holding ``book:read`` reads the
TARGET firm's data with firm-staff-equivalent read visibility, and one holding ``book:write``
performs the firm-staff write against the target firm's book.

Driven over real HTTP (ASGI) against the real testcontainer Postgres so the whole chain is
exercised — query-param parsing, the operations gate, the claim minting and RLS itself. Only
``get_current_principal`` is overridden (a verified token is the one thing we cannot mint here).

The READ matrix, once, thoroughly (one representative GET per swapped router):

  * ops + ``book:read`` + ``?tenant=<uid>``  ⇒ the TARGET firm's rows, and only that firm's
    (never the sibling firm's, never the operations tenant's own);
  * ops without the permission                ⇒ 403;
  * unknown uid                               ⇒ 404 (after the gates — no existence oracle);
  * a FIRM staff principal passing the param  ⇒ 403, own tenant or foreign (operations-only);
  * a CLIENT principal passing it             ⇒ 403;
  * no param                                  ⇒ the pre-existing behaviour, pinned per router
    (delegation makes it identical by construction; the pin catches a future non-delegating
    rewrite) — and no param NEVER widens: an ops book-reader without the param sees the
    operations tenant's own data, not a firm's;
  * ``book:write`` implies ``book:read``; ``system`` bypasses (registry conventions);
  * a suspended firm stays readable to ops (suspension locks the firm out, not Graphene).

The WRITE matrix (the proxy middle-office actions — client details, bank details, model
mandate, instruction create/cancel):

  * ops + ``book:write`` + ``?tenant=<uid>``  ⇒ the write lands in the TARGET tenant, with the
    target's own domain gates intact (a sibling firm's model id is refused);
  * ops with only ``book:read``               ⇒ 403 on EVERY write — reads stay ``book:read``,
    writes demand ``book:write``, and the refusal happens at the session gate;
  * a FIRM staff principal passing the param  ⇒ 403;
  * unknown uid                               ⇒ 404 (after the gates);
  * no param                                  ⇒ the firm's own write path, pinned per endpoint —
    and no param never widens: ops naming no target cannot touch a firm's rows;
  * audit attribution: a foreign write's ``audit.log`` row names the ACTING OPERATOR
    (``user_id``) while carrying the TARGET tenant's ``tenant_id``.

Order-independent: unique ids per run, tenant-scoped assertions, never truncates.
"""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import date

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import db as db_mod
from app import rbac
from app.auth import Principal, get_current_principal
from app.config import settings
from app.contacts import contact_phone
from app.main import app
from app.permissions import BOOK_READ, BOOK_WRITE, satisfies
from tests.conftest import SECCL_PLATFORM_SQL


@pytest.fixture(autouse=True)
def _no_live_seccl_money_rail(monkeypatch: pytest.MonkeyPatch) -> None:
    """This suite POSTs instructions on Seccl-linked accounts via the firm router, which
    dispatches. A local ``SECCL_*`` must not fire a real group."""

    async def unconfigured(*_a: object, **_k: object) -> dict[str, object]:
        raise LookupError("Seccl is not configured.")

    monkeypatch.setattr("app.integrations.seccl.money._seccl_group", unconfigured)


# ---- seed ------------------------------------------------------------------------------------


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, str]:
    """The operations tenant (with its core-project environment row) + two firms, each carrying
    one recognisably-named row per swapped surface: a contact, a document, a task, a flow, a
    flow execution — and, for the proxy-write matrix, an active account, an investment model and
    a pending money-movement instruction. Marker strings are unique per run so assertions cannot
    collide with other suites' data."""
    s = uuid.uuid4().hex[:8]
    out: dict[str, str] = {"suffix": s, "core_client_id": f"client_core_{s}"}
    with super_engine.connect() as c:
        # One (global-catalogue) platform row shared by both firms' models.
        provider = str(
            c.execute(
                text(SECCL_PLATFORM_SQL),
                {"n": f"BookPlatform {s}"},
            ).scalar_one()
        )

        def mk_tenant(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"bkten_{tag}_{s}", "a": f"bkfirm-{tag}-{s}", "n": f"BookFirm {tag}"},
                ).scalar_one()
            )

        def mk_user(tenant: int, tag: str, portal: str = "platform") -> tuple[str, str]:
            wos = f"bkwos_{tag}_{s}"
            uid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                        "  first_name, last_name, enabled) "
                        "VALUES (:uid, :t, :email, :wos, :first, 'Book', true) RETURNING id"
                    ),
                    {
                        "uid": f"bkusr_{tag}_{s}",
                        "t": tenant,
                        "email": f"{tag}_{s}@bookfirm.test",
                        "wos": wos,
                        "first": tag.title(),
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, :p)"
                ),
                {"u": uid, "t": tenant, "p": portal},
            )
            return uid, wos

        def mk_book(tenant: int, tag: str, staff_uuid: str) -> dict[str, str]:
            """One row per swapped surface, named with this firm's marker. Returns the ids the
            proxy-write matrix acts on (contact/account/model + a pending instruction uid)."""
            contact_id = int(
                c.execute(
                    text(
                        "INSERT INTO crm.contacts "
                        "  (tenant_id, uid, type, name, surname, service_type) "
                        "VALUES (:t, :uid, 'individual', :n, :sn, 'advised') RETURNING id"
                    ),
                    {"t": tenant, "uid": f"bkcon_{tag}_{s}", "n": f"Contact-{tag}-{s}", "sn": tag},
                ).scalar_one()
            )
            file_id = str(
                c.execute(
                    text(
                        "INSERT INTO documents.files "
                        "  (tenant_id, bucket, storage_key, content_type, size_bytes, sha256) "
                        "VALUES (:t, 'b', :key, 'application/pdf', 10, 'abc') RETURNING id"
                    ),
                    {"t": tenant, "key": f"uploads/{tenant}/{tag}-{s}/original.pdf"},
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO documents.documents "
                    "  (uid, tenant_id, original_file_id, pdf_file_id, document_type, source, "
                    "   name, status) "
                    "VALUES (:uid, :t, CAST(:f AS uuid), CAST(:f AS uuid), 'other', 'uploaded', "
                    "        :n, 'generated')"
                ),
                {"uid": f"bkdoc_{tag}_{s}", "t": tenant, "f": file_id, "n": f"Doc-{tag}-{s}"},
            )
            c.execute(
                text(
                    "INSERT INTO tasks.tasks "
                    "  (uid, tenant_id, assignee_kind, assignee_user_id, title) "
                    "VALUES (:uid, :t, 'user', CAST(:u AS uuid), :title)"
                ),
                {
                    "uid": f"btsk_{tag}_{s}",
                    "t": tenant,
                    "u": staff_uuid,
                    "title": f"Task-{tag}-{s}",
                },
            )
            flow_id = int(
                c.execute(
                    text(
                        "INSERT INTO flows.flows (uid, tenant_id, code, name) "
                        "VALUES (:uid, :t, 'onboarding', :n) RETURNING id"
                    ),
                    {"uid": f"bflw_{tag}_{s}", "t": tenant, "n": f"Flow-{tag}-{s}"},
                ).scalar_one()
            )
            version_id = int(
                c.execute(
                    text(
                        "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version) "
                        "VALUES (:uid, :t, :f, 1) RETURNING id"
                    ),
                    {"uid": f"bflv_{tag}_{s}", "t": tenant, "f": flow_id},
                ).scalar_one()
            )
            # created_by is this firm's staffer: the listing is their book, not the whole firm's,
            # unless they hold contacts:read_all.
            c.execute(
                text(
                    "INSERT INTO flows.executions "
                    "  (uid, tenant_id, flow_id, flow_version_id, status, created_by) "
                    "VALUES (:uid, :t, :f, :v, 'active', CAST(:u AS uuid))"
                ),
                {
                    "uid": f"bfex_{tag}_{s}",
                    "t": tenant,
                    "f": flow_id,
                    "v": version_id,
                    "u": staff_uuid,
                },
            )
            # The proxy-write targets: a firm model, an ACTIVE account on the contact (so an
            # `expected_in` instruction — the one kind that needs no provider position — can be
            # raised against it), and a pending instruction to list and cancel.
            model_id = int(
                c.execute(
                    text(
                        "INSERT INTO platform.models (tenant_id, provider, name) "
                        "VALUES (:t, :p, :n) RETURNING id"
                    ),
                    {"t": tenant, "p": provider, "n": f"Model-{tag}-{s}"},
                ).scalar_one()
            )
            account_id = int(
                c.execute(
                    text(
                        "INSERT INTO platform.accounts "
                        "  (tenant_id, contact_id, uid, name, status, value) "
                        "VALUES (:t, :con, :uid, :n, 'active', 100000.00) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "con": contact_id,
                        "uid": f"bkacc_{tag}_{s}",
                        "n": f"Acct-{tag}-{s}",
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO platform.instructions "
                    "  (uid, tenant_id, account_id, contact_id, kind, amount) "
                    "VALUES (:uid, :t, :a, :c, 'expected_in', 100.00)"
                ),
                {"uid": f"bins_{tag}_{s}", "t": tenant, "a": account_id, "c": contact_id},
            )
            return {
                "contact": str(contact_id),
                "contact_uid": f"bkcon_{tag}_{s}",
                "account": str(account_id),
                "account_uid": f"bkacc_{tag}_{s}",
                "model": str(model_id),
                "ins": f"bins_{tag}_{s}",
            }

        # The operations tenant + its core-project environment (what operations_tenant_id
        # resolves through) — the client fixture points settings.workos_client_id at it. The
        # operations APPLICATION id + membership are what classify the ops sessions' portal (0107).
        ops_tenant = mk_tenant("ops")
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, workos_client_id, operations_client_id) "
                "VALUES (:t, 'production', :cid, :app)"
            ),
            {"t": ops_tenant, "cid": out["core_client_id"], "app": f"app_ops_{s}"},
        )
        ops_uuid, ops_wos = mk_user(ops_tenant, "ops", portal="operations")
        # The operations tenant carries a contact of its own so a foreign read can prove it
        # returns the TARGET's rows, not the caller's.
        c.execute(
            text(
                "INSERT INTO crm.contacts (tenant_id, uid, type, name, service_type) "
                "VALUES (:t, :uid, 'individual', :n, 'advised')"
            ),
            {"t": ops_tenant, "uid": f"bkcon_ops_{s}", "n": f"Contact-ops-{s}"},
        )

        firm_a = mk_tenant("alpha")
        # Firm alpha's portal applications — staff_a/client_a sessions classify against these.
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments "
                "  (tenant_id, stage, workos_client_id, client_portal_client_id, "
                "   platform_client_id) "
                "VALUES (:t, 'production', :cid, :capp, :papp)"
            ),
            {
                "t": firm_a,
                "cid": f"client_alpha_{s}",
                "capp": f"app_client_alpha_{s}",
                "papp": f"app_platform_alpha_{s}",
            },
        )
        staff_a_uuid, staff_a_wos = mk_user(firm_a, "alphastaff")
        _client_a_uuid, client_a_wos = mk_user(firm_a, "alphaclient", portal="client")
        book_a = mk_book(firm_a, "alpha", staff_a_uuid)

        firm_b = mk_tenant("beta")
        staff_b_uuid, _staff_b_wos = mk_user(firm_b, "betastaff")
        book_b = mk_book(firm_b, "beta", staff_b_uuid)

        c.commit()

    out.update(
        ops_wos=ops_wos,
        ops_org=f"org_ops_{s}",
        ops_uuid=ops_uuid,
        staff_a_wos=staff_a_wos,
        client_a_wos=client_a_wos,
        firm_a_uid=f"bkten_alpha_{s}",
        firm_b_uid=f"bkten_beta_{s}",
        firm_a_id=str(firm_a),
        firm_b_id=str(firm_b),
        # The proxy-write targets, path-template keys ({contact_a} etc. in SURFACES/WRITES).
        contact_a=book_a["contact"],
        contact_a_uid=book_a["contact_uid"],
        account_a_uid=book_a["account_uid"],
        account_a=book_a["account"],
        model_a=book_a["model"],
        ins_a=book_a["ins"],
        contact_b=book_b["contact"],
        contact_b_uid=book_b["contact_uid"],
        account_b=book_b["account"],
        model_b=book_b["model"],
        ins_b=book_b["ins"],
    )
    return out


# ---- harness ---------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(
    db: dict[str, str], seed: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """The real HTTP app against the container DB. `workos_client_id` is pointed at the seeded
    core environment so `environments.operations_tenant_id` resolves the seeded ops tenant."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    monkeypatch.setattr(settings, "workos_client_id", seed["core_client_id"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


ActAs = Callable[..., None]


@pytest.fixture
def act(seed: dict[str, str]) -> AsyncIterator[ActAs]:
    """Install a principal for subsequent requests. A FRESH Principal per request (the lambda
    re-constructs it), mirroring production where get_current_principal builds one per request —
    so the book overlay's in-place permission grant can never bleed across requests here either.
    Each persona carries the `client_id` of the APPLICATION its portal signs in through (0107:
    the portal is classified from it + the active membership, never from role/org).
    """

    def _as(who: str, perms: list[str], role: str = "adviser") -> None:
        s = seed["suffix"]
        wos = {
            "ops": seed["ops_wos"],
            "staff_a": seed["staff_a_wos"],
            "client_a": seed["client_a_wos"],
        }[who]
        client_id = {
            "ops": f"app_ops_{s}",
            "staff_a": f"app_platform_alpha_{s}",
            "client_a": f"app_client_alpha_{s}",
        }[who]
        org = seed["ops_org"] if who == "ops" else f"org_firm_{s}"
        app.dependency_overrides[get_current_principal] = lambda: Principal(
            user_id=wos,
            organisation_id=org,
            role=role,
            permissions=list(perms),
            client_id=client_id,
        )

    yield _as
    app.dependency_overrides.pop(get_current_principal, None)


def _marker(seed: dict[str, str], surface: str, tag: str) -> str:
    return {
        "contacts": f"Contact-{tag}-{seed['suffix']}",
        "documents": f"Doc-{tag}-{seed['suffix']}",
        "tasks": f"Task-{tag}-{seed['suffix']}",
        "flows": f"Flow-{tag}-{seed['suffix']}",
        "applications": f"bfex_{tag}_{seed['suffix']}",
        "instructions": f"bins_{tag}_{seed['suffix']}",
        "models": f"Model-{tag}-{seed['suffix']}",
    }[surface]


# One representative GET per swapped router (+ /documents/clients, the one write-gated read the
# overlay must satisfy; + the two supporting reads the proxy-action forms need — the
# instructions list (instructions:read since the 0042 split) and the model catalogue). Paths are
# str.format templates over the seed (``{contact_a}``).
# (path, surface, the platform-read slugs a firm staffer would need.)
SURFACES: list[tuple[str, str, list[str]]] = [
    ("/crm/contacts", "contacts", ["contacts:read_all"]),
    ("/documents", "documents", []),
    ("/documents/clients", "contacts", ["documents:write", "contacts:read_all"]),
    ("/tasks", "tasks", ["tasks:read"]),
    ("/flows", "flows", ["flows:read"]),
    ("/applications", "applications", ["cases:read"]),
    ("/firm/contacts/{contact_a_uid}/instructions", "instructions", ["instructions:read"]),
    ("/catalog/models", "models", ["contacts:read"]),
]


# The proxy middle-office WRITE surface — every endpoint wired to `firm_book_write_session`.
# (method, path template, body builder). The body builder takes the seed so ids resolve at call
# time; every row targets firm ALPHA's book.
def _phone(suffix: str) -> str:
    """A number the contact validator accepts. The seed suffix is hex, so its letters are dropped:
    a phone is digits (or E.164) and nothing else since UAT-56/69."""
    digits = "".join(ch for ch in suffix if ch.isdigit())
    return f"0161496{digits[:4]:0<4}"


Write = tuple[str, str, Callable[[dict[str, str]], dict[str, object] | None]]
WRITES: list[Write] = [
    ("PATCH", "/firm/contacts/{contact_a_uid}", lambda s: {"mobile": _phone(s["suffix"])}),
    (
        "PUT",
        "/crm/contacts/{contact_a_uid}/bank-account",
        lambda s: {
            "name": "Book Holder",
            "bank": "Test Bank",
            "sort_code": "12-34-56",
            "account_number": "12345678",
        },
    ),
    ("PUT", "/crm/accounts/{account_a_uid}/model", lambda s: {"model_id": int(s["model_a"])}),
    (
        "POST",
        "/firm/contacts/{contact_a_uid}/instructions",
        lambda s: {
            "kind": "expected_in",
            "account_id": int(s["account_a"]),
            "amount": 250.0,
            "due_date": date.today().isoformat(),
        },
    ),
    ("POST", "/firm/instructions/{ins_a}/cancel", lambda s: None),
]


async def _get(
    client: AsyncClient, path: str, seed: dict[str, str] | None = None, tenant: str | None = None
) -> Response:
    if seed is not None:
        path = path.format(**seed)
    return await client.get(path, params={"tenant": tenant} if tenant is not None else None)


async def _write(
    client: AsyncClient,
    method: str,
    path: str,
    body: dict[str, object] | None,
    seed: dict[str, str],
    tenant: str | None = None,
) -> Response:
    return await client.request(
        method,
        path.format(**seed),
        json=body,
        params={"tenant": tenant} if tenant is not None else None,
    )


# ---- the matrix ------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "surface", "_perms"), SURFACES)
async def test_ops_with_book_read_sees_the_target_firms_rows_and_only_those(
    client: AsyncClient, act: ActAs, seed: dict[str, str], path: str, surface: str, _perms: object
) -> None:
    """The whole point: `book:read` + `?tenant=` reads the TARGET firm's book — including the
    handlers' own permission gates (tasks:read / flows:read / cases:read / documents:write on
    the clients pick-list), which the visibility overlay satisfies — and RLS still fences the
    rows to that one firm."""
    act("ops", ["book:read"], role="operations")
    resp = await _get(client, path, seed=seed, tenant=seed["firm_a_uid"])
    assert resp.status_code == 200, (path, resp.status_code, resp.text)
    assert _marker(seed, surface, "alpha") in resp.text, (path, resp.text)
    assert _marker(seed, surface, "beta") not in resp.text, path


@pytest.mark.asyncio
async def test_a_foreign_read_carries_the_targets_rows_not_the_ops_tenants(
    client: AsyncClient, act: ActAs, seed: dict[str, str]
) -> None:
    """RLS spot-check: the minted claims belong to the TARGET tenant, so the operations tenant's
    own data (the caller's home firm!) is exactly as invisible as any third firm's."""
    act("ops", ["book:read"], role="operations")
    resp = await _get(client, "/crm/contacts", tenant=seed["firm_a_uid"])
    assert resp.status_code == 200
    assert _marker(seed, "contacts", "alpha") in resp.text
    assert _marker(seed, "contacts", "ops") not in resp.text
    assert _marker(seed, "contacts", "beta") not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/crm/contacts", "/applications"])  # both session flavours
async def test_ops_without_book_read_is_refused(
    client: AsyncClient, act: ActAs, seed: dict[str, str], path: str
) -> None:
    """Operations scope alone is not enough — the param needs the `book:read` grant (other ops
    permissions, tenants:read included, do not imply it)."""
    act("ops", ["tenants:read"], role="operations")
    resp = await _get(client, path, tenant=seed["firm_a_uid"])
    assert resp.status_code == 403, (path, resp.status_code, resp.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/crm/contacts", "/applications"])  # both session flavours
async def test_unknown_tenant_uid_is_404(
    client: AsyncClient, act: ActAs, seed: dict[str, str], path: str
) -> None:
    act("ops", ["book:read"], role="operations")
    resp = await _get(client, path, tenant=f"no_such_{seed['suffix']}")
    assert resp.status_code == 404, (path, resp.status_code, resp.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/crm/contacts", "/applications"])  # both session flavours
@pytest.mark.parametrize("target", ["firm_a_uid", "firm_b_uid"])  # own AND foreign
async def test_firm_staff_cannot_use_the_param_even_for_their_own_tenant(
    client: AsyncClient, act: ActAs, seed: dict[str, str], path: str, target: str
) -> None:
    """`?tenant=` is operations-only. A firm staffer naming ANY tenant — their own included, and
    even carrying a stray `book:read` in the token — is refused: the coarse gate is the resolved
    operations tenant, not a permission a firm could ever be granted."""
    act("staff_a", ["book:read", "contacts:read_all", "cases:read"])
    resp = await _get(client, path, tenant=seed[target])
    assert resp.status_code == 403, (path, target, resp.status_code, resp.text)


@pytest.mark.asyncio
async def test_a_client_principal_cannot_use_the_param(
    client: AsyncClient, act: ActAs, seed: dict[str, str]
) -> None:
    act("client_a", [], role="client")
    resp = await _get(client, "/crm/contacts", tenant=seed["firm_a_uid"])
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_book_write_implies_book_read_and_system_bypasses(
    client: AsyncClient, act: ActAs, seed: dict[str, str]
) -> None:
    """Registry conventions hold for the new pair: write ⇒ read, and `system` needs neither."""
    act("ops", ["book:write"], role="operations")
    resp = await _get(client, "/crm/contacts", tenant=seed["firm_a_uid"])
    assert resp.status_code == 200
    act("ops", ["system"], role="operations")
    resp = await _get(client, "/crm/contacts", tenant=seed["firm_a_uid"])
    assert resp.status_code == 200
    assert _marker(seed, "contacts", "alpha") in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "surface", "perms"), SURFACES)
async def test_no_param_keeps_the_pre_existing_firm_staff_behaviour(
    client: AsyncClient, act: ActAs, seed: dict[str, str], path: str, surface: str, perms: list
) -> None:
    """The regression bar: without `?tenant=` the dependency IS tenant_session / firm_session
    (delegation), so a firm staffer sees their own firm and nothing else — pinned per router so
    a future non-delegating rewrite cannot drift."""
    act("staff_a", list(perms))
    resp = await _get(client, path, seed=seed)
    assert resp.status_code == 200, (path, resp.status_code, resp.text)
    assert _marker(seed, surface, "alpha") in resp.text, (path, resp.text)
    assert _marker(seed, surface, "beta") not in resp.text, path


@pytest.mark.asyncio
async def test_no_param_never_widens_an_ops_principal(
    client: AsyncClient, act: ActAs, seed: dict[str, str]
) -> None:
    """An ops book-reader WITHOUT the param goes down the plain tenant_session path: their own
    (operations) tenant's data, no firm's — holding `book:read` changes nothing until a target
    is named."""
    act("ops", ["book:read", "contacts:read_all"], role="operations")
    resp = await _get(client, "/crm/contacts")
    assert resp.status_code == 200
    assert _marker(seed, "contacts", "ops") in resp.text
    assert _marker(seed, "contacts", "alpha") not in resp.text
    assert _marker(seed, "contacts", "beta") not in resp.text


@pytest.mark.asyncio
async def test_a_suspended_firm_stays_readable_to_ops(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """The foreign path deliberately skips _refuse_if_suspended (the admin_session reasoning):
    suspension locks the FIRM out; Graphene keeps sight of the book it is custodian of."""
    with super_engine.connect() as c:
        c.execute(
            text("UPDATE auth.tenants SET enabled = false WHERE uid = :u"),
            {"u": seed["firm_b_uid"]},
        )
        c.commit()
    act("ops", ["book:read"], role="operations")
    resp = await _get(client, "/crm/contacts", tenant=seed["firm_b_uid"])
    assert resp.status_code == 200
    assert _marker(seed, "contacts", "beta") in resp.text


# ---- the write matrix (proxy middle-office actions) -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_ops_with_book_write_performs_each_proxy_write(
    client: AsyncClient,
    act: ActAs,
    seed: dict[str, str],
    method: str,
    path: str,
    body: Callable[[dict[str, str]], dict[str, object] | None],
) -> None:
    """The whole point of `book:write`: every named proxy action succeeds against the TARGET
    firm's book — the handlers' own gates (`contacts:write` on the client-record PATCH,
    `instructions:write` on the money endpoints since the 0042 split) are satisfied by the
    firm-context overlay, and the tenant-scoped service layer acts on the target's rows."""
    act("ops", ["book:write"], role="operations")
    resp = await _write(client, method, path, body(seed), seed, tenant=seed["firm_a_uid"])
    assert resp.status_code == 200, (method, path, resp.status_code, resp.text)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_ops_with_only_book_read_is_refused_on_every_write(
    client: AsyncClient,
    act: ActAs,
    seed: dict[str, str],
    method: str,
    path: str,
    body: Callable[[dict[str, str]], dict[str, object] | None],
) -> None:
    """Reads stay `book:read`; WRITES demand `book:write` — and the refusal is the session gate
    (`_resolve_book_target`), which fires before any visibility overlay exists, so the overlay's
    `contacts:write` can never carry a book reader into a write. The 403 names the missing book
    permission, pinning WHICH gate refused."""
    act("ops", ["book:read"], role="operations")
    resp = await _write(client, method, path, body(seed), seed, tenant=seed["firm_a_uid"])
    assert resp.status_code == 403, (method, path, resp.status_code, resp.text)
    assert "book:write" in resp.text, (method, path, resp.text)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_firm_staff_cannot_use_the_param_on_writes(
    client: AsyncClient,
    act: ActAs,
    seed: dict[str, str],
    method: str,
    path: str,
    body: Callable[[dict[str, str]], dict[str, object] | None],
) -> None:
    """`?tenant=` stays operations-only on the write surface too — a firm staffer naming their
    OWN tenant, even with a stray `book:write` in the token, is refused at the coarse gate."""
    act("staff_a", ["book:write", "contacts:write"])
    resp = await _write(client, method, path, body(seed), seed, tenant=seed["firm_a_uid"])
    assert resp.status_code == 403, (method, path, resp.status_code, resp.text)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_no_param_keeps_the_firm_staff_write_behaviour(
    client: AsyncClient,
    act: ActAs,
    seed: dict[str, str],
    method: str,
    path: str,
    body: Callable[[dict[str, str]], dict[str, object] | None],
) -> None:
    """The regression bar for the swap: without `?tenant=` the dependency IS `firm_session`
    (delegation), so the platform portal's own write path is byte-identical — pinned per
    endpoint so a future non-delegating rewrite cannot drift."""
    # contacts:write for the client-record PATCH, instructions:write for the four money
    # endpoints — the post-0042 shape of a firm's writing staff (the backfill grants both).
    act("staff_a", ["contacts:write", "instructions:write"])
    resp = await _write(client, method, path, body(seed), seed)
    assert resp.status_code == 200, (method, path, resp.status_code, resp.text)


@pytest.mark.asyncio
async def test_no_param_never_widens_an_ops_writer(
    client: AsyncClient, act: ActAs, seed: dict[str, str]
) -> None:
    """An ops book-writer WITHOUT the param goes down the plain `firm_session` path: their own
    (operations) tenant, where firm alpha's contact does not exist — 404, no write."""
    act("ops", ["book:write", "contacts:write"], role="operations")
    body = {"mobile": _phone(seed["suffix"])}
    resp = await _write(client, "PATCH", "/firm/contacts/{contact_a_uid}", body, seed)
    assert resp.status_code == 404, (resp.status_code, resp.text)


@pytest.mark.asyncio
async def test_unknown_tenant_uid_is_404_on_a_write(
    client: AsyncClient, act: ActAs, seed: dict[str, str]
) -> None:
    act("ops", ["book:write"], role="operations")
    resp = await _write(
        client,
        "PATCH",
        "/firm/contacts/{contact_a_uid}",
        {"mobile": _phone(seed["suffix"])},
        seed,
        tenant=f"no_such_{seed['suffix']}",
    )
    assert resp.status_code == 404, (resp.status_code, resp.text)


@pytest.mark.asyncio
async def test_a_proxied_client_edit_lands_in_the_target_firms_row(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    act("ops", ["book:write"], role="operations")
    phone = _phone(seed["suffix"])
    resp = await _write(
        client,
        "PATCH",
        "/firm/contacts/{contact_a_uid}",
        {"mobile": phone},
        seed,
        tenant=seed["firm_a_uid"],
    )
    assert resp.status_code == 200, resp.text
    with super_engine.connect() as c:
        row = c.execute(
            text("SELECT mobile, tenant_id FROM crm.contacts WHERE id = :c"),
            {"c": int(seed["contact_a"])},
        ).one()
    assert row.mobile == contact_phone(phone)
    assert row.tenant_id == int(seed["firm_a_id"])


@pytest.mark.asyncio
async def test_a_proxied_bank_update_lands_normalised(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """The bank write goes through the same single validated door as the firm's own — the stored
    details are the canonicalised ones, in the TARGET firm's row."""
    act("ops", ["book:write"], role="operations")
    resp = await _write(
        client,
        "PUT",
        "/crm/contacts/{contact_a_uid}/bank-account",
        {"name": "Book Holder", "sort_code": "123456", "account_number": "12345678"},
        seed,
        tenant=seed["firm_a_uid"],
    )
    assert resp.status_code == 200, resp.text
    with super_engine.connect() as c:
        row = c.execute(
            text("SELECT bank_account, tenant_id FROM crm.contacts WHERE id = :c"),
            {"c": int(seed["contact_a"])},
        ).one()
    assert row.bank_account == {
        "name": "Book Holder",
        "sort_code": "12-34-56",
        "account_number": "12345678",
    }
    assert row.tenant_id == int(seed["firm_a_id"])


@pytest.mark.asyncio
async def test_a_proxied_model_change_runs_the_targets_ownership_gate(
    client: AsyncClient, act: ActAs, seed: dict[str, str]
) -> None:
    """The handlers' own domain validation runs against the MINTED (target) tenant: the target
    firm's model is accepted, the sibling firm's is refused with the same 400 the firm's own
    staff would get — `book:write` does not soften a single domain gate."""
    act("ops", ["book:write"], role="operations")
    ok = await _write(
        client,
        "PUT",
        "/crm/accounts/{account_a_uid}/model",
        {"model_id": int(seed["model_a"])},
        seed,
        tenant=seed["firm_a_uid"],
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["discretionary"] is True
    foreign = await _write(
        client,
        "PUT",
        "/crm/accounts/{account_a_uid}/model",
        {"model_id": int(seed["model_b"])},
        seed,
        tenant=seed["firm_a_uid"],
    )
    assert foreign.status_code == 400, (foreign.status_code, foreign.text)


@pytest.mark.asyncio
async def test_a_proxied_instruction_lands_in_the_target_tenant_and_cancels(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """Create-then-cancel end to end on the foreign path: the row lands in the TARGET tenant
    (never the operations tenant), and `created_by` is NULL by design — the operator has no
    auth.users row in the target firm; their identity lives on the audit trail (next test)."""
    act("ops", ["book:write"], role="operations")
    resp = await _write(
        client,
        "POST",
        "/firm/contacts/{contact_a_uid}/instructions",
        {"kind": "expected_in", "account_id": int(seed["account_a"]), "amount": 250.0},
        seed,
        tenant=seed["firm_a_uid"],
    )
    assert resp.status_code == 200, resp.text
    uid = resp.json()["uid"]
    with super_engine.connect() as c:
        row = c.execute(
            text("SELECT tenant_id, status, created_by FROM platform.instructions WHERE uid = :u"),
            {"u": uid},
        ).one()
    assert row.tenant_id == int(seed["firm_a_id"])
    assert row.status == "pending"
    assert row.created_by is None

    cancel = await _write(
        client, "POST", f"/firm/instructions/{uid}/cancel", None, seed, tenant=seed["firm_a_uid"]
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json() == {"ok": True}
    with super_engine.connect() as c:
        status_now = c.execute(
            text("SELECT status FROM platform.instructions WHERE uid = :u"), {"u": uid}
        ).scalar_one()
    assert status_now == "cancelled"


@pytest.mark.asyncio
async def test_a_foreign_write_is_audited_as_the_acting_operator(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """Attribution is non-negotiable on the proxy path: the published claims stay the OPERATOR's
    own, so the DML audit trigger stamps the real person (`user_id`) while the affected row's
    tenant keeps the event in the TARGET firm's trail — same event, same shape, as a firm-staff
    write, different named actor."""
    act("ops", ["book:write"], role="operations")
    resp = await _write(
        client,
        "POST",
        "/firm/contacts/{contact_a_uid}/instructions",
        {"kind": "expected_in", "account_id": int(seed["account_a"]), "amount": 99.0},
        seed,
        tenant=seed["firm_a_uid"],
    )
    assert resp.status_code == 200, resp.text
    uid = resp.json()["uid"]
    with super_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT user_id::text AS user_id, tenant_id FROM audit.log "
                "WHERE action = 'platform.instructions.insert' "
                "  AND detail -> 'row' ->> 'uid' = :u"
            ),
            {"u": uid},
        ).one()
    assert row.user_id == seed["ops_uuid"]
    assert row.tenant_id == int(seed["firm_a_id"])


@pytest.mark.asyncio
async def test_a_custodian_refusal_keeps_the_booking_and_says_so(
    client: AsyncClient,
    act: ActAs,
    seed: dict[str, str],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ad-hoc fee whose custodian step raises (SEI slow, unconfigured, or refusing) used to
    escape the router as a 500 — which rolled the instruction AND its failed run back, so after
    the wait the adviser was told "Could not raise the instruction" and had nothing to retry.
    On baton the door books the instruction and starts the run in the same transaction; the
    worker advances it, and a refusal parks the run NeedsAttention with the custodian's reason.
    The booking must stand, and the run must say why."""
    from app.actions import service
    from app.actions.attempt import OperationError
    from app.lake import holdings as lake_holdings

    async def refused(*_a: object, **_k: object) -> object:
        raise OperationError("SEI could not accept this request.", permanent=True)

    # A fee is a draw on cash, so the funding gate needs a position to check it against.
    async def cash(*_a: object, **_k: object) -> list[dict[str, object]]:
        return [{"asset_name": "Cash", "asset_type": "Cash", "value": 500.0, "as_of": "2026-06-30"}]

    monkeypatch.setattr(lake_holdings, "firm_holdings", cash)
    # And a custodian to send to: unlinked, there is no provider leaf to refuse.
    with super_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO platform.links "
                "  (tenant_id, entity_type, entity_id, provider, external_id) "
                "VALUES (:t, 'account', :a, 'seccl', :ext) ON CONFLICT DO NOTHING"
            ),
            {
                "t": int(seed["firm_a_id"]),
                "a": int(seed["account_a"]),
                "ext": f"SECCL_{seed['suffix']}",
            },
        )

    # The `seccl.fee` leaf imports its handler at call time, so patching the module attribute
    # swaps the custodian call for one that refuses.
    monkeypatch.setattr("app.integrations.seccl.actions.seccl_adhoc_fee", refused)

    act("staff_a", ["contacts:write", "instructions:write"])
    resp = await _write(
        client,
        "POST",
        "/firm/contacts/{contact_a_uid}/instructions",
        {"kind": "adhoc_fee", "account_id": int(seed["account_a"]), "amount": 25.0},
        seed,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    with super_engine.connect() as c:
        status_now = c.execute(
            text("SELECT status FROM platform.instructions WHERE uid = :u"), {"u": body["uid"]}
        ).scalar_one()
        run_uid = c.execute(
            text("SELECT uid FROM baton.runs WHERE idempotency_key = :k AND tenant_id = :t"),
            {"k": f"instruction:{body['uid']}", "t": int(seed["firm_a_id"])},
        ).scalar_one()
    # The booking stands, with its run beside it.
    assert status_now == "pending"

    # Advance the run as the worker would: the refusal parks it for a person, with the reason.
    advanced = await service.advance_inline(run_uid, budget_seconds=30)
    assert advanced.run.state.name == "NeedsAttention", advanced.run.state
    with super_engine.connect() as c:
        reason = c.execute(
            text(
                "SELECT state_message FROM baton.runs "
                "WHERE action_key = 'seccl.fee' AND root_run_id = "
                "  (SELECT id FROM baton.runs WHERE uid = :u)"
            ),
            {"u": run_uid},
        ).scalar_one()
        status_after = c.execute(
            text("SELECT status FROM platform.instructions WHERE uid = :u"), {"u": body["uid"]}
        ).scalar_one()
    assert reason == "SEI could not accept this request."
    assert status_after == "pending"


# ---- registry --------------------------------------------------------------------------------


def test_book_permissions_are_operations_only() -> None:
    """The separation is a property of the declaration (the aml:adjudicate pattern): both slugs
    land in OPERATIONS_ONLY, so assert_grantable refuses them to every firm-scoped role."""
    assert BOOK_READ.slug in rbac.OPERATIONS_ONLY
    assert BOOK_WRITE.slug in rbac.OPERATIONS_ONLY
    assert satisfies({BOOK_WRITE.slug}, BOOK_READ.slug)  # write implies read
    assert not satisfies({BOOK_READ.slug}, BOOK_WRITE.slug)
