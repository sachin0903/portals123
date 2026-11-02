"""Action nodes — the engine doing unattended work inside a journey, against a real Postgres.

Four things are being proved, and they map 1:1 onto the design decisions:

1. **Each document action goes through the real service.** ``request_document`` /
   ``generate_document`` / ``send_document`` assert on the rows ``app/documents/service.py``
   writes — the request and its item, the queued document and its render job, the recipient link
   and the per-recipient audit event. Mocking the service would prove nothing: the whole reason it
   was extracted from the router is that the automated path and the human path must be the same
   code, and only its observable effects can show that.
2. **The audit event names the automation.** Every action emits the event type a HUMAN doing the
   same act emits, with ``actor_sub = automation:flow:{code}:{node}`` and the automation marker's
   ``source: "flow"`` — so automated journey work sits in the firm's own activity log next to
   everyone else's, filterable by actor rather than hidden in a separate stream.
3. **A failure fails visibly, and rolls back.** The refusing action leaves NOTHING behind (its
   savepoint is rolled back), the step latches ``failed``, the case parks ``blocked`` — not the
   terminal ``failed``, which is latched and could never be retried — and
   ``orchestrator.retry_step`` re-runs it.
4. **``kind`` is the discriminator.** A ``generate_document`` STEP node and a ``generate_document``
   ACTION node coexist in one flow and behave differently: the step queues a render and WAITS for
   the worker's callback, the action queues one and completes. This is the collision the contract's
   explicit ``kind`` exists to settle, and this test is what stops a precedence rule creeping back.

The bed is per-test-nonce and never truncates (``test_effects``'s reasoning): another suite's
leftovers cannot make an assertion pass or fail.
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

from app import tags as tags_mod
from app.documents import service as docs_service
from app.flows import handlers, orchestrator, step_handlers
from app.flows import memory as memory_mod
from app.flows.domain_events import StepSignal

Maker = async_sessionmaker[AsyncSession]


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def outbox_on_container(db: dict[str, str], monkeypatch) -> None:
    """Point the notification outbox's own service_role transaction at the test container.

    ``queue_client_email`` opens its own connection through ``app.db.SessionLocal`` (see its
    docstring), which in a test is a developer's local database or nothing at all. Without this the
    document actions' notifications would silently fail — and every "did the client get told"
    assertion would be vacuously true.
    """
    engine = create_async_engine(db["app"])
    outbox_maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _sys() -> AsyncIterator[AsyncSession]:
        async with outbox_maker() as session, session.begin():
            await session.execute(text("SET LOCAL ROLE service_role"))
            yield session

    monkeypatch.setattr(docs_service, "system_transaction", _sys)


class _Bed:
    """One firm, one client, one published flow with a live execution + case to hang steps off."""

    def __init__(self, maker: Maker, nonce: str, ids: dict[str, Any]) -> None:
        self.maker = maker
        self.nonce = nonce
        self.tenant_id = int(ids["tenant_id"])
        self.contact_id = int(ids["contact_id"])
        self.user_id = str(ids["user_id"])
        self.role_id = int(ids["role_id"])
        self.flow_id = int(ids["flow_id"])
        self.flow_version_id = int(ids["flow_version_id"])
        self.execution_id = int(ids["execution_id"])
        self.case_id = int(ids["case_id"])
        self.case_uid = str(ids["case_uid"])

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A service_role session — what the orchestrator runs on (``db.firm_session``)."""
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            yield s

    def ctx(self, step_key: str, config: dict[str, Any], session: AsyncSession) -> Any:
        return handlers.StepContext(
            session=session,
            tenant_id=self.tenant_id,
            execution_id=self.execution_id,
            case_id=self.case_id,
            step_key=step_key,
            config=config,
            subject_type="contact",
            subject_id=self.contact_id,
        )

    async def add_step(self, step_key: str, step_type: str, title: str = "Do the thing") -> None:
        async with self.session() as s:
            await s.execute(
                text(
                    "INSERT INTO flows.case_steps (tenant_id, case_id, step_id, step_key, "
                    "  step_type, title, assignee, required, status) "
                    "VALUES (:t, :c, :k, :k, :st, :ti, 'staff', true, 'pending')"
                ),
                {
                    "t": self.tenant_id,
                    "c": self.case_id,
                    "k": step_key,
                    "st": step_type,
                    "ti": title,
                },
            )

    async def instantiate(self, step_key: str, step_type: str, config: dict[str, Any]) -> Any:
        """Instantiate one planned step exactly as the orchestrator does: persist the row, resolve
        the handler by its persisted ``step_type``, call ``instantiate``. Returns its StepEvent."""
        await self.add_step(step_key, step_type)
        handler = handlers.get_handler(step_type)
        assert handler is not None, f"no handler registered for {step_type}"
        async with self.session() as s:
            return await handler.instantiate(self.ctx(step_key, config, s))

    async def run_action(
        self, action_type: str, config: dict[str, Any], *, step_key: str | None = None
    ) -> Any:
        key = step_key or f"act_{uuid.uuid4().hex[:6]}"
        return await self.instantiate(key, handlers.action_step_type(action_type), config)

    async def run_planned(self, step: dict[str, Any]) -> Any:
        """Run a step straight out of a validated definition's derived projection — the shape the
        planner fans out, so the persisted ``step_type`` is the one production would carry."""
        config = step.get("config") or {}
        return await self.instantiate(str(step["id"]), str(step["type"]), dict(config))

    async def rows(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        async with self.session() as s:
            result = (
                await s.execute(text(sql), {"t": self.tenant_id, **(params or {})})
            ).mappings()
            return [dict(r) for r in result.all()]

    async def events(self) -> list[dict[str, Any]]:
        return await self.rows(
            "SELECT event_type, actor_sub, actor_id, method, route, metadata "
            "FROM audit.actions WHERE tenant_id = :t ORDER BY id"
        )

    async def contact_tags(self) -> list[str]:
        async with self.session() as s:
            return await tags_mod.list_for(
                s, target="contact", target_id=self.contact_id, tenant_id=self.tenant_id
            )

    async def grant(self, *slugs: str) -> None:
        async with self.session() as s:
            for slug in slugs:
                await s.execute(
                    text(
                        "INSERT INTO auth.role_permissions (role_id, permission_id) "
                        "SELECT :r, id FROM auth.permissions WHERE slug = :s "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"r": self.role_id, "s": slug},
                )

    async def seed_template(self, *, published: bool = True) -> str:
        """A publishable template with no unsourced variables (the completeness gate is real)."""
        uid = f"tpl_{self.nonce}"
        async with self.session() as s:
            template_id = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO documents.templates (tenant_id, uid, code, name, "
                            "  document_type, created_by) "
                            "VALUES (:t, :u, :c, 'Welcome pack', 'letter', CAST(:by AS uuid)) "
                            "RETURNING id"
                        ),
                        {
                            "t": self.tenant_id,
                            "u": uid,
                            "c": f"welcome_{self.nonce}",
                            "by": self.user_id,
                        },
                    )
                ).scalar_one()
            )
            version_id = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO documents.template_versions (uid, tenant_id, template_id, "
                            "  version_number, status, schema, created_by) "
                            "VALUES (:u, :t, :tp, 1, :st, CAST(:sc AS jsonb), CAST(:by AS uuid)) "
                            "RETURNING id"
                        ),
                        {
                            "u": f"tpv_{self.nonce}",
                            "t": self.tenant_id,
                            "tp": template_id,
                            "st": "published" if published else "draft",
                            "sc": json.dumps({"html": "<p>Welcome.</p>", "css": ""}),
                            "by": self.user_id,
                        },
                    )
                ).scalar_one()
            )
            if published:
                await s.execute(
                    text(
                        "UPDATE documents.templates SET current_published_version_id = :v "
                        "WHERE id = :i"
                    ),
                    {"v": version_id, "i": template_id},
                )
        return uid

    async def seed_document(self) -> str:
        uid = f"doc_{self.nonce}"
        async with self.session() as s:
            await s.execute(
                text(
                    "INSERT INTO documents.documents (uid, tenant_id, document_type, source, "
                    "  name, status) VALUES (:u, :t, 'letter', 'uploaded', 'Your report', "
                    "  'generated')"
                ),
                {"u": uid, "t": self.tenant_id},
            )
        return uid


