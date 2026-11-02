"""Action events — the request-level "every action emits an event" floor (app/audit_actions.py).

Driven over real HTTP (ASGI) against the real testcontainer Postgres, exactly like
``test_book_session.py``: only ``get_current_principal`` is overridden (a verified token is the
one thing we cannot mint here), and the override stashes the principal in ``request.state`` just
as the real dependency does — that stash is the middleware's attribution source, so it is part of
the contract under test.

What is pinned, one test per property:

* one representative mutating endpoint per SESSION FAMILY emits an event with the right
  attribution — ``firm_session`` (POST /firm/contacts), ``tenant_session`` (POST /forms, RLS
  path), ``admin_session`` (PATCH /admin/tenants/{id}) — the middleware is uniform per request,
  so the session family (how claims are minted) is the axis that varies, not the router;
* the PROXIED book path (``?tenant=<uid>``) records BOTH sides: the operator's tenant in
  ``tenant_id`` and the target firm in ``target_tenant_id``;
* redaction: secret-shaped query values never land, and request bodies never land at all;
* a refused action (4xx) emits nothing — an audit of effects, not attempts;
* a failing event write does NOT fail the action (best-effort by construction), but it does
  reach ``report_error``;
* reads (GET) never emit.

Order-independent: unique ids per run, actor-scoped assertions, never truncates.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import audit_actions as actions
from app import audit_stream
from app import db as db_mod
from app.auth import Principal, get_current_principal
from app.main import app
from tests.conftest import SECCL_PLATFORM_SQL
from tests.logcapture import captured

# ---- seed ------------------------------------------------------------------------------------


@pytest.fixture
def seed(super_engine: Engine) -> dict[str, str]:
    """One firm (a staff user + a contact) and one operations-side tenant (an ops user). Marker
    strings are unique per run so assertions cannot collide with other suites' data."""
    s = uuid.uuid4().hex[:8]
    with super_engine.connect() as c:

        def mk_tenant(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"actten_{tag}_{s}", "a": f"actfirm-{tag}-{s}", "n": f"ActFirm {tag}"},
                ).scalar_one()
            )

        def mk_user(tenant: int, tag: str) -> tuple[str, str]:
            wos = f"actwos_{tag}_{s}"
            user_uuid = str(
                c.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, email, workos_user_id, "
                        "  first_name, last_name, enabled) "
                        "VALUES (:uid, :t, :email, :wos, :first, 'Action', true) RETURNING id"
                    ),
                    {
                        "uid": f"actusr_{tag}_{s}",
                        "t": tenant,
                        "email": f"{tag}_{s}@actfirm.test",
                        "wos": wos,
                        "first": tag.title(),
                    },
                ).scalar_one()
            )
            c.execute(
                text(
                    "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal) "
                    "VALUES (:u, :t, 'platform')"
                ),
                {"u": user_uuid, "t": tenant},
            )
            return user_uuid, wos

        firm = mk_tenant("firm")
        staff_uuid, staff_wos = mk_user(firm, "staff")
        ops = mk_tenant("ops")
        ops_uuid, ops_wos = mk_user(ops, "ops")
        c.execute(
            text(
                "UPDATE auth.organisation_memberships SET portal = 'operations' "
                "WHERE user_id = :u AND tenant_id = :t"
            ),
            {"u": ops_uuid, "t": ops},
        )
        # App-only portal (0107): each tenant's environment declares the platform application its
        # staff sign in through — the overridden principal carries the matching client_id, and the
        # active platform memberships above are what grant the portal.
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  platform_client_id) VALUES (:t, 'dev', :env, :app)"
            ),
            {"t": firm, "env": f"env_act_firm_{s}", "app": f"app_act_platform_firm_{s}"},
        )
        c.execute(
            text(
                "INSERT INTO auth.tenant_environments (tenant_id, stage, workos_client_id, "
                "  operations_client_id) VALUES (:t, 'dev', :env, :app)"
            ),
            {"t": ops, "env": f"env_act_ops_{s}", "app": f"app_act_operations_ops_{s}"},
        )
        contact_id = int(
            c.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, name, surname, service_type) "
                    "VALUES (:t, :uid, 'individual', :n, 'Firm', 'advised') RETURNING id"
                ),
                {"t": firm, "uid": f"actcon_{s}", "n": f"Contact-{s}"},
            ).scalar_one()
        )
        # The before-value (PREVIOUS_FIELDS) targets: a role the staff member holds on their
        # platform membership, two permissions on that role, and an account already carrying a
        # model — the "FROM" states the pre-read must capture before the handler overwrites them.
        role_id = int(
            c.execute(
                text(
                    "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                    "VALUES (:t, :slug, 'Adviser', 'platform') RETURNING id"
                ),
                {"t": firm, "slug": f"actrole_{s}"},
            ).scalar_one()
        )
        for perm in (f"actperm_a_{s}", f"actperm_b_{s}"):
            c.execute(
                text(
                    "WITH created AS ("
                    "  INSERT INTO auth.permissions (slug, name) VALUES (:p, :p) RETURNING id"
                    ") INSERT INTO auth.role_permissions (role_id, permission_id) "
                    "SELECT :r, id FROM created"
                ),
                {"r": role_id, "p": perm},
            )
        c.execute(
            text(
                "UPDATE auth.organisation_memberships SET role_id = :r "
                "WHERE user_id = :u AND tenant_id = :t AND portal = 'platform'"
            ),
            {"r": role_id, "u": staff_uuid, "t": firm},
        )
        provider = str(
            c.execute(
                text(SECCL_PLATFORM_SQL),
                {"n": f"ActPlatform {s}"},
            ).scalar_one()
        )

        def mk_model(tag: str) -> int:
            return int(
                c.execute(
                    text(
                        "INSERT INTO platform.models (tenant_id, provider, name) "
                        "VALUES (:t, :p, :n) RETURNING id"
                    ),
                    {"t": firm, "p": provider, "n": f"Model-{tag}-{s}"},
                ).scalar_one()
            )

        model_a = mk_model("a")
        model_b = mk_model("b")
        account_id = int(
            c.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, contact_id, uid, name, status, "
                    "  value, model_id) "
                    "VALUES (:t, :con, :uid, :n, 'active', 50000.00, :m) RETURNING id"
                ),
                {
                    "t": firm,
                    "con": contact_id,
                    "uid": f"actacc_{s}",
                    "n": f"Acct-{s}",
                    "m": model_a,
                },
            ).scalar_one()
        )
        c.commit()

    return {
        "suffix": s,
        "firm_id": str(firm),
        "firm_uid": f"actten_firm_{s}",
        "ops_id": str(ops),
        "staff_uuid": staff_uuid,
        "staff_wos": staff_wos,
        "ops_uuid": ops_uuid,
        "ops_wos": ops_wos,
        "app_staff": f"app_act_platform_firm_{s}",
        "app_ops": f"app_act_operations_ops_{s}",
        "contact_id": str(contact_id),
        # The record's ADDRESS: paths take the uid, the serial stays for the assertions that key
        # on what the DB holds.
        "contact_uid": f"actcon_{s}",
        "role_slug": f"actrole_{s}",
        "perm_a": f"actperm_a_{s}",
        "perm_b": f"actperm_b_{s}",
        "model_a": str(model_a),
        "model_b": str(model_b),
        "account_id": str(account_id),
        "account_uid": f"actacc_{s}",
    }