@pytest_asyncio.fixture
async def bed(maker: Maker) -> _Bed:
    nonce = uuid.uuid4().hex[:8]
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tenant_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {"u": f"acs_{nonce}", "a": f"acs-{nonce}", "n": f"Actions {nonce}"},
                )
            ).scalar_one()
        )
        contact_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, first_name, type, "
                        "  service_type, email) "
                        "VALUES (:t, :u, 'Ada Client', 'Ada', 'individual', 'advised', :e) "
                        "RETURNING id"
                    ),
                    {"t": tenant_id, "u": f"con_{nonce}", "e": f"ada.{nonce}@example.test"},
                )
            ).scalar_one()
        )
        user_id = str(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email, "
                        "  first_name, last_name) "
                        "VALUES (:uid, :t, :w, :e, 'Grace', 'Adviser') RETURNING id"
                    ),
                    {
                        "uid": f"usr_{nonce}",
                        "t": tenant_id,
                        "w": f"user_{nonce}",
                        "e": f"grace.{nonce}@example.test",
                    },
                )
            ).scalar_one()
        )
        role_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.roles (tenant_id, slug, name, portal) "
                        "VALUES (:t, 'adviser', 'Adviser', 'platform') RETURNING id"
                    ),
                    {"t": tenant_id},
                )
            ).scalar_one()
        )
        flow_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                        "VALUES (:u, :t, :c, 'Onboarding', 'published') RETURNING id"
                    ),
                    {"u": f"flw_{nonce}", "t": tenant_id, "c": f"onboard_{nonce}"},
                )
            ).scalar_one()
        )
        version_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, "
                        "  status, definition) "
                        "VALUES (:u, :t, :f, 1, 'published', CAST(:d AS jsonb)) RETURNING id"
                    ),
                    {
                        "u": f"flv_{nonce}",
                        "t": tenant_id,
                        "f": flow_id,
                        "d": json.dumps({"steps": [], "milestones": []}),
                    },
                )
            ).scalar_one()
        )
        execution_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO flows.executions (uid, tenant_id, flow_id, flow_version_id, "
                        "  status) VALUES (:u, :t, :f, :v, 'active') RETURNING id"
                    ),
                    {"u": f"fex_{nonce}", "t": tenant_id, "f": flow_id, "v": version_id},
                )
            ).scalar_one()
        )
        case_uid = f"cas_{nonce}"
        case_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO flows.cases (uid, tenant_id, flow_id, flow_version_id, "
                        "  subject_contact_id, execution_id, status, kind, subject_type, "
                        "  subject_id) "
                        "VALUES (:u, :t, :f, :v, :c, :e, 'active', 'profile', 'contact', :c) "
                        "RETURNING id"
                    ),
                    {
                        "u": case_uid,
                        "t": tenant_id,
                        "f": flow_id,
                        "v": version_id,
                        "c": contact_id,
                        "e": execution_id,
                    },
                )
            ).scalar_one()
        )
    return _Bed(
        maker,
        nonce,
        {
            "tenant_id": tenant_id,
            "contact_id": contact_id,
            "user_id": user_id,
            "role_id": role_id,
            "flow_id": flow_id,
            "flow_version_id": version_id,
            "execution_id": execution_id,
            "case_id": case_id,
            "case_uid": case_uid,
        },
    )


def _automation_marker(event: dict[str, Any]) -> dict[str, Any]:
    metadata = event["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    marker = metadata.get("automation")
    assert isinstance(marker, dict), "an automated action must carry the automation marker"
    return marker


# ---------------------------------------------------------------------------------------------
# The registry + the discriminator
# ---------------------------------------------------------------------------------------------


@pytest.mark.pkgs_ui_pin
def test_every_contract_action_has_a_flow_node_handler() -> None:
    """An action type the builder can author but the engine cannot resolve is a node that plans a
    step nothing will ever pick up — pending for ever, with no error anywhere."""
    from app.flows import graph as graph_mod

    for action_type in graph_mod.ACTION_CONFIG_KEYS:
        handler = handlers.get_handler(handlers.action_step_type(action_type))
        assert isinstance(handler, step_handlers.ActionStepHandler)
        assert handler.action_type == action_type


def test_the_action_prefix_round_trips() -> None:
    assert handlers.action_type_of(handlers.action_step_type("apply_tag")) == "apply_tag"
    # An ordinary step type is not engine work, and must not be mistaken for a truncated one.
    assert handlers.action_type_of("generate_document") is None


def test_an_action_node_is_never_assigned_to_a_client() -> None:
    """The persisted column's vocabulary is client|staff and its default is 'client'. An action is
    neither — but 'client' would put engine work in a client's portal, so the projection pins it."""
    from app.flows import definitions

    out = definitions.validate_definition(
        {"nodes": [{"id": "tag", "kind": "action", "type": "apply_tag", "config": {"tag": "x"}}]}
    )
    assert out["steps"][0]["assignee"] == "staff"  # type: ignore[index]


# ---------------------------------------------------------------------------------------------
# The context an action node's copy interpolates against
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_action_nodes_copy_reads_the_runtime_context(bed: _Bed) -> None:
    """An action node interpolates against ``orchestrator.case_context`` — the SAME mapping the
    branch node beside it is evaluated against, case memory included.

    It used to interpolate against nothing at all (``facts={}``), so ``{{ memory.risk_band }}`` in
    an approved email template and ``{{ contact.service_type }}-client`` in a tag both rendered
    empty on this path while working on the rule tier's — while the step-level ``tasks``
    side-effect, which already read ``case_context``, behaved a third way. Three surfaces the
    author cannot tell apart, two of them lying.
    """
    async with bed.session() as s:
        await memory_mod.write(
            s,
            tenant_id=bed.tenant_id,
            case_id=bed.case_id,
            key="risk_band",
            value="cautious",
            source="answers",
        )

    event = await bed.run_action("apply_tag", {"tag": "{{ memory.risk_band }}-review"})

    assert event.signal is StepSignal.COMPLETED, event.reason
    assert await bed.contact_tags() == ["cautious-review"]


@pytest.mark.asyncio
async def test_an_action_node_can_print_the_clients_own_fields(bed: _Bed) -> None:
    """The other half of the same absence: ``apply_tag``'s own comment claims
    ``{{ contact.service_type }}-client`` is authorable, which was false wherever the action was
    authored inside a flow rather than on a rule."""
    event = await bed.run_action("apply_tag", {"tag": "{{ contact.service_type }}-client"})

    assert event.signal is StepSignal.COMPLETED, event.reason
    assert await bed.contact_tags() == ["advised-client"]


# ---------------------------------------------------------------------------------------------
# The four actions, through the real services
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_document_raises_a_real_client_request(bed: _Bed) -> None:
    event = await bed.run_action(
        "request_document", {"title": "Passport scan", "category": "identity"}
    )
    assert event is not None and event.signal is StepSignal.COMPLETED

    requests = await bed.rows(
        "SELECT r.uid, r.contact_id, r.status, i.action, i.title, i.category "
        "FROM documents.requests r JOIN documents.request_items i ON i.request_id = r.id "
        "WHERE r.tenant_id = :t"
    )
    assert len(requests) == 1
    assert requests[0]["contact_id"] == bed.contact_id
    assert (requests[0]["action"], requests[0]["title"]) == ("upload", "Passport scan")
    assert requests[0]["category"] == "identity"

    # No inline email here: the request notice is the event-triggered notification system's job now
    # (the document.request rule reacts to the action below), not an outbox row this action queues —
    # that retired the double-send. So the flow raises the request and emits the action; the outbox
    # stays empty on this path.
    queued = await bed.rows(
        "SELECT event_type, payload FROM flows.outbox WHERE tenant_id = :t ORDER BY id"
    )
    assert [q["event_type"] for q in queued] == []

    events = await bed.events()
    assert [e["event_type"] for e in events] == ["document_request.created"]
    assert events[0]["actor_id"] is None  # no human did this, and none is named
    assert str(events[0]["actor_sub"]).startswith("automation:flow:")
    assert _automation_marker(events[0])["source"] == "flow"


@pytest.mark.asyncio
async def test_generate_document_queues_a_render_and_completes(bed: _Bed) -> None:
    template_uid = await bed.seed_template()
    event = await bed.run_action("generate_document", {"template_uid": template_uid})
    assert event is not None and event.signal is StepSignal.COMPLETED

    docs = await bed.rows(
        "SELECT d.uid, d.status, d.source, d.intended_contact_id, d.metadata, "
        "  (SELECT count(*) FROM documents.render_jobs j WHERE j.document_id = d.id) AS jobs "
        "FROM documents.documents d WHERE d.tenant_id = :t"
    )
    assert len(docs) == 1
    assert (docs[0]["status"], docs[0]["source"]) == ("queued", "generated")
    assert docs[0]["intended_contact_id"] == bed.contact_id
    assert docs[0]["jobs"] == 1
    metadata = docs[0]["metadata"]
    assert (json.loads(metadata) if isinstance(metadata, str) else metadata)["source"] == (
        "automation"
    )

    events = await bed.events()
    assert [e["event_type"] for e in events] == ["document.generated"]


@pytest.mark.asyncio
async def test_send_document_releases_it_to_the_client(bed: _Bed) -> None:
    document_uid = await bed.seed_document()
    event = await bed.run_action("send_document", {"document_uid": document_uid})
    assert event is not None and event.signal is StepSignal.COMPLETED

    recipients = await bed.rows(
        "SELECT dc.contact_id, dc.role FROM documents.document_contacts dc "
        "JOIN documents.documents d ON d.id = dc.document_id "
        "WHERE d.uid = :u AND dc.tenant_id = :t",
        {"u": document_uid},
    )
    assert recipients == [{"contact_id": bed.contact_id, "role": "recipient"}]
    # The per-recipient audit event the client's own activity feed reads.
    log = await bed.rows(
        "SELECT a.action, a.actor_id FROM documents.audit_log a "
        "JOIN documents.documents d ON d.id = a.document_id WHERE d.uid = :u AND a.tenant_id = :t",
        {"u": document_uid},
    )
    assert [row["action"] for row in log] == ["document.sent"]
    assert log[0]["actor_id"] is None

    events = await bed.events()
    assert [e["event_type"] for e in events] == ["document.sent"]


@pytest.mark.asyncio
async def test_webhook_out_queues_an_allowlisted_endpoint(bed: _Bed, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(
        settings, "automation_webhook_endpoints", {"crm_sync": "https://crm.example/hook"}
    )
    event = await bed.run_action(
        "webhook_out", {"endpoint_key": "crm_sync", "payload_keys": ["contact.email", "case.uid"]}
    )
    assert event is not None and event.signal is StepSignal.COMPLETED

    queued = await bed.rows(
        "SELECT event_type, payload, status, attempts FROM flows.outbox WHERE tenant_id = :t"
    )
    assert len(queued) == 1
    assert queued[0]["event_type"] == "automation.webhook"
    assert (queued[0]["status"], queued[0]["attempts"]) == ("queued", 0)
    payload = queued[0]["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["url"] == "https://crm.example/hook"
    assert payload["body"]["contact.email"].startswith("ada.")
    assert payload["body"]["case.uid"] == bed.case_uid

    events = await bed.events()
    assert [e["event_type"] for e in events] == ["action.automations-webhook-out"]


@pytest.mark.asyncio
async def test_webhook_out_refuses_an_endpoint_this_deployment_does_not_know(
    bed: _Bed, monkeypatch
) -> None:
    """The control: an author names a KEY, and a key an operator has not configured is refused —
    never guessed, never turned into a URL."""
    from app.config import settings

    monkeypatch.setattr(settings, "automation_webhook_endpoints", {})
    event = await bed.run_action("webhook_out", {"endpoint_key": "wherever"})
    assert event is not None and event.signal is StepSignal.FAILED
    assert "not a webhook endpoint" in (event.reason or "")
    assert await bed.rows("SELECT id FROM flows.outbox WHERE tenant_id = :t") == []


# ---------------------------------------------------------------------------------------------
# lookup — the inbound half, inside a journey
# ---------------------------------------------------------------------------------------------
#
# The controls of `app/flows/lookup.py` are proved in `test_flows_lookup.py`, without a database.
# What needs one is the half that only exists inside a case: the answer lands in CASE MEMORY where
# the next branch reads it, a refusal leaves the case retryable with nothing half-written, and the
# audit event names the endpoint and the memory keys and NEVER the values.


def _serve_lookup(handler: Any, monkeypatch: Any) -> list[Any]:
    """Replace the socket under ``lookup.fetch``, keeping the real client's policy."""
    import httpx

    from app.flows import lookup as lookup_mod

    seen: list[Any] = []

    def _record(request: Any) -> Any:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        lookup_mod,
        "_build_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(_record),
            timeout=httpx.Timeout(lookup_mod.TIMEOUT_S, connect=lookup_mod.CONNECT_TIMEOUT_S),
            follow_redirects=False,
        ),
    )
    return seen


@pytest.mark.asyncio
async def test_lookup_remembers_the_answer_where_a_branch_will_read_it(
    bed: _Bed, monkeypatch
) -> None:
    """The whole purpose: what the endpoint said is in ``flows.case_memory`` under the author's own
    names, sourced ``action`` and stamped with the node that wrote it — the shape
    ``memory.context`` hands a branch and an interpolated message alike."""
    import httpx

    from app.config import settings
    from app.flows import memory

    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})
    # Something already remembered, to prove a lookup can ASK with the journey's own memory.
    async with bed.session() as s:
        await memory.write(
            s,
            tenant_id=bed.tenant_id,
            case_id=bed.case_id,
            key="policy_number",
            value="P-1234",
            source="start",
        )

    seen = _serve_lookup(
        lambda _r: httpx.Response(200, json={"data": {"risk": {"band": "high"}}, "score": 71}),
        monkeypatch,
    )
    event = await bed.run_action(
        "lookup",
        {
            "endpoint_key": "risk",
            "request_mapping": {"email": "contact.email", "policy": "memory.policy_number"},
            "response_mapping": {"risk_band": "data.risk.band", "risk_score": "score"},
        },
        step_key="risk_lookup",
    )
    assert event is not None and event.signal is StepSignal.COMPLETED

    body = json.loads(seen[0].content)
    assert body["policy"] == "P-1234"  # memory.* resolved by the same grammar as contact.*
    assert body["email"].startswith("ada.")

    async with bed.session() as s:
        assert await memory.read(s, bed.case_id) == {
            "policy_number": "P-1234",
            "risk_band": "high",
            "risk_score": 71,
        }
    rows = await bed.rows(
        "SELECT key, source, node_id FROM flows.case_memory "
        "WHERE case_id = :c AND key = 'risk_band'",
        {"c": bed.case_id},
    )
    assert (rows[0]["source"], rows[0]["node_id"]) == ("action", "risk_lookup")