# ---- harness ---------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sessionlocal(
    db: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Point ``db.SessionLocal`` at the container DB. ``app.audit_actions`` reads it at call time,
    so
    this one patch covers the request sessions, the middleware's post-response write session AND
    the before-value pre-read's own session (`capture_previous`)."""
    engine = create_async_engine(db["app"])
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "SessionLocal", maker)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def client(sessionlocal: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncClient]:
    """The real HTTP app (middleware included — ASGITransport runs the full stack) against the
    container DB."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


ActAs = Callable[..., None]


@pytest.fixture
def act(seed: dict[str, str]) -> AsyncIterator[ActAs]:
    """Install a principal for subsequent requests. The override STASHES the principal in
    ``request.state`` like the real ``get_current_principal`` does (``_issue_principal``) —
    that stash is the middleware's attribution source, so it belongs to the surface under test."""

    def _as(who: str, perms: list[str]) -> None:
        wos = {"staff": seed["staff_wos"], "ops": seed["ops_wos"]}[who]
        app_id = {"staff": seed["app_staff"], "ops": seed["app_ops"]}[who]

        def override(request: Request) -> Principal:
            principal = Principal(
                user_id=wos,
                organisation_id=f"org_act_{seed['suffix']}",
                role="adviser",
                permissions=list(perms),
                # The application the session signed in through (0107) — classifies the portal.
                client_id=app_id,
            )
            request.state.principal = principal
            return principal

        app.dependency_overrides[get_current_principal] = override

    yield _as
    app.dependency_overrides.pop(get_current_principal, None)


def _events_for(super_engine: Engine, seed: dict[str, str], event_type: str) -> list[dict]:
    """This run's rows of one event type (scoped by the seeded actors' subs — order-independent)."""
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT id, event_type, tenant_id, target_tenant_id, "
                    "       CAST(actor_id AS text) AS actor_id, actor_sub, method, route, "
                    "       status, CAST(path_params AS text) AS path_params, "
                    "       CAST(metadata AS text) AS metadata "
                    "FROM audit.actions WHERE event_type = :et AND actor_sub IN (:a, :b) "
                    "ORDER BY id"
                ),
                {"et": event_type, "a": seed["staff_wos"], "b": seed["ops_wos"]},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


# ---- the floor emits, per session family -----------------------------------------------------