@pytest.mark.asyncio
async def test_the_lookup_event_names_the_endpoint_and_the_keys_but_never_the_values(
    bed: _Bed, monkeypatch
) -> None:
    """A lookup's answer can be the client's identity data, and ``audit.actions`` is a stream the
    firm's own staff browse. The event is evidence that a lookup ran and what it wrote INTO —
    never what it wrote."""
    import httpx

    from app.config import settings

    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})
    _serve_lookup(
        lambda _r: httpx.Response(200, json={"nino": "QQ123456C", "band": "high"}), monkeypatch
    )
    await bed.run_action(
        "lookup",
        {
            "endpoint_key": "risk",
            "response_mapping": {"national_insurance": "nino", "risk_band": "band"},
        },
    )
    events = await bed.events()
    assert [e["event_type"] for e in events] == ["action.automations-lookup"]
    subject = events[0]["metadata"]["subject"]
    assert subject["endpoint_key"] == "risk"
    assert sorted(subject["memory_keys"]) == ["national_insurance", "risk_band"]
    serialised = json.dumps(events[0]["metadata"])
    assert "QQ123456C" not in serialised and "high" not in serialised


@pytest.mark.asyncio
async def test_lookup_refuses_an_endpoint_withdrawn_since_the_flow_was_published(
    bed: _Bed, monkeypatch
) -> None:
    """A published flow outlives the deploy that allowlisted its endpoint. Re-resolved from the
    LIVE configuration at every dispatch — the discipline ``dispatcher._handle_webhook`` applies to
    a queued row, applied here to a stored definition."""
    from app.config import settings

    monkeypatch.setattr(settings, "automation_lookup_endpoints", {})
    seen = _serve_lookup(
        lambda _r: pytest.fail("a withdrawn endpoint must not be called"), monkeypatch
    )
    event = await bed.run_action(
        "lookup", {"endpoint_key": "risk", "response_mapping": {"band": "band"}}
    )
    assert event is not None and event.signal is StepSignal.FAILED
    assert "not a lookup endpoint this deployment knows" in (event.reason or "")
    assert seen == []
    assert (
        await bed.rows("SELECT key FROM flows.case_memory WHERE case_id = :c", {"c": bed.case_id})
        == []
    )


@pytest.mark.asyncio
async def test_an_answer_too_large_to_remember_refuses_and_writes_nothing(
    bed: _Bed, monkeypatch
) -> None:
    """The memory caps are the case's, not the lookup's, and they REFUSE rather than truncate. A
    lookup takes that strictly: its values are its whole output and a branch is about to test them,
    so a half-applied answer would be a journey routing on half a fact. Both keys or neither."""
    import httpx

    from app.config import settings
    from app.flows import memory

    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})
    _serve_lookup(
        lambda _r: httpx.Response(
            200, json={"band": "high", "dossier": "x" * (memory.MAX_VALUE_BYTES + 10)}
        ),
        monkeypatch,
    )
    event = await bed.run_action(
        "lookup",
        {
            "endpoint_key": "risk",
            "response_mapping": {"risk_band": "band", "dossier": "dossier"},
        },
    )
    assert event is not None and event.signal is StepSignal.FAILED
    assert "remember" in (event.reason or "")
    async with bed.session() as s:
        assert await memory.read(s, bed.case_id) == {}
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_a_slow_endpoint_parks_the_case_retryable_rather_than_holding_it(
    bed: _Bed, monkeypatch
) -> None:
    """THE synchronous-call decision's safety net. A lookup blocks because the next node needs its
    answer; when the far end is down that must cost a bounded wait and a BLOCKED case an operator
    can re-run, never a case wedged for ever and never the latched terminal ``failed``."""
    import asyncio

    import httpx

    from app.config import settings
    from app.flows import lookup as lookup_mod

    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})
    monkeypatch.setattr(lookup_mod, "TIMEOUT_S", 0.05)

    async def _hang(_request: Any) -> Any:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"band": "high"})

    _serve_lookup(_hang, monkeypatch)
    event = await bed.run_action(
        "lookup",
        {"endpoint_key": "risk", "response_mapping": {"risk_band": "band"}},
        step_key="risk_lookup",
    )
    assert event is not None and event.signal is StepSignal.FAILED
    assert "did not answer within" in (event.reason or "")

    async with bed.session() as s:
        await orchestrator.apply_step_event(s, bed.tenant_id, event)
    case = (await bed.rows("SELECT status FROM flows.cases WHERE id = :c", {"c": bed.case_id}))[0]
    assert case["status"] == "blocked", "a lookup that could not run must be retryable"


# ---------------------------------------------------------------------------------------------
# Delivery: retries and dead letters come from the outbox, free
# ---------------------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, body: bytes = b"{}") -> None:
        self.status_code = status_code
        self.is_redirect = 300 <= status_code < 400
        self._body = body

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        yield self._body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FakeHttpx:
    """Just enough of the shared client for the dispatcher: records the POST, returns a canned
    status. Stands in for ``lookup._build_client`` — the ONE factory both the inbound lookup and
    the outbound webhook go through, so the webhook inherits the same ``follow_redirects=False``
    rather than the httpx default — and it streams, because ``_handle_webhook`` reads the reply
    under a byte cap instead of letting ``client.post`` buffer whatever the far end sends."""

    def __init__(self, status_code: int, body: bytes = b"{}") -> None:
        self.status_code = status_code
        self.body = body
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, **_kw: Any) -> _FakeHttpx:
        return self

    async def __aenter__(self) -> _FakeHttpx:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def stream(self, _method: str, url: str, json: dict[str, Any] | None = None) -> _FakeResponse:
        self.calls.append((url, json or {}))
        return _FakeResponse(self.status_code, self.body)