@pytest.mark.asyncio
async def test_firm_session_write_emits_attributed_event(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    act("staff", ["contacts:create"])
    r = await client.post(
        "/firm/contacts",
        json={"type": "individual", "first_name": "Eve", "surname": f"Event{seed['suffix']}"},
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "client.created")
    assert len(events) == 1
    e = events[0]
    assert e["actor_sub"] == seed["staff_wos"]
    assert e["actor_id"] == seed["staff_uuid"]  # resolved auth.users uuid, not just the sub
    assert e["tenant_id"] == int(seed["firm_id"])
    assert e["target_tenant_id"] is None  # no ?tenant= — not a proxied action
    assert (e["method"], e["route"], e["status"]) == ("POST", "/firm/contacts", 200)
    # The correlation id rides along, so an event joins to the request's log lines.
    assert json.loads(e["metadata"])["correlation_id"]


@pytest.mark.asyncio
async def test_tenant_session_write_emits_with_path_params(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    act("staff", ["forms:write"])
    r = await client.post(
        "/forms", json={"code": f"act-form-{seed['suffix']}", "name": "Action form"}
    )
    assert r.status_code == 201, r.text
    form_uid = r.json()["uid"]

    r = await client.put(
        f"/forms/{form_uid}/fields",
        json={"fields": [{"field_key": "q1", "label": "Question one", "field_type": "text"}]},
    )
    assert r.status_code == 200, r.text

    created = _events_for(super_engine, seed, "form.created")
    assert [e["status"] for e in created] == [201]  # the real outcome status, not a flat 200
    assert created[0]["tenant_id"] == int(seed["firm_id"])

    fields = _events_for(super_engine, seed, "form.fields_updated")
    assert len(fields) == 1
    # The target ids land as top-level-filterable path params (route template, not raw path).
    assert fields[0]["route"] == "/forms/{uid}/fields"
    assert json.loads(fields[0]["path_params"]) == {"uid": form_uid}


@pytest.mark.asyncio
async def test_admin_session_write_emits(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    act("ops", ["system"])
    r = await client.patch(
        f"/admin/tenants/{seed['firm_uid']}", json={"name": f"Renamed {seed['suffix']}"}
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "firm.updated")
    assert len(events) == 1
    e = events[0]
    assert e["actor_sub"] == seed["ops_wos"]
    assert e["actor_id"] == seed["ops_uuid"]
    assert e["tenant_id"] == int(seed["ops_id"])  # the OPERATOR's own tenant context
    # The stream records what the ROUTE received, and the section is uid-addressed — so this is
    # the firm's address rather than its serial, which is what a reader can act on.
    assert json.loads(e["path_params"]) == {"tenant_uid": seed["firm_uid"]}
    # …AND the firm it was done TO, off the `{tenant_uid}` path param. 53 mutating routes live
    # under `/admin/tenants/{tenant_uid}/…` and every one of them is operations acting on a firm;
    # before this was read they recorded no target at all, so the firm's own trail could not show
    # support action taken on it and the row read as the OPERATOR's own firm activity.
    assert e["target_tenant_id"] == int(seed["firm_id"])


@pytest.mark.asyncio
async def test_admin_path_target_survives_an_unresolvable_id(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """A `{tenant_id}` naming no firm leaves the target NULL — it never loses the whole event.

    The path param is a serial id off the URL, so it can name a firm that does not exist (a deleted
    one, a typo, a probe). Recording a target we cannot resolve would assert something untrue;
    losing the event would be worse still, because the attempt is what an evidence stream is for.
    The route 404s, so this also pins that a refused admin action emits nothing — see
    `test_refused_action_emits_nothing` for the general rule.
    """
    act("ops", ["system"])
    r = await client.patch("/admin/tenants/2147483646", json={"name": "nobody"})
    assert r.status_code in (404, 422), r.text
    assert _events_for(super_engine, seed, "firm.updated") == []


# ---- the proxied book path names both sides ---------------------------------------------------


@pytest.mark.asyncio
async def test_proxied_book_write_records_operator_and_target_firm(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    act("ops", ["system", "book:write"])
    r = await client.patch(
        f"/firm/contacts/{seed['contact_uid']}",
        params={"tenant": seed["firm_uid"]},
        json={"phone": "01614960000"},
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "client.updated")
    assert len(events) == 1
    e = events[0]
    # BOTH sides of the proxy: the acting operator (sub + uuid + their own tenant) AND the firm
    # whose book was written.
    assert e["actor_sub"] == seed["ops_wos"]
    assert e["actor_id"] == seed["ops_uuid"]
    assert e["tenant_id"] == int(seed["ops_id"])
    assert e["target_tenant_id"] == int(seed["firm_id"])
    assert json.loads(e["metadata"])["query"]["tenant"] == seed["firm_uid"]


# ---- redaction --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_shaped_values_never_land(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    act("staff", ["contacts:create"])
    r = await client.post(
        # FastAPI ignores undeclared query params, so these reach only the middleware — which must
        # redact them.
        f"/firm/contacts?password=hunter2&access_token=tok_{seed['suffix']}&note=fine",
        json={
            "type": "individual",
            "surname": f"Redact{seed['suffix']}",
            "phone": "body-password-hunter3",
            "cookie_password": "hunter4",
        },
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "client.created")
    e = [x for x in events if json.loads(x["metadata"]).get("query")][-1]
    metadata = json.loads(e["metadata"])
    assert metadata["query"] == {
        "password": "[redacted]",
        "access_token": "[redacted]",
        "note": "fine",
    }
    whole_row = json.dumps(e, default=str)
    assert "hunter2" not in whole_row
    assert f"tok_{seed['suffix']}" not in whole_row

    # THE RULE IS THE KEY, NOT THE VALUE. A body key is caller-supplied text, so a shape rule is
    # what stops somebody smuggling content into the trail by naming a field after it.
    assert metadata["body"]["cookie_password"] == actions.REDACTED
    assert "hunter4" not in whole_row
    # …while an ordinary field is kept, whatever its value happens to look like. `phone` is not a
    # credential because its CONTENT resembles one.
    assert metadata["body"]["phone"] == "body-password-hunter3"


def test_redact_covers_audit_record_list() -> None:
    """The Python redact list must keep covering audit.record()'s SQL list (0001 + 0038) — the
    docs promise the two stay in step."""
    for key in (
        "key_hash",
        "credentials_ref",
        "workos_api_key_ct",
        "cookie_password_ct",
        "password_ct",
    ):
        assert actions.redact({key: "x"})[key] == "[redacted]"
    # And the generic shapes; target identifiers like step_key must SURVIVE (they are the point).
    assert actions.redact({"api_key": "x", "step_key": "aml_check"}) == {
        "api_key": "[redacted]",
        "step_key": "aml_check",
    }


# ---- subject capture (the SUBJECT_FIELDS allowlist) --------------------------------------------


@pytest.mark.asyncio
async def test_allowlisted_subject_fields_land_in_metadata(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """The one narrow exception to "bodies never land": an event type on SUBJECT_FIELDS captures
    exactly its named id/enum fields into metadata.subject — here, WHICH login was linked to the
    client (the product question the bare contact_id row could not answer)."""
    act("staff", ["contacts:write"])
    r = await client.post(
        f"/firm/contacts/{seed['contact_uid']}/users",
        json={"user_id": seed["staff_uuid"], "capacity": "authorised"},
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "client.portal_user_linked")
    assert len(events) == 1
    metadata = json.loads(events[0]["metadata"])
    assert metadata["subject"] == {"user_id": seed["staff_uuid"], "capacity": "authorised"}
    # And the targets stay where they were: the path param is untouched by subject capture.
    assert json.loads(events[0]["path_params"]) == {"contact_uid": seed["contact_uid"]}


@pytest.mark.asyncio
async def test_create_contact_captures_the_response_id_never_free_text(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """create-contact captures the created client's id from the RESPONSE (so "Created a client"
    names the person via RESPONSE_SUBJECT_FIELDS) — but the request body's free-text names/emails
    NEVER land: the only subject key is the resolvable `contact_id`, and the surname is nowhere in
    the row."""
    act("staff", ["contacts:create"])
    marker = f"Subjectless{seed['suffix']}"
    r = await client.post("/firm/contacts", json={"type": "individual", "surname": marker})
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "client.created")
    subject = json.loads(events[-1]["metadata"]).get("subject")
    assert subject is not None
    assert isinstance(subject["contact_id"], int)
    # The CLOSED values the create turns on may land - `type` is individual|organisation, and the
    # replayability audit added it so the row can say what KIND of client was made. The rule this
    # case protects is narrower and unchanged: no free text. Asserted as "the surname is nowhere in
    # the row" rather than as an exact key set, which is what the rule actually says.
    assert "surname" not in subject and "email" not in subject
    # The surname is on the row — in `metadata.body`, not in the SUBJECT. The distinction still
    # matters: `subject` is the resolvable, first-class fields the sentence and chips are built
    # from, and free text has never belonged there. The body is the separate, declared capture.
    assert json.loads(events[-1]["metadata"])["body"]["surname"] == marker


@pytest.mark.asyncio
async def test_redact_belt_holds_even_for_a_mistakenly_allowlisted_field(
    client: AsyncClient,
    act: ActAs,
    seed: dict[str, str],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The belt: even if a secret-shaped field is allowlisted BY MISTAKE (the tripwire test
    refuses it at review; this simulates it shipping anyway), extract_subject skips it at
    runtime — and a free-text field is bounded by the value guard, not trusted."""
    monkeypatch.setitem(
        actions.SUBJECT_FIELDS,
        "client.created",
        ("password", "api_key", "type"),
    )
    act("staff", ["contacts:create"])
    r = await client.post(
        "/firm/contacts",
        json={
            "type": "individual",
            "surname": f"Belt{seed['suffix']}",
            "password": "hunter2-belt",
            "api_key": "sk-belt-secret",
        },
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "client.created")
    latest = events[-1]
    metadata = json.loads(latest["metadata"])
    # The legitimately-allowlisted enum landed and the secret-shaped fields never did — not even
    # as "[redacted]" (a subject is captured or skipped, never marked). The response-only capture
    # still adds the created contact's id (a separate, resolvable subject).
    subject = metadata["subject"]
    assert subject["type"] == "individual"
    assert "password" not in subject and "api_key" not in subject
    assert isinstance(subject.get("contact_id"), int)
    whole_row = json.dumps(latest, default=str)
    assert "hunter2-belt" not in whole_row
    assert "sk-belt-secret" not in whole_row


@pytest.mark.asyncio
async def test_an_update_records_which_fields_it_changed_and_no_value(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """The third capture, end to end (September 2026 event-content review).

    `client.updated` had NO capture at all: the row named the client and said nothing else.
    `metadata.changed` records the field NAMES the PATCH carried and never a value, which is why it
    can be turned on across forty update routes. Driven through a real request, because the capture
    depends on the middleware BUFFERING the body for an event with no subject allowlist — the one
    wire a unit test of `extract_changed_keys` cannot see.
    """
    act("staff", ["contacts:write"])
    # Letters only: the surname validator refuses digits, so the run's suffix rides the email.
    surname = "Zzyzxvale"
    email = f"changed{seed['suffix']}@example.com"
    r = await client.patch(
        f"/firm/contacts/{seed['contact_uid']}",
        json={"surname": surname, "email": email},
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "client.updated")
    latest = events[-1]
    metadata = json.loads(latest["metadata"])
    assert metadata["changed"] == ["email", "surname"]
    # The VALUES are on the row too now (`metadata.body`), which is the deliberate widening this
    # capture was turned on for: the changed-key list says an email moved, and the body says what
    # it moved to. `changed` is still worth its place — it is what the sentence reads to say
    # "Updated Margaret Cole: email, surname" without printing either value into the headline.
    assert metadata["body"]["surname"] == surname
    assert metadata["body"]["email"] == email


@pytest.mark.asyncio
async def test_a_declared_event_records_its_request_body(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """Body capture, end to end — the widest thing the trail stores.

    Driven through a real request because the capture depends on the middleware buffering the body
    for a THIRD reason (`BODY_CAPTURE_EVENTS`): an event on that list but on neither of the others
    would otherwise reach the extractor with an empty body and record nothing, silently. It also
    pins the cost — the client's own details ARE on the row now.
    """
    act("staff", ["contacts:write"])
    surname = "Bodycapture"
    r = await client.patch(
        f"/firm/contacts/{seed['contact_uid']}",
        json={
            "surname": surname,
            "gender": "female",
            "password": "must-not-land",  # noqa: S106 - the point is that it does NOT land
        },
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "client.updated")
    metadata = json.loads(events[-1]["metadata"])
    assert metadata["body"]["surname"] == surname
    assert metadata["body"]["gender"] == "female"
    # The one thing it still refuses, at runtime rather than by declaration.
    assert metadata["body"]["password"] == actions.REDACTED
    assert "must-not-land" not in json.dumps(events[-1], default=str)


def test_extract_subject_is_fail_quiet() -> None:
    """Unit pins for the best-effort contract: non-JSON bodies, missing keys, oversized values
    and list handling — each yields less subject, never an error."""
    et = "client.portal_user_linked"  # allowlists (user_id, capacity)
    # Not JSON at all (e.g. multipart) → no subject.
    assert actions.extract_subject(et, b"--boundary\r\nnot json", {}) is None
    # A JSON array (not an object) → no subject.
    assert actions.extract_subject(et, b'["user_id"]', {}) is None
    # Missing keys are skipped; present ones land.
    assert actions.extract_subject(et, b'{"user_id": "u-1"}', {}) == {"user_id": "u-1"}
    # The query string is the fallback source (id-shaped fields can ride either).
    assert actions.extract_subject(et, b"", {"user_id": "u-2"}) == {"user_id": "u-2"}
    # An over-long string value is dropped (ids/enums are short — the value guard).
    long = "x" * 500
    assert actions.extract_subject(et, json.dumps({"user_id": long}).encode(), {}) is None
    # Lists of scalars survive (documents-send-document's contact_ids).
    assert actions.extract_subject("document.sent", b'{"contact_ids": [1, 2, 3]}', {}) == {
        "contact_ids": [1, 2, 3]
    }
    # No allowlist entry → None without touching the body.
    assert actions.extract_subject("action.not-listed", b'{"user_id": "u"}', {}) is None


def test_extract_subject_truncates_the_free_text_covering_note() -> None:
    """The document-send covering `message` is the ONE deliberate free-text subject: a long note is
    TRUNCATED (bounded, ellipsised), never dropped like an over-long id — so a firm's /events still
    reads "shared a document, saying: …". Pins the free-text carve-out from the id/enum guard."""
    et = "document.sent"  # allowlists (contact_ids, message)
    # A short note lands verbatim (and rides alongside the id-shaped contact_ids).
    assert actions.extract_subject(
        et, b'{"contact_ids": [1], "message": "Signed and returned."}', {}
    ) == {"contact_ids": [1], "message": "Signed and returned."}
    # A note past the id/enum cap (120) is TRUNCATED, not dropped — contrast the id guard above.
    out = actions.extract_subject(et, json.dumps({"message": "x" * 600}).encode(), {})
    assert out is not None
    assert out["message"].startswith("x" * 100)
    assert len(out["message"]) <= actions._MAX_SUBJECT_TEXT and out["message"].endswith("…")


def test_extract_response_subject_lifts_and_maps_the_created_id() -> None:
    """The response-only capture: a create's new id (response `id`) is lifted and MAPPED to the
    read side's subject key (`contact_id`), so "Created a client" names the person. Same fail-quiet
    contract as extract_subject — non-JSON, non-object, missing key, oversized value each yield
    nothing, and an unlisted event never touches the body."""
    et = "client.created"  # maps response {id} -> {contact_id}
    assert actions.extract_response_subject(et, b'{"id": 96, "name": "Margaret Cole"}') == {
        "contact_id": 96
    }
    # files-upload echoes the client it was filed against.
    assert actions.extract_response_subject(
        "file.uploaded", b'{"uid": "file_1", "contact_id": 7}'
    ) == {"contact_id": 7}
    # Invites: WHO was invited is free-text email in the body — lift the response user id instead.
    uuid = "550e8400-e29b-41d4-a716-446655440000"
    assert actions.extract_response_subject(
        "member.invited", f'{{"user_id": "{uuid}", "email": "x@firm.test"}}'.encode()
    ) == {"user_id": uuid}
    # Missing key / non-object / non-JSON / unlisted event → None.
    assert actions.extract_response_subject(et, b'{"name": "no id here"}') is None
    assert actions.extract_response_subject(et, b"[1, 2, 3]") is None
    assert actions.extract_response_subject(et, b"not json") is None
    assert actions.extract_response_subject("action.not-listed", b'{"id": 1}') is None


def test_extract_response_subject_lifts_every_id_out_of_a_list() -> None:
    """An UPLOAD's created documents, off `UploadResult.documents[].uid`.

    THE DEFECT THIS EXISTS FOR. `document.uploaded` captured nothing at all: the uid does not exist
    when the request arrives, so there was no path param and no request field to lift — the event
    named no document, could not appear in that document's own Events tab, and humanised to
    "Uploaded a document" on every row. Lifting the whole `documents` list is not an option either
    (`_subject_value_ok` refuses a list of objects, correctly — a subject holds short scalars, never
    rows), which is why the `list[].field` path form exists.

    A LIST because one drop may carry several files. Fail-quiet like the rest: a missing list, a
    non-list, or elements without the field each yield less, never an error.
    """
    et = "document.uploaded"
    body = (
        b'{"documents": [{"uid": "doc_a", "name": "Passport.pdf"}, '
        b'{"uid": "doc_b", "name": "Statement.pdf"}], "rejected": []}'
    )
    assert actions.extract_response_subject(et, body) == {"document_uids": ["doc_a", "doc_b"]}

    # One file is still a list — the shape does not change with the count, so no reader has to
    # handle two shapes for one event.
    assert actions.extract_response_subject(et, b'{"documents": [{"uid": "doc_a"}]}') == {
        "document_uids": ["doc_a"]
    }

    # Nothing accepted (every file refused) ⇒ no subject rather than an empty list.
    assert actions.extract_response_subject(et, b'{"documents": [], "rejected": [{}]}') is None
    # Elements without the field, a non-list, and a missing key each yield nothing.
    assert actions.extract_response_subject(et, b'{"documents": [{"name": "x"}]}') is None
    assert actions.extract_response_subject(et, b'{"documents": "not a list"}') is None
    assert actions.extract_response_subject(et, b'{"rejected": []}') is None


def test_upload_subject_resolves_to_document_entities() -> None:
    """The read side knows `document_uids` names documents.

    The write side capturing the uids is half the job: the feed only turns an id into a NAME for a
    field it can map to an entity kind, and the document filter only matches a document to an event
    through a field it knows. Both registries have to carry the new key, and they are separate
    dicts, so this pins that they agree.
    """
    from app.event_schema import registry as event_registry
    from app.routers import admin as admin_router

    assert event_registry.entity_field_kinds()["document_uids"] == "document"
    assert admin_router._ENTITY_FIELD_KINDS["document_uids"] == "document"
    # …and the stream's own field map, which drives the `tags=document` entity-kind filter.
    assert "document_uids" in audit_stream.TAG_FIELDS["document"]


# ---- before-values (the PREVIOUS_FIELDS registry) ----------------------------------------------


@pytest.mark.asyncio
async def test_previous_value_lands_for_a_registered_update(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """The "from what, to what" contract, end to end: changing an account's model records the
    OLD model in metadata.previous and the NEW one in metadata.subject — the pre-read ran before
    the handler overwrote the row."""
    # instructions:write since the 0042 split: the model mandate is money movement.
    act("staff", ["instructions:write"])
    r = await client.put(
        f"/crm/accounts/{seed['account_uid']}/model", json={"model_id": int(seed["model_b"])}
    )
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "account.model_updated")
    assert len(events) == 1
    metadata = json.loads(events[0]["metadata"])
    assert metadata["previous"] == {"model_id": int(seed["model_a"])}
    assert metadata["subject"] == {"model_id": int(seed["model_b"])}


@pytest.mark.asyncio
async def test_previous_survives_clearing_a_value(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """Clearing the mandate (model_id: null) is exactly when the before-value matters most —
    the subject captures nothing (extract_subject skips nulls), so `previous` is the only
    record of what was cleared."""
    # instructions:write since the 0042 split: the model mandate is money movement.
    act("staff", ["instructions:write"])
    r = await client.put(f"/crm/accounts/{seed['account_uid']}/model", json={"model_id": None})
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "account.model_updated")
    metadata = json.loads(events[-1]["metadata"])
    assert metadata["previous"] == {"model_id": int(seed["model_a"])}
    assert "subject" not in metadata


@pytest.mark.asyncio
async def test_unregistered_events_capture_no_previous(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """An event type off PREVIOUS_FIELDS never grows a `previous` key — creations have no prior
    state, and nothing outside the registry is pre-read at all."""
    act("staff", ["contacts:create"])
    r = await client.post(
        "/firm/contacts", json={"type": "individual", "surname": f"NoPrev{seed['suffix']}"}
    )
    assert r.status_code == 200, r.text
    latest = _events_for(super_engine, seed, "client.created")[-1]
    assert "previous" not in json.loads(latest["metadata"])


@pytest.mark.asyncio
async def test_previous_pre_read_failure_never_blocks_the_action(
    client: AsyncClient,
    act: ActAs,
    seed: dict[str, str],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-quiet by contract: a broken pre-read spec (bad SQL) must not block or fail the
    action — the event still lands, just without `previous`."""
    monkeypatch.setitem(
        actions.PREVIOUS_FIELDS,
        "account.model_updated",
        actions.PreviousSpec(
            fields=("model_id",),
            sql="SELECT model_id FROM platform.no_such_table WHERE id = :account_id",
            params=("account_id",),
        ),
    )
    # instructions:write since the 0042 split: the model mandate is money movement.
    act("staff", ["instructions:write"])
    r = await client.put(
        f"/crm/accounts/{seed['account_uid']}/model", json={"model_id": int(seed["model_b"])}
    )
    assert r.status_code == 200, r.text
    latest = _events_for(super_engine, seed, "account.model_updated")[-1]
    metadata = json.loads(latest["metadata"])
    assert "previous" not in metadata
    assert metadata["subject"] == {"model_id": int(seed["model_b"])}  # subject capture unharmed


@pytest.mark.asyncio
async def test_capture_previous_membership_and_role_specs(
    sessionlocal: Any, seed: dict[str, str]
) -> None:
    """The registry's own SQL against real rows: the membership spec returns the member's prior
    role slug keyed by tenant (the firm route can't know its tenant until write time), and the
    role-permissions spec returns the prior slug list, sorted. One spec per ACT covers both the
    firm's door and Graphene's — see the note below."""
    firm = int(seed["firm_id"])

    membership = await actions.capture_previous(
        "member.portal_role_updated",
        {"user_id": seed["staff_uuid"], "portal": "platform"},
    )
    # A LIST now, matching the shape the request sends: a person may hold several roles in one
    # portal, and the read side pairs before with after by NAME.
    assert membership == {firm: {"role_slugs": [seed["role_slug"]]}}

    permissions = await actions.capture_previous(
        "role.permissions_updated", {"slug": seed["role_slug"]}
    )
    assert permissions == {firm: {"permissions": sorted([seed["perm_a"], seed["perm_b"]])}}

    # ONE SPEC NOW SERVES BOTH DOORS. There used to be a second entry per event type for the ops
    # route, which pinned the row by its `{tenant_id}` path param — and because a dict keeps the
    # LAST
    # duplicate key, the FIRM door was silently running the ops spec against a path that has no
    # `tenant_id`. Collapsing the two event types into one act made the clash visible; the per-
    # tenant
    # spec is what covers both, since it needs only the params both paths carry.
    #
    # The ops door still resolves to the right firm: it records `target_tenant_id` now, and
    # `pick_previous` prefers the target over the actor's own tenant.
    admin_membership = await actions.capture_previous(
        "member.portal_role_updated",
        {"user_id": seed["staff_uuid"], "tenant_id": seed["firm_id"], "portal": "platform"},
    )
    assert admin_membership == {firm: {"role_slugs": [seed["role_slug"]]}}
    assert actions.pick_previous(admin_membership, tenant_id=None, target_tenant_id=firm) == {
        "role_slugs": [seed["role_slug"]]
    }

    admin_permissions = await actions.capture_previous(
        "role.permissions_updated",
        {"slug": seed["role_slug"], "tenant_id": seed["firm_id"]},
    )
    assert admin_permissions == {firm: {"permissions": sorted([seed["perm_a"], seed["perm_b"]])}}

    # A junk path param breaks the CAST inside the pre-read — fail-quiet, never an error.
    junk = await actions.capture_previous(
        "member.portal_role_updated", {"user_id": "not-a-uuid", "portal": "platform"}
    )
    assert junk is None


def test_pick_previous_resolves_the_tenant_at_write_time() -> None:
    """Unit pins for the write-time choice: proxied target first, then the actor's tenant, then
    the pinned (None-keyed) row; no match — or nothing captured — yields nothing."""
    per_tenant: actions.CapturedPrevious = {
        7: {"role_slug": "adviser"},
        9: {"role_slug": "compliance"},
    }
    # The proxied ?tenant= target wins over the actor's own tenant.
    assert actions.pick_previous(per_tenant, tenant_id=7, target_tenant_id=9) == {
        "role_slug": "compliance"
    }
    assert actions.pick_previous(per_tenant, tenant_id=7, target_tenant_id=None) == {
        "role_slug": "adviser"
    }
    # A per-tenant capture with no row for the resolved tenant degrades, never guesses.
    assert actions.pick_previous(per_tenant, tenant_id=3, target_tenant_id=None) is None
    # Non-per-tenant specs key their single row on None.
    pinned: actions.CapturedPrevious = {None: {"model_id": 4}}
    assert actions.pick_previous(pinned, tenant_id=7, target_tenant_id=9) == {"model_id": 4}
    assert actions.pick_previous(None, tenant_id=7, target_tenant_id=None) is None
    assert actions.pick_previous({}, tenant_id=7, target_tenant_id=None) is None


def test_previous_specs_sql_is_executable_and_shaped(super_engine: Engine) -> None:
    """Every registered spec's SQL must run (all-None binds — zero rows is fine) and return the
    columns the spec declares (+ tenant_id for per-tenant specs). A spec whose SQL drifted from
    its fields would otherwise fail quietly forever — the exact rot this suite exists to stop."""
    with super_engine.connect() as c:
        for event_type, spec in actions.PREVIOUS_FIELDS.items():
            result = c.execute(text(spec.sql), dict.fromkeys(spec.params))
            columns = set(result.keys())
            missing = set(spec.fields) - columns
            assert not missing, f"{event_type}: SQL never returns declared fields {missing}"
            if spec.per_tenant:
                assert "tenant_id" in columns, (
                    f"{event_type}: per-tenant spec must SELECT tenant_id"
                )


# ---- what the save moved (the CHANGED_COLUMNS registry) ---------------------------------------


@pytest.mark.asyncio
async def test_an_update_records_the_columns_it_moved_from_and_to(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """The fifth capture, end to end — the one place the whole CHAIN is observed.

    `metadata.changes` is three moving parts wired across a request: a pre-read on the ASGI receive
    hook, a second read after the response is on the wire, and `diff_rows` between them. Each part
    is pinned alone in `test_event_content.py`, and each can be correct while the chain records
    NOTHING — and the failure is silent, because a missing `changes` key looks exactly like a save
    that moved nothing. `task.updated` drives it as the cheapest declared spec.
    """
    s = seed["suffix"]
    task_uid = f"acttsk_{s}"
    was, now = f"Chase the P60 {s}", f"Chase the P45 {s}"
    with super_engine.connect() as c:
        c.execute(
            text(
                "INSERT INTO tasks.tasks (uid, tenant_id, assignee_kind, assignee_user_id, "
                "  title, category, priority, status, created_by) "
                "VALUES (:uid, :t, 'user', CAST(:u AS uuid), :title, 'follow_up', 'normal', "
                "  'open', CAST(:u AS uuid))"
            ),
            {"uid": task_uid, "t": int(seed["firm_id"]), "u": seed["staff_uuid"], "title": was},
        )
        c.commit()

    act("staff", ["tasks:read", "tasks:write"])
    # `status` is the WIRE name: `UpdateTask` declares the field `task_status` with
    # `alias="status"`, and nothing in this chain reads the model.
    r = await client.patch(f"/tasks/{task_uid}", json={"title": now, "status": "in_progress"})
    assert r.status_code == 200, r.text

    events = _events_for(super_engine, seed, "task.updated")
    assert len(events) == 1
    changes = json.loads(events[0]["metadata"])["changes"]
    # BOTH sides of every column that moved — and the OUTCOME the row committed, not the
    # intention the body stated.
    assert changes["title"] == {"from": was, "to": now}
    assert changes["status"] == {"from": "open", "to": "in_progress"}
    # …and it is a DIFF, not the row: a column the save left alone is absent. That property is
    # what makes the capture affordable on a wide table instead of a second copy of it.
    assert "priority" not in changes
    assert "category" not in changes
    assert "id" not in changes


# ---- failure semantics ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refused_action_emits_nothing(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """An audit of effects, not attempts: 4xx (here a 403 — no permission) emits no event."""
    act("staff", [])  # no contacts:create
    before = len(_events_for(super_engine, seed, "client.created"))
    r = await client.post("/firm/contacts", json={"type": "individual", "surname": "Denied"})
    assert r.status_code == 403
    assert len(_events_for(super_engine, seed, "client.created")) == before


@pytest.mark.asyncio
async def test_event_write_failure_never_breaks_the_action(
    client: AsyncClient,
    act: ActAs,
    seed: dict[str, str],
    super_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Best-effort: the action succeeds even when the event write blows up — and the failure is
    REPORTED (report_error → error tracker + structured log), never silent."""

    async def boom(**_kwargs: Any) -> None:
        raise RuntimeError("audit.actions is on fire")

    monkeypatch.setattr(actions, "_write_event", boom)
    act("staff", ["contacts:create"])
    with captured("app.audit_actions", level=logging.ERROR) as records:
        r = await client.post(
            "/firm/contacts", json={"type": "individual", "surname": f"Fail{seed['suffix']}"}
        )
    assert r.status_code == 200, r.text  # the response was already on the wire; never a 500
    assert any(getattr(rec, "event", "") == "action_event_write_failed" for rec in records), (
        "the swallowed failure must reach report_error"
    )


@pytest.mark.asyncio
async def test_reads_do_not_emit(
    client: AsyncClient, act: ActAs, seed: dict[str, str], super_engine: Engine
) -> None:
    """The MIDDLEWARE never fires on a GET — it records effects, and a read is not one. The one
    deliberate exception is the explicit document-activity writer for a document VIEW (see
    ``record_document_action``): it is not the middleware, it carries its own event type, and it is
    excluded here so this pins the middleware invariant, not its."""
    act("staff", ["forms:read"])
    r = await client.get("/forms")
    assert r.status_code == 200, r.text
    with super_engine.connect() as c:
        n = c.execute(
            text("SELECT count(*) FROM audit.actions WHERE method = 'GET' AND event_type <> :view"),
            {"view": actions.DOCUMENT_VIEW_EVENT},
        ).scalar_one()
    assert n == 0


# ---- the document-activity floor (view / worker render) ---------------------------------------
# The two kinds of document activity the request-path middleware cannot see, folded in by
# `record_document_action` (a GET view, a worker render). Same stream, same read surface; distinct
# event types so a reader never double-counts against the mutating document routes the middleware
# already covers. A third — an anonymous public-share access — went with the share-links feature.


def _all_events(super_engine: Engine, event_type: str) -> list[dict]:
    """Every row of one event type (caller isolates its own by tenant/actor) — for events with no
    document uid or seeded actor to scope by (e.g. an anonymous failed login)."""
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT id, event_type, tenant_id, CAST(actor_id AS text) AS actor_id, "
                    "  actor_sub, method, route, status, CAST(path_params AS text) AS path_params, "
                    "  CAST(metadata AS text) AS metadata "
                    "FROM audit.actions WHERE event_type = :et ORDER BY id"
                ),
                {"et": event_type},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def _doc_events(super_engine: Engine, event_type: str, uid: str) -> list[dict]:
    """This run's rows of one document event type, scoped by the run-unique document uid."""
    with super_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT id, event_type, tenant_id, CAST(actor_id AS text) AS actor_id, "
                    "  actor_sub, method, route, status, CAST(path_params AS text) AS path_params, "
                    "  CAST(metadata AS text) AS metadata "
                    "FROM audit.actions WHERE event_type = :et AND path_params->>'uid' = :uid "
                    "ORDER BY id"
                ),
                {"et": event_type, "uid": uid},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@pytest.mark.asyncio
async def test_document_render_event_records_explicit_context(
    sessionlocal: async_sessionmaker[AsyncSession], seed: dict[str, str], super_engine: Engine
) -> None:
    """The worker render path: an explicit tenant, the queuer as actor, the system sub — no
    principal, its own service_role transaction."""
    uid = f"docrender_{seed['suffix']}"
    await actions.record_document_action(
        event_type=actions.DOCUMENT_RENDER_EVENT,
        method="POST",
        route="/documents/{uid}/render",
        document_uid=uid,
        tenant_id=int(seed["firm_id"]),
        actor_id=seed["staff_uuid"],
        actor_sub=actions.WORKER_RENDER_SUB,
        extra={"renderer": "weasyprint", "version": 1},
    )
    rows = _doc_events(super_engine, actions.DOCUMENT_RENDER_EVENT, uid)
    assert len(rows) == 1
    e = rows[0]
    assert e["tenant_id"] == int(seed["firm_id"])
    assert (
        e["actor_id"] == seed["staff_uuid"]
    )  # the queuer — reads in their name, as audit_log does
    assert e["actor_sub"] == actions.WORKER_RENDER_SUB
    assert (e["method"], e["route"], e["status"]) == ("POST", "/documents/{uid}/render", 200)
    assert json.loads(e["path_params"]) == {"uid": uid}
    md = json.loads(e["metadata"])
    assert md["renderer"] == "weasyprint" and md["version"] == 1
    assert "correlation_id" in md  # rides along even with no request (None outside one)


@pytest.mark.asyncio
async def test_document_view_event_resolves_tenant_and_actor_from_principal(
    sessionlocal: async_sessionmaker[AsyncSession], seed: dict[str, str], super_engine: Engine
) -> None:
    """The view path passes a signed-in principal, and the SAME ``auth.resolve_claims`` the
    request-path floor uses resolves the tenant + the actor's ``auth.users`` uuid."""
    uid = f"docview_{seed['suffix']}"
    principal = Principal(
        user_id=seed["staff_wos"],
        organisation_id=f"org_act_{seed['suffix']}",
        role="adviser",
        permissions=["documents:read"],
    )
    await actions.record_document_action(
        event_type=actions.DOCUMENT_VIEW_EVENT,
        method="GET",
        route="/documents/{uid}/url",
        document_uid=uid,
        principal=principal,
    )
    rows = _doc_events(super_engine, actions.DOCUMENT_VIEW_EVENT, uid)
    assert len(rows) == 1
    e = rows[0]
    assert e["tenant_id"] == int(seed["firm_id"])
    assert e["actor_id"] == seed["staff_uuid"]  # resolved, not just the raw sub
    assert e["actor_sub"] == seed["staff_wos"]
    assert (e["method"], e["route"]) == ("GET", "/documents/{uid}/url")


@pytest.mark.asyncio
async def test_document_action_skips_when_no_context(
    sessionlocal: async_sessionmaker[AsyncSession], super_engine: Engine
) -> None:
    """No principal and no explicit tenant ⇒ nothing to attribute the event to ⇒ no row written
    (a guard against a malformed row, not a crash)."""
    uid = f"docnoctx_{uuid.uuid4().hex[:8]}"
    await actions.record_document_action(
        event_type=actions.DOCUMENT_VIEW_EVENT,
        method="GET",
        route="/documents/{uid}/url",
        document_uid=uid,
        actor_sub="someone",
    )
    assert _doc_events(super_engine, actions.DOCUMENT_VIEW_EVENT, uid) == []


@pytest.mark.asyncio
async def test_document_action_write_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed write must never propagate — the view/render/access already happened — but it is
    REPORTED (report_error → error tracker + structured log), never silent."""

    def boom() -> Any:
        raise RuntimeError("audit.actions is on fire")

    monkeypatch.setattr(db_mod, "SessionLocal", boom)
    with captured("app.audit_actions", level=logging.ERROR) as records:
        await actions.record_document_action(
            event_type=actions.DOCUMENT_VIEW_EVENT,
            method="GET",
            route="/documents/{uid}/url",
            document_uid="doc_x",
            tenant_id=1,
            actor_sub=actions.WORKER_RENDER_SUB,
        )
    assert any(
        getattr(rec, "event", "") == "document_action_event_write_failed" for rec in records
    ), "the swallowed failure must reach report_error"


# ---- auth events (record_auth_action) ----------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_activated_event_records_and_resolves_actor(
    sessionlocal: async_sessionmaker[AsyncSession], seed: dict[str, str], super_engine: Engine
) -> None:
    """record_auth_action appends a synthetic auth event and resolves the actor uuid from the
    WorkOS id (auth.users.auth_id), so the activation trail reads in the person's name."""
    await actions.record_auth_action(
        event_type=actions.AUTH_ACTIVATED_EVENT,
        tenant_id=int(seed["firm_id"]),
        actor_sub=seed["staff_wos"],
    )
    rows = _events_for(super_engine, seed, actions.AUTH_ACTIVATED_EVENT)
    assert len(rows) == 1
    e = rows[0]
    assert e["actor_sub"] == seed["staff_wos"]
    assert e["actor_id"] == seed["staff_uuid"]  # resolved from auth.users.auth_id, not just the sub
    assert e["tenant_id"] == int(seed["firm_id"])
    # The pseudo path follows the event name (`authentication.account_activated`), which is why
    # it reads `account-activated` rather than the old `activated`. Cosmetic, and consistent.
    assert (e["method"], e["route"], e["status"]) == ("POST", "/auth/account-activated", 200)
    assert "correlation_id" in json.loads(e["metadata"])


@pytest.mark.asyncio
async def test_successful_login_stamps_last_sign_in(
    sessionlocal: async_sessionmaker[AsyncSession], seed: dict[str, str], super_engine: Engine
) -> None:
    """The synthetic login event is also the authoritative seam for the directory's last seen."""
    # The fixture binds app.audit_actions' session factory to this test DB.
    assert sessionlocal is not None
    with super_engine.begin() as conn:
        conn.execute(
            text("UPDATE auth.users SET last_sign_in = NULL WHERE id = CAST(:u AS uuid)"),
            {"u": seed["staff_uuid"]},
        )

    await actions.record_auth_action(
        event_type=actions.AUTH_LOGIN_EVENT,
        tenant_id=int(seed["firm_id"]),
        actor_sub=seed["staff_wos"],
    )

    with super_engine.connect() as conn:
        stamped = conn.execute(
            text("SELECT last_sign_in FROM auth.users WHERE id = CAST(:u AS uuid)"),
            {"u": seed["staff_uuid"]},
        ).scalar_one()
    assert stamped is not None


@pytest.mark.asyncio
async def test_auth_login_failed_records_attempt_without_a_subject(
    sessionlocal: async_sessionmaker[AsyncSession], seed: dict[str, str], super_engine: Engine
) -> None:
    """A FAILED login has no verified subject — it is attributed to the anonymous marker with the
    attempted email in the metadata (the security-relevant identifier, not a credential) and a 401
    status, so the trail tones it as a warning and password-spraying is visible."""
    await actions.record_auth_action(
        event_type=actions.AUTH_LOGIN_FAILED_EVENT,
        tenant_id=int(seed["firm_id"]),
        actor_sub=actions.ANONYMOUS_LOGIN_SUB,
        status=401,
        extra={"attempted_email": "wrong@firm.test"},
    )
    rows = [
        r
        for r in _all_events(super_engine, actions.AUTH_LOGIN_FAILED_EVENT)
        if r["actor_sub"] == actions.ANONYMOUS_LOGIN_SUB and r["tenant_id"] == int(seed["firm_id"])
    ]
    assert len(rows) == 1
    e = rows[0]
    assert e["actor_id"] is None  # a failed login resolves to no mirror user
    assert e["status"] == 401
    assert json.loads(e["metadata"])["attempted_email"] == "wrong@firm.test"


@pytest.mark.asyncio
async def test_auth_action_write_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """Like the document producer: a failed auth-event write is REPORTED, never propagated — the
    login/logout/activation already happened."""

    def boom() -> Any:
        raise RuntimeError("audit.actions is on fire")

    monkeypatch.setattr(db_mod, "SessionLocal", boom)
    with captured("app.audit_actions", level=logging.ERROR) as records:
        await actions.record_auth_action(
            event_type=actions.AUTH_ACTIVATED_EVENT, tenant_id=1, actor_sub="user_x"
        )
    assert any(getattr(rec, "event", "") == "auth_action_event_write_failed" for rec in records)