@pytest.mark.asyncio
async def test_a_queued_webhook_is_delivered_and_a_failure_retries(bed: _Bed, monkeypatch) -> None:
    """The reason ``webhook_out`` rides the outbox at all: a 500 from the far end costs a retry on
    backoff, not a lost automation and not a transaction held open on someone else's server."""
    from datetime import UTC, datetime

    from app.config import settings
    from app.flows import dispatcher

    monkeypatch.setattr(
        settings, "automation_webhook_endpoints", {"crm_sync": "https://crm.example/hook"}
    )
    await bed.run_action("webhook_out", {"endpoint_key": "crm_sync", "payload_keys": ["case.uid"]})

    failing = _FakeHttpx(503)
    monkeypatch.setattr(dispatcher.lookup, "_build_client", failing)
    async with bed.session() as s:
        await dispatcher.dispatch_due(s, now=datetime.now(UTC), limit=20)
    row = (
        await bed.rows("SELECT status, attempts, last_error FROM flows.outbox WHERE tenant_id = :t")
    )[0]
    assert (row["status"], row["attempts"]) == ("queued", 1)  # retrying, not lost
    assert "503" in str(row["last_error"])
    assert any(call[0] == "https://crm.example/hook" for call in failing.calls)

    ok = _FakeHttpx(200)
    monkeypatch.setattr(dispatcher.lookup, "_build_client", ok)
    async with bed.session() as s:
        # The retry is due in a minute; ask the drain for rows due later so we exercise delivery.
        await dispatcher.dispatch_due(s, now=datetime(2099, 1, 1, tzinfo=UTC), limit=20)
    row = (await bed.rows("SELECT status, attempts FROM flows.outbox WHERE tenant_id = :t"))[0]
    assert (row["status"], row["attempts"]) == ("delivered", 2)
    # The drain is not tenant-scoped (it is the worker's global queue), so find OUR delivery.
    assert any(call[1]["data"] == {"case.uid": bed.case_uid} for call in ok.calls)


@pytest.mark.asyncio
async def test_a_withdrawn_endpoint_dead_letters_rather_than_retrying(
    bed: _Bed, monkeypatch
) -> None:
    """A queued row can outlive the deploy that allowlisted its endpoint. Retrying it for ever
    would keep calling something an operator deliberately removed, so it dead-letters at once."""
    from datetime import UTC, datetime

    from app.config import settings
    from app.flows import dispatcher

    monkeypatch.setattr(
        settings, "automation_webhook_endpoints", {"crm_sync": "https://crm.example/hook"}
    )
    await bed.run_action("webhook_out", {"endpoint_key": "crm_sync"})
    monkeypatch.setattr(settings, "automation_webhook_endpoints", {})
    async with bed.session() as s:
        await dispatcher.dispatch_due(s, now=datetime.now(UTC), limit=5)
    row = (await bed.rows("SELECT status, last_error FROM flows.outbox WHERE tenant_id = :t"))[0]
    assert row["status"] == "dead_letter"
    assert "no longer configured" in str(row["last_error"])


# ---------------------------------------------------------------------------------------------
# Failure: visible, rolled back, retryable
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_action_rolls_back_its_savepoint_and_writes_no_event(bed: _Bed) -> None:
    """``generate_document`` naming a template with no published version. The service refuses, and
    the savepoint means the refusal cannot leave a half-made document or a doomed render job."""
    template_uid = await bed.seed_template(published=False)
    event = await bed.run_action("generate_document", {"template_uid": template_uid})
    assert event is not None and event.signal is StepSignal.FAILED
    assert "no published version" in (event.reason or "")
    assert await bed.rows("SELECT uid FROM documents.documents WHERE tenant_id = :t") == []
    assert await bed.rows("SELECT id FROM documents.render_jobs WHERE tenant_id = :t") == []
    # Effects, not attempts: nothing happened, so nothing claims it did.
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_a_faulting_action_fails_the_step_without_poisoning_the_transaction(
    bed: _Bed, monkeypatch
) -> None:
    """A FAULT (not a considered refusal) — a constraint nobody anticipated, a provider client
    exploding. It must fail the step, not the caller's transaction: the orchestrator is mid-pass
    over a case and the other steps' bookkeeping has to survive."""
    from app.flows import effects

    async def _explode(*_a: object, **_kw: object) -> None:
        raise RuntimeError("the documents service fell over")

    monkeypatch.setattr(effects, "execute", _explode)
    step_type = handlers.action_step_type("apply_tag")
    await bed.add_step("boom", step_type)
    handler = handlers.get_handler(step_type)
    assert handler is not None
    async with bed.session() as s:
        event = await handler.instantiate(bed.ctx("boom", {"tag": "vip"}, s))
        # The session is still usable after the savepoint rollback — the point of the savepoint.
        assert (await s.execute(text("SELECT 1"))).scalar_one() == 1
    assert event is not None and event.signal is StepSignal.FAILED
    assert "fell over" in (event.reason or "")
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_a_failed_action_blocks_the_case_and_can_be_retried(bed: _Bed) -> None:
    """THE FAILURE-STATE DECISION, end to end.

    The step latches ``failed``; the case derives to ``blocked`` — deliberately not the terminal
    ``failed``, which is latched and would make the case unretryable for ever. ``retry_step``
    accepts exactly this state, re-runs the action, and a now-publishable template completes it.
    """
    template_uid = await bed.seed_template(published=False)
    definition = {
        "nodes": [
            {
                "id": "render",
                "kind": "action",
                "type": "generate_document",
                "title": "Render the pack",
                "config": {"template_uid": template_uid},
            }
        ]
    }
    async with bed.session() as s:
        await s.execute(
            text("UPDATE flows.flow_versions SET definition = CAST(:d AS jsonb) WHERE id = :i"),
            {"d": json.dumps(_validated(definition)), "i": bed.flow_version_id},
        )

    event = await bed.run_action(
        "generate_document", {"template_uid": template_uid}, step_key="render"
    )
    assert event is not None and event.signal is StepSignal.FAILED
    async with bed.session() as s:
        await orchestrator.apply_step_event(s, bed.tenant_id, event)

    step = (
        await bed.rows(
            "SELECT status, data FROM flows.case_steps WHERE case_id = :c AND step_key = 'render'",
            {"c": bed.case_id},
        )
    )[0]
    assert step["status"] == "failed"
    case = (await bed.rows("SELECT status FROM flows.cases WHERE id = :c", {"c": bed.case_id}))[0]
    assert case["status"] == "blocked", "a failed action must park the case where a human sees it"

    # Publish the template — the transient cause is fixed — and retry the step.
    async with bed.session() as s:
        await s.execute(
            text(
                "UPDATE documents.templates t SET current_published_version_id = "
                "  (SELECT v.id FROM documents.template_versions v WHERE v.template_id = t.id) "
                "WHERE t.uid = :u"
            ),
            {"u": template_uid},
        )
    async with bed.session() as s:
        assert await orchestrator.retry_step(
            s, tenant_id=bed.tenant_id, case_id=bed.case_id, step_key="render"
        )
    step = (
        await bed.rows(
            "SELECT status FROM flows.case_steps WHERE case_id = :c AND step_key = 'render'",
            {"c": bed.case_id},
        )
    )[0]
    assert step["status"] == "fulfilled"
    assert len(await bed.rows("SELECT uid FROM documents.documents WHERE tenant_id = :t")) == 1


def _validated(definition: dict[str, Any]) -> dict[str, Any]:
    from app.flows import definitions

    return definitions.validate_definition(definition)


# ---------------------------------------------------------------------------------------------
# Publish preflight — a flow that could never run must not publish
# ---------------------------------------------------------------------------------------------


async def _publish(bed: _Bed, definition: dict[str, Any]) -> str | None:
    """Publish a draft version through the REAL endpoint. Returns the 422's detail, or None.

    Through the endpoint rather than the helper because the point of the check is that publish
    RUNS it: a preflight nothing calls is not a preflight. The session is ``authenticated`` with
    this firm's claims, exactly as ``tenant_session`` builds it.
    """
    import json as _json

    from fastapi import HTTPException

    from app.auth import Principal
    from app.routers import flows as flows_router

    vid = f"flv_{uuid.uuid4().hex[:8]}"
    async with bed.session() as s:
        await s.execute(
            text(
                "INSERT INTO flows.flow_versions (uid, tenant_id, flow_id, version, status, "
                "  definition) VALUES (:u, :t, :f, :v, 'draft', CAST(:d AS jsonb))"
            ),
            {
                "u": vid,
                "t": bed.tenant_id,
                "f": bed.flow_id,
                "v": 100 + uuid.uuid4().int % 1000,
                "d": _json.dumps(_validated(definition)),
            },
        )
    claims = {
        "sub": f"wos_{bed.nonce}",
        "role": "adviser",
        # 0107: lineage is a whitelist on the portal claim — no portal, no reach.
        "portal": "platform",
        "permissions": ["flows:write"],
        "user_uuid": bed.user_id,
        "tenant_id": bed.tenant_id,
        "tenant_ids": [bed.tenant_id],
        "contact_ids": [],
        "account_ids": [],
    }
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE authenticated"))
        await s.execute(
            text("SELECT set_config('request.jwt.claims', :c, true)"), {"c": _json.dumps(claims)}
        )
        try:
            await flows_router.publish_flow_version(
                uid=f"flw_{bed.nonce}",
                vid=vid,
                principal=Principal(
                    user_id="x", organisation_id="x", role="adviser", permissions=["flows:write"]
                ),
                session=s,
            )
        except HTTPException as exc:
            return str(exc.detail)
    return None


@pytest.mark.asyncio
async def test_publish_refuses_an_action_no_role_in_the_firm_could_perform(bed: _Bed) -> None:
    """The permission half B1 left unwired. Nobody in this firm holds ``contacts:write``, so an
    automation that tags a client is asserting an authority the firm has granted to no one — and
    the person who can fix that is the author looking at the screen, not an operator at 3am."""
    definition = {
        "nodes": [
            {
                "id": "tag",
                "kind": "action",
                "type": "apply_tag",
                "title": "Tag as onboarded",
                "config": {"tag": "onboarded"},
            }
        ]
    }
    detail = await _publish(bed, definition)
    assert detail is not None
    assert "no role in this firm holds 'contacts:write'" in detail

    await bed.grant("contacts:write")
    assert await _publish(bed, definition) is None


@pytest.mark.asyncio
async def test_publish_refuses_an_action_whose_reference_is_missing(bed: _Bed) -> None:
    """The static half: a template that is not published cannot be rendered, ever."""
    await bed.grant("documents:write")
    template_uid = await bed.seed_template(published=False)
    detail = await _publish(
        bed,
        {
            "nodes": [
                {
                    "id": "pack",
                    "kind": "action",
                    "type": "generate_document",
                    "title": "Render the pack",
                    "config": {"template_uid": template_uid},
                }
            ]
        },
    )
    assert detail is not None and "no published version" in detail


@pytest.mark.asyncio
async def test_publish_refuses_a_lookup_naming_an_endpoint_this_deployment_lacks(
    bed: _Bed, monkeypatch
) -> None:
    """An author cannot add an endpoint and an operator can, so the refusal has to happen where the
    author is and has to say whose job the fix is. The alternative is a flow that publishes clean
    and then blocks its first client at whatever hour they reach the node."""
    from app.config import settings

    await bed.grant("automations:write")
    definition = {
        "nodes": [
            {
                "id": "risk",
                "kind": "action",
                "type": "lookup",
                "title": "Ask the risk engine",
                "config": {
                    "endpoint_key": "risk_engine",
                    "request_mapping": {"email": "contact.email"},
                    "response_mapping": {"risk_band": "data.band"},
                },
            }
        ]
    }
    monkeypatch.setattr(settings, "automation_lookup_endpoints", {})
    detail = await _publish(bed, definition)
    assert detail is not None and "AUTOMATION_LOOKUP_ENDPOINTS" in detail

    monkeypatch.setattr(
        settings, "automation_lookup_endpoints", {"risk_engine": "https://r.example/x"}
    )
    assert await _publish(bed, definition) is None


@pytest.mark.asyncio
async def test_publish_refuses_a_lookup_that_would_remember_nothing(bed: _Bed, monkeypatch) -> None:
    """A lookup with no response mapping calls a third party and throws the answer away — and the
    branch downstream tests a memory key nothing ever sets. Silent at runtime, so it is caught
    here."""
    from app.config import settings

    await bed.grant("automations:write")
    monkeypatch.setattr(
        settings, "automation_lookup_endpoints", {"risk_engine": "https://r.example/x"}
    )
    detail = await _publish(
        bed,
        {
            "nodes": [
                {
                    "id": "risk",
                    "kind": "action",
                    "type": "lookup",
                    "title": "Ask the risk engine",
                    "config": {"endpoint_key": "risk_engine"},
                }
            ]
        },
    )
    assert detail is not None and "nothing it fetched would be remembered" in detail


@pytest.mark.asyncio
async def test_publish_refuses_a_start_flow_naming_a_draft_or_missing_flow(bed: _Bed) -> None:
    """``graph_issues`` can only see that the code is slug-shaped. Whether it names a flow that
    exists and is PUBLISHED needs the database — and a tail call into a draft is a journey that
    ends by going nowhere, at whatever hour the client reaches it."""

    def _definition(code: str) -> dict[str, Any]:
        return {
            "nodes": [
                {
                    "id": "task",
                    "type": "task",
                    "title": "Say hello",
                    "config": {},
                    "next": "onwards",
                },
                {
                    "id": "onwards",
                    "kind": "start_flow",
                    "title": "Then the review",
                    "config": {"flow_code": code},
                },
            ]
        }

    detail = await _publish(bed, _definition("nowhere"))
    assert detail is not None and "not a flow in this firm" in detail

    draft_code = f"review_{bed.nonce}"
    async with bed.session() as s:
        await s.execute(
            text(
                "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                "VALUES (:u, :t, :c, 'Annual review', 'draft')"
            ),
            {"u": f"flw_d_{bed.nonce}", "t": bed.tenant_id, "c": draft_code},
        )
    detail = await _publish(bed, _definition(draft_code))
    assert detail is not None and "not published" in detail

    async with bed.session() as s:
        await s.execute(
            text("UPDATE flows.flows SET status = 'published' WHERE code = :c AND tenant_id = :t"),
            {"c": draft_code, "t": bed.tenant_id},
        )
    assert await _publish(bed, _definition(draft_code)) is None


# ---------------------------------------------------------------------------------------------
# The collision: one name, two behaviours, decided by `kind`
# ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_document_step_and_action_nodes_coexist_and_differ(bed: _Bed) -> None:
    """A ``generate_document`` STEP and a ``generate_document`` ACTION in the same flow.

    The step is staff work whose completion IS the rendered document, so it queues the render and
    reports WAITING for the worker's callback. The action is unattended: it queues the render and
    completes, and the journey moves on. Same name, different node kind, different behaviour —
    which is exactly what a precedence rule ("step types win") made impossible to author.
    """
    template_uid = await bed.seed_template()
    definition = _validated(
        {
            "nodes": [
                {
                    "id": "staff_pack",
                    "type": "generate_document",
                    "title": "Suitability report",
                    "config": {"template_uid": template_uid},
                    "next": "auto_pack",
                },
                {
                    "id": "auto_pack",
                    "kind": "action",
                    "type": "generate_document",
                    "title": "Welcome pack",
                    "config": {"template_uid": template_uid},
                },
            ]
        }
    )
    by_id = {str(s["id"]): s for s in definition["steps"]}  # type: ignore[index]
    assert by_id["staff_pack"]["type"] == "generate_document"
    assert by_id["auto_pack"]["type"] == "action:generate_document"

    step_event = await bed.run_planned(by_id["staff_pack"])
    action_event = await bed.run_planned(by_id["auto_pack"])

    assert step_event is not None and step_event.signal is StepSignal.WAITING
    assert action_event is not None and action_event.signal is StepSignal.COMPLETED

    # Two documents, two render jobs — both went through a real render path.
    assert len(await bed.rows("SELECT uid FROM documents.documents WHERE tenant_id = :t")) == 2
    # …but only the STAFF step recorded a document to wait for, and only the ACTION emitted an
    # action event: the step's document is the staff member's work, the action's is the engine's.
    steps = await bed.rows(
        "SELECT step_key, step_type, data FROM flows.case_steps WHERE case_id = :c ORDER BY id",
        {"c": bed.case_id},
    )
    waiting = next(s for s in steps if s["step_key"] == "staff_pack")
    data = waiting["data"] if isinstance(waiting["data"], dict) else json.loads(waiting["data"])
    assert "document_id" in data
    assert [e["event_type"] for e in await bed.events()] == ["document.generated"]
