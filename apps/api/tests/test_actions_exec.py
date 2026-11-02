"""The action executor, against a real Postgres.

Three things are being proved here, and only the first is "the code works":

1. **Each action goes through the real service**, so the business rules on the human path apply to
   the automated one — an assignee must be a member of this firm, an adviser must belong to it, a
   terminal case stays terminal. The tests assert on the SERVICE's observable effects (the task
   row, the adviser link) rather than mocking it, because a mock would pass just as happily
   against a parallel INSERT, which is the thing this design exists to prevent.
2. **The audit event lands, naming the automation**, in the right tenant, carrying the exact chain
   marker the consumer's self-trigger guard reads. That guard is the most important safety
   property in the engine and it reads a dict written by this module; a test that only checked
   "an event exists" would let the two drift apart silently.
3. **A failure rolls back only its own savepoint.** The whole point of the consumer wrapping a
   dispatch in ``begin_nested`` is that one bad automation cannot take the batch with it — proved
   by running a failing action inside a savepoint and finding the batch's other work intact.

The bed is per-test-nonce and never truncates (``test_automations``' reasoning): another suite's
leftovers cannot make an assertion pass or fail, because everything is scoped to a tenant created
moments earlier.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import tags as tags_mod
from app.config import settings
from app.email import document as render_email
from app.flows import effects, triggers
from app.flows import memory as memory_mod
from app.flows.triggers import AutomationContext
from app.render import jinja_engine
from tests.conftest import assert_public_id

Maker = async_sessionmaker[AsyncSession]


# ---- Fixtures -------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def maker(db: dict[str, str]) -> AsyncIterator[Maker]:
    engine = create_async_engine(db["app"])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class _Bed:
    """One firm, one client, one staff user — enough for every action to have somewhere to land."""

    def __init__(self, maker: Maker, nonce: str, ids: dict[str, Any]) -> None:
        self.maker = maker
        self.nonce = nonce
        self.tenant_id = int(ids["tenant_id"])
        self.contact_id = int(ids["contact_id"])
        self.user_id = str(ids["user_id"])
        self.role_id = int(ids["role_id"])

    def ctx(self, **overrides: Any) -> AutomationContext:
        """A firing that names the bed's client, which is what most actions need."""
        base: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "automation_id": 1,
            "automation_uid": f"aut_{self.nonce}",
            "automation_name": "Chase the client",
            "trigger_key": "contact.bank_details_changed",
            "event_id": 4242,
            "event_type": "client.bank_account_updated",
            "subject_kind": "contact",
            "subject_id": self.contact_id,
            "case_uid": None,
            "chain_depth": 0,
            "actor_id": None,
            "config": {},
            "facts": {},
        }
        base.update(overrides)
        return AutomationContext(**base)

    async def run(self, spec: dict[str, Any], *, ctx: AutomationContext | None = None) -> Any:
        """Dispatch one action the way the consumer does — service_role, inside a savepoint."""
        target = triggers.target_for("action")
        assert target is not None, "effects must have registered the 'action' target"
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            async with s.begin_nested():
                return await target.run(s, ctx or self.ctx(), spec)

    async def events(self) -> list[dict[str, Any]]:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT event_type, tenant_id, target_tenant_id, actor_id, actor_sub, "
                            "  method, route, status, path_params, metadata "
                            "FROM audit.actions WHERE tenant_id = :t ORDER BY id"
                        ),
                        {"t": self.tenant_id},
                    )
                )
                .mappings()
                .all()
            )
            return [dict(r) for r in rows]

    async def tasks(self) -> list[dict[str, Any]]:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            rows = (
                (
                    await s.execute(
                        text(
                            "SELECT uid, title, description, category, priority, source_kind, "
                            "  source_ref, assignee_kind, assignee_role_id, assignee_user_id, "
                            "  assignee_contact_id, subject_type, subject_id, due_date "
                            "FROM tasks.tasks WHERE tenant_id = :t ORDER BY id"
                        ),
                        {"t": self.tenant_id},
                    )
                )
                .mappings()
                .all()
            )
            return [dict(r) for r in rows]

    async def contact_tags(self) -> list[str]:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            return await tags_mod.list_for(
                s, target="contact", target_id=self.contact_id, tenant_id=self.tenant_id
            )

    async def grant(self, *slugs: str) -> None:
        """Give the bed's role these permissions — what ``preflight`` checks the firm has."""
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            for slug in slugs:
                await s.execute(
                    text(
                        "INSERT INTO auth.role_permissions (role_id, permission_id) "
                        "SELECT :r, id FROM auth.permissions WHERE slug = :s "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"r": self.role_id, "s": slug},
                )

    async def seed_template(
        self,
        *,
        code: str,
        html: str = "<p>Dear {{ contact.name || client }}</p>",
        published: bool = True,
        subject: str | None = None,
        preheader: str | None = None,
    ) -> None:
        """An EMAIL template at its published version — what `send_email` resolves.

        It seeded `documents.templates` until the action was pointed at `email.templates`, which is
        the surface a firm actually authors emails in; a template written in the editor was
        unreachable from a journey until then. The subject is a COLUMN on the version here, not a
        schema key.
        """
        schema: dict[str, Any] = {"html": html, "css": "", "source": html}
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            template_id = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO email.templates (tenant_id, uid, code, name, category, "
                            "  created_by) "
                            "VALUES (:t, :u, :c, :n, 'notification', CAST(:by AS uuid)) "
                            "RETURNING id"
                        ),
                        {
                            "t": self.tenant_id,
                            "u": f"eml{self.nonce}{code}"[:12],
                            "c": code,
                            "n": "Chaser email",
                            "by": self.user_id,
                        },
                    )
                ).scalar_one()
            )
            version_id = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO email.template_versions (uid, tenant_id, template_id, "
                            "  version_number, status, subject, preheader, schema, created_by) "
                            "VALUES (:u, :t, :tid, 1, :st, :subj, :pre, CAST(:sc AS jsonb), "
                            "  CAST(:by AS uuid)) RETURNING id"
                        ),
                        {
                            "u": f"ver{self.nonce}{code}"[:12],
                            "t": self.tenant_id,
                            "tid": template_id,
                            "st": "published" if published else "draft",
                            "subj": subject or "",
                            "pre": preheader or "",
                            "sc": json.dumps(schema),
                            "by": self.user_id,
                        },
                    )
                ).scalar_one()
            )
            if published:
                await s.execute(
                    text(
                        "UPDATE email.templates SET current_published_version_id = :v WHERE id = :t"
                    ),
                    {"v": version_id, "t": template_id},
                )

    async def seed_case(self, *, status: str = "active") -> str:
        """A live case for the bed's client, with an execution behind it."""
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            flow_id = int(
                (
                    await s.execute(
                        text(
                            "INSERT INTO flows.flows (uid, tenant_id, code, name, status) "
                            "VALUES (:u, :t, :c, 'Welcome', 'published') RETURNING id"
                        ),
                        {
                            "u": f"flw_{self.nonce}",
                            "t": self.tenant_id,
                            "c": f"welcome_{self.nonce}",
                        },
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
                            "u": f"flv_{self.nonce}",
                            "t": self.tenant_id,
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
                            "INSERT INTO flows.executions (uid, tenant_id, flow_id, "
                            "  flow_version_id, status) VALUES (:u, :t, :f, :v, 'active') "
                            "RETURNING id"
                        ),
                        {
                            "u": f"fex_{self.nonce}",
                            "t": self.tenant_id,
                            "f": flow_id,
                            "v": version_id,
                        },
                    )
                ).scalar_one()
            )
            uid = f"cas_{self.nonce}"
            await s.execute(
                text(
                    "INSERT INTO flows.cases (uid, tenant_id, flow_id, flow_version_id, "
                    "  subject_contact_id, execution_id, status) "
                    "VALUES (:u, :t, :f, :v, :c, :e, :st)"
                ),
                {
                    "u": uid,
                    "t": self.tenant_id,
                    "f": flow_id,
                    "v": version_id,
                    "c": self.contact_id,
                    "e": execution_id,
                    "st": status,
                },
            )
        return uid

    async def remember(self, case_uid: str, key: str, value: Any) -> None:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            case_id = int(
                (
                    await s.execute(
                        text("SELECT id FROM flows.cases WHERE uid = :u"), {"u": case_uid}
                    )
                ).scalar_one()
            )
            await memory_mod.write(
                s,
                tenant_id=self.tenant_id,
                case_id=case_id,
                key=key,
                value=value,
                source="answers",
            )

    async def case_status(self, uid: str) -> str:
        async with self.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            return str(
                (
                    await s.execute(
                        text("SELECT status FROM flows.cases WHERE uid = :u"), {"u": uid}
                    )
                ).scalar_one()
            )


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
                    {"u": f"axe_{nonce}", "a": f"axe-{nonce}", "n": f"Executor {nonce}"},
                )
            ).scalar_one()
        )
        contact_id = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO crm.contacts (tenant_id, uid, name, type, service_type, "
                        "  email) VALUES (:t, :u, 'Ada Client', 'individual', 'advised', :e) "
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
                        "  first_name, last_name, type, is_adviser) "
                        # This login is the adviser these flows assign, and `assign_adviser` now
                        # refuses a login the firm has not said advises — the check that stopped a
                        # client's own login being made an adviser. `is_adviser` is a column
                        # (0125) rather than a `type` member, and it implies staff.
                        "VALUES (:uid, :t, :w, :e, 'Grace', 'Adviser', ARRAY['staff'], true)"
                        " RETURNING id"
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
        # tasks_service._validate_assignee needs an ACTIVE membership before it points at a user.
        await s.execute(
            text(
                "INSERT INTO auth.organisation_memberships (user_id, tenant_id, portal, role_id, "
                "  status) VALUES (:u, :t, 'platform', :r, 'active')"
            ),
            {"u": user_id, "t": tenant_id, "r": role_id},
        )
    return _Bed(
        maker,
        nonce,
        {
            "tenant_id": tenant_id,
            "contact_id": contact_id,
            "user_id": user_id,
            "role_id": role_id,
        },
    )


# ---- The contract mirror --------------------------------------------------------------------


@pytest.mark.pkgs_ui_pin
def test_the_action_registry_mirrors_the_contract() -> None:
    """``ACTIONS`` is the Python mirror of flow-contract.json's ``actionTypes``. The API container
    ships without pkgs/ui, so this pin test is the only thing keeping the two honest — the same
    arrangement as the trigger registry and block-contract.json."""
    from pathlib import Path

    contract = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "pkgs/ui/src/lib/components/book/journeys/flow-contract.json"
        ).read_text(encoding="utf-8")
    )
    declared = contract["actionTypes"]
    assert set(effects.ACTIONS) == set(declared), (
        "the action registry and the contract disagree about which actions exist"
    )
    for name, spec in declared.items():
        assert effects.ACTIONS[name].permission == spec["permission"], (
            f"{name}: the executor and the contract disagree about the permission it needs"
        )


def test_every_implemented_action_has_a_handler_and_vice_versa() -> None:
    implemented = {t for t, s in effects.ACTIONS.items() if s.implemented}
    assert implemented == set(effects.HANDLERS)
    # The whole vocabulary. `implemented` stays as the seam for an action declared with no handler.
    assert implemented == {
        "apply_tag",
        "remove_tag",
        "raise_task",
        "send_email",
        "assign_adviser",
        "set_case_status",
        "request_document",
        "generate_document",
        "send_document",
        "webhook_out",
        "lookup",
        "classify",
        "draft_message",
    }


@pytest.mark.pkgs_ui_pin
def test_registering_an_action_outside_the_contract_is_refused() -> None:
    """The Phase B seam has a gate: a handler for a type the contract does not declare would be an
    action the builder can never author and the TS side has never heard of."""
    with pytest.raises(KeyError):
        effects.register_action("teleport_client", lambda *a: None)  # type: ignore[arg-type]


def test_the_executor_is_registered_as_the_action_dispatch_kind() -> None:
    assert isinstance(triggers.target_for("action"), effects.ActionTarget)


def test_tag_event_types_are_the_future_routes_operation_ids() -> None:
    """Tags have no human route yet, so their event types are the ids such a route WILL generate
    ('action.' + '{tag}-{route name}'). Pinned so that when the staff tagging API lands, manual and
    automated tagging stay ONE event type rather than two half-histories."""
    assert effects.ACTIONS["apply_tag"].event_type == "action.tags-apply-tag"
    assert effects.ACTIONS["remove_tag"].event_type == "action.tags-remove-tag"


def test_the_config_reader_accepts_both_authored_shapes() -> None:
    """Nested and flattened both work — the seam with an API written in parallel."""
    assert effects.action_config({"type": "apply_tag", "tag": "vip"}) == {"tag": "vip"}
    assert effects.action_config({"type": "apply_tag", "config": {"tag": "vip"}}) == {"tag": "vip"}
    assert effects.action_config(
        {"type": "x", "tag": "old", "target": "case", "config": {"tag": "new"}}
    ) == {"tag": "new", "target": "case"}


# ---- apply_tag / remove_tag -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_tag_tags_the_client_and_emits_the_event(bed: _Bed) -> None:
    result = await bed.run({"type": "apply_tag", "tag": "VIP"})

    assert result.ok
    assert await bed.contact_tags() == ["vip"]  # normalised by the service
    events = await bed.events()
    assert [e["event_type"] for e in events] == ["action.tags-apply-tag"]
    assert events[0]["metadata"]["subject"] == {"tag": "vip", "target": "contact"}


@pytest.mark.asyncio
async def test_apply_tag_is_idempotent_and_a_no_op_emits_nothing(bed: _Bed) -> None:
    """An audit of EFFECTS. Re-applying a tag the client already carries changed nothing, so it
    must not write an event — the stream is a trigger source, and a firing describing no change
    would hand every downstream automation a phantom."""
    await bed.run({"type": "apply_tag", "tag": "vip"})
    second = await bed.run({"type": "apply_tag", "tag": "  VIP  "})

    assert second.ok
    assert "already tagged" in (second.detail or "")
    assert await bed.contact_tags() == ["vip"]
    assert len(await bed.events()) == 1


@pytest.mark.asyncio
async def test_remove_tag_removes_it_and_an_absent_tag_is_a_no_op(bed: _Bed) -> None:
    await bed.run({"type": "apply_tag", "tag": "vip"})
    removed = await bed.run({"type": "remove_tag", "tag": "vip"})
    again = await bed.run({"type": "remove_tag", "tag": "vip"})

    assert removed.ok
    assert await bed.contact_tags() == []
    assert again.ok and "was not applied" in (again.detail or "")
    assert [e["event_type"] for e in await bed.events()] == [
        "action.tags-apply-tag",
        "action.tags-remove-tag",
    ]


@pytest.mark.asyncio
async def test_a_tag_is_interpolated_then_normalised(bed: _Bed) -> None:
    result = await bed.run(
        {"type": "apply_tag", "tag": "{{ contact.service_type || unknown }}-Client"},
        ctx=bed.ctx(facts={"contact.service_type": "Advised"}),
    )
    assert result.ok
    assert await bed.contact_tags() == ["advised-client"]


@pytest.mark.asyncio
async def test_case_memory_reaches_an_actions_copy(bed: _Bed) -> None:
    """``{{ memory.* }}`` renders for every action, not just ``lookup``.

    Three places promise this — the contract's ``send_email`` comment, ``memory.py``'s header (the
    security argument for ``remember`` being an authored per-key mapping rests on memory being
    what gets printed into client-facing copy), and ``apply_tag``'s own comment. ``build_context``
    merged none of it, so an approved template naming a remembered value rendered its fallback.
    """
    case_uid = await bed.seed_case()
    await bed.remember(case_uid, "risk_band", "cautious")

    result = await bed.run(
        {"type": "apply_tag", "tag": "{{ memory.risk_band || unknown }}-review"},
        ctx=bed.ctx(case_uid=case_uid),
    )

    assert result.ok
    assert await bed.contact_tags() == ["cautious-review"]


@pytest.mark.asyncio
async def test_a_firing_with_no_case_renders_the_fallback_rather_than_faulting(bed: _Bed) -> None:
    """The rule tier fires on contact-scoped triggers with no journey anywhere. Memory is
    per-case, so there is nothing to read — and "nothing to read" must be indistinguishable from
    an empty case, or an author's copy behaves differently depending on how the rule was reached.
    """
    result = await bed.run({"type": "apply_tag", "tag": "{{ memory.risk_band || unknown }}-review"})

    assert result.ok
    assert await bed.contact_tags() == ["unknown-review"]


@pytest.mark.asyncio
async def test_memory_already_carried_in_the_facts_is_not_re_read(bed: _Bed) -> None:
    """A flow action node hands ``build_context`` the runtime context, which fetched memory in the
    query it was already making. Re-reading it here would double that path's cost to arrive at the
    same answer, so the facts win — pinned because "it is only one more SELECT" is how an action
    dispatch grows a second round-trip per firing.
    """
    case_uid = await bed.seed_case()
    await bed.remember(case_uid, "risk_band", "stored")

    result = await bed.run(
        {"type": "apply_tag", "tag": "{{ memory.risk_band }}-review"},
        ctx=bed.ctx(case_uid=case_uid, facts={"memory": {"risk_band": "carried"}}),
    )

    assert result.ok
    assert await bed.contact_tags() == ["carried-review"]


@pytest.mark.asyncio
async def test_apply_tag_refuses_when_the_trigger_named_no_client(bed: _Bed) -> None:
    result = await bed.run(
        {"type": "apply_tag", "tag": "vip"}, ctx=bed.ctx(subject_kind=None, subject_id=None)
    )
    assert not result.ok
    assert "nobody to act on" in (result.detail or "")
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_a_case_tag_needs_a_case(bed: _Bed) -> None:
    result = await bed.run({"type": "apply_tag", "tag": "escalated", "target": "case"})
    assert not result.ok
    assert "did not happen inside a journey" in (result.detail or "")


@pytest.mark.asyncio
async def test_a_case_tag_lands_on_the_case(bed: _Bed) -> None:
    case_uid = await bed.seed_case()
    result = await bed.run(
        {"type": "apply_tag", "tag": "escalated", "target": "case"},
        ctx=bed.ctx(case_uid=case_uid),
    )
    assert result.ok
    assert result.case_uid == case_uid
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        case_id = (
            await s.execute(text("SELECT id FROM flows.cases WHERE uid = :u"), {"u": case_uid})
        ).scalar_one()
        assert await tags_mod.list_for(s, target="case", target_id=int(case_id)) == ["escalated"]


# ---- raise_task — the real service, with its real rules -------------------------------------


@pytest.mark.asyncio
async def test_raise_task_goes_through_the_tasks_service(bed: _Bed) -> None:
    result = await bed.run(
        {
            "type": "raise_task",
            "title": "Chase {{ contact.name }}",
            "description": "Raised by {{ automation.name }}",
            "assign_to": "role",
            "assignee_role_slug": "adviser",
            "category": "follow_up",
            "priority": "high",
            "due_in_days": 3,
        }
    )

    assert result.ok
    tasks = await bed.tasks()
    assert len(tasks) == 1
    task = tasks[0]
    # Interpolated against the enriched context: `contact.name` is not a lean condition fact.
    assert task["title"] == "Chase Ada Client"
    assert task["description"] == "Raised by Chase the client"
    assert task["category"] == "follow_up"
    assert task["priority"] == "high"
    assert task["assignee_kind"] == "role"
    assert task["assignee_role_id"] == bed.role_id
    assert_public_id(task["uid"])
    assert task["source_kind"] == "automation"
    assert task["subject_type"] == "contact"
    assert task["subject_id"] == str(bed.contact_id)
    assert task["due_date"] is not None


@pytest.mark.asyncio
async def test_raise_task_emits_the_same_event_a_human_would(bed: _Bed) -> None:
    """`task.created` — the operation id of the human route. An automated task and a
    manually raised one are the same kind of thing in the trail, told apart by actor."""
    await bed.run(
        {
            "type": "raise_task",
            "title": "Do it",
            "assign_to": "role",
            "assignee_role_slug": "adviser",
        }
    )
    events = await bed.events()
    assert [e["event_type"] for e in events] == ["task.created"]
    assert events[0]["route"] == "/tasks"
    assert events[0]["method"] == "POST"


@pytest.mark.asyncio
async def test_raise_task_refuses_a_role_the_firm_does_not_have(bed: _Bed) -> None:
    result = await bed.run(
        {
            "type": "raise_task",
            "title": "x",
            "assign_to": "role",
            "assignee_role_slug": "compliance",
        }
    )
    assert not result.ok
    assert "no 'compliance' role" in (result.detail or "")
    assert await bed.tasks() == []


@pytest.mark.asyncio
async def test_raise_task_can_target_the_clients_adviser(bed: _Bed) -> None:
    await bed.run({"type": "assign_adviser", "adviser_id": bed.user_id})
    result = await bed.run({"type": "raise_task", "title": "Call them", "assign_to": "adviser"})

    assert result.ok
    task = (await bed.tasks())[0]
    assert task["assignee_kind"] == "user"
    assert str(task["assignee_user_id"]) == bed.user_id


@pytest.mark.asyncio
async def test_raise_task_refuses_when_there_is_no_adviser_to_give_it_to(bed: _Bed) -> None:
    result = await bed.run({"type": "raise_task", "title": "Call them", "assign_to": "adviser"})
    assert not result.ok
    assert "no assigned adviser" in (result.detail or "")


@pytest.mark.asyncio
async def test_a_scheduled_firing_raises_a_fresh_task_each_time(bed: _Bed) -> None:
    """A schedule's whole purpose is to raise the quarterly review task every quarter. A stable
    source_ref would have collapsed every slot onto the first task, for ever."""
    spec = {
        "type": "raise_task",
        "title": "Quarterly review",
        "assign_to": "role",
        "assignee_role_slug": "adviser",
    }
    scheduled = bed.ctx(event_id=None, trigger_key="schedule.recurring")
    await bed.run(spec, ctx=scheduled)
    await bed.run(spec, ctx=scheduled)

    tasks = await bed.tasks()
    assert len(tasks) == 2
    assert all(t["source_ref"] is None for t in tasks)


@pytest.mark.asyncio
async def test_an_event_driven_firing_dedupes_on_source_ref(bed: _Bed) -> None:
    """The belt under the consumer's delivery row: the same event twice is one task."""
    spec = {
        "type": "raise_task",
        "title": "Chase",
        "assign_to": "role",
        "assignee_role_slug": "adviser",
    }
    await bed.run(spec)
    await bed.run(spec)

    tasks = await bed.tasks()
    assert len(tasks) == 1
    assert tasks[0]["source_ref"] == f"aut_{bed.nonce}:4242"


# ---- assign_adviser -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assign_adviser_goes_through_the_contacts_service(bed: _Bed) -> None:
    result = await bed.run({"type": "assign_adviser", "adviser_id": bed.user_id, "role": "adviser"})

    assert result.ok
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        row = (
            (
                await s.execute(
                    text(
                        "SELECT adviser_id, role FROM crm.client_advisers "
                        "WHERE tenant_id = :t AND contact_id = :c"
                    ),
                    {"t": bed.tenant_id, "c": bed.contact_id},
                )
            )
            .mappings()
            .one()
        )
    assert str(row["adviser_id"]) == bed.user_id
    assert row["role"] == "adviser"

    events = await bed.events()
    assert events[0]["event_type"] == "client.adviser_assigned"
    # The UID, matching what the HTTP route records. A flow assigning an adviser and a human doing
    # it are the same act, so the row each writes has to be the same shape — this pinned the serial,
    # which the live route stopped recording when clients became uid-addressed.
    assert events[0]["path_params"] == {"contact_uid": f"con_{bed.nonce}"}
    assert events[0]["metadata"]["subject"] == {"adviser_id": bed.user_id}


@pytest.mark.asyncio
async def test_assign_adviser_defaults_to_the_adviser_role(bed: _Bed) -> None:
    """An unauthored ``role`` means the adviser slot (0245), not a literal ``None`` handed to the
    service -- which has its own ``role: str = ADVISER_ROLE`` default, but only if this module
    does not get there first with ``None``."""
    result = await bed.run({"type": "assign_adviser", "adviser_id": bed.user_id})

    assert result.ok
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        role = (
            await s.execute(
                text(
                    "SELECT role FROM crm.client_advisers WHERE tenant_id = :t AND contact_id = :c"
                ),
                {"t": bed.tenant_id, "c": bed.contact_id},
            )
        ).scalar_one()
    assert role == "adviser"


@pytest.mark.asyncio
async def test_assign_adviser_refuses_an_unknown_role_as_a_mistake_not_a_fault(bed: _Bed) -> None:
    """The service's ``ValueError`` on a bad role is an authoring mistake, surfaced the same way
    as every other action's config errors -- not an uncaught exception."""
    result = await bed.run({"type": "assign_adviser", "adviser_id": bed.user_id, "role": "lead"})

    assert not result.ok
    assert "unknown team role" in (result.detail or "")
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_assign_adviser_enforces_the_services_tenant_rule(bed: _Bed, maker: Maker) -> None:
    """The rule that only exists in the service. A parallel INSERT would happily have linked an
    adviser from another firm."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        other_tenant = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.tenants (uid, alias, name) "
                        "VALUES (:u, :a, :n) RETURNING id"
                    ),
                    {
                        "u": f"oth_{bed.nonce}",
                        "a": f"oth-{bed.nonce}",
                        "n": f"Other {bed.nonce}",
                    },
                )
            ).scalar_one()
        )
        outsider = str(
            (
                await s.execute(
                    text(
                        "INSERT INTO auth.users (uid, tenant_id, workos_user_id, email, "
                        "  first_name) VALUES (:uid, :t, :w, :e, 'Outsider') RETURNING id"
                    ),
                    {
                        "uid": f"usr_out_{bed.nonce}",
                        "t": other_tenant,
                        "w": f"out_{bed.nonce}",
                        "e": f"out.{bed.nonce}@example.test",
                    },
                )
            ).scalar_one()
        )

    result = await bed.run({"type": "assign_adviser", "adviser_id": outsider})

    assert not result.ok
    assert "not a member of this firm" in (result.detail or "")
    assert await bed.events() == []


# ---- set_case_status ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_case_status_latches_a_case(bed: _Bed) -> None:
    case_uid = await bed.seed_case()
    result = await bed.run(
        {"type": "set_case_status", "status": "cancelled"}, ctx=bed.ctx(case_uid=case_uid)
    )

    assert result.ok
    assert await bed.case_status(case_uid) == "cancelled"
    events = await bed.events()
    assert events[0]["event_type"] == "action.flows-set-case-status"
    assert events[0]["metadata"]["subject"] == {"status": "cancelled"}


@pytest.mark.asyncio
async def test_set_case_status_refuses_a_working_state(bed: _Bed) -> None:
    """A working state is re-derived from the case's steps, so setting it would not survive the
    next event. Refusing beats an action whose effect silently evaporates."""
    case_uid = await bed.seed_case()
    result = await bed.run(
        {"type": "set_case_status", "status": "active"}, ctx=bed.ctx(case_uid=case_uid)
    )
    assert not result.ok
    assert "would not survive the next event" in (result.detail or "")


@pytest.mark.asyncio
async def test_set_case_status_on_an_already_finished_case_is_a_no_op(bed: _Bed) -> None:
    case_uid = await bed.seed_case(status="completed")
    result = await bed.run(
        {"type": "set_case_status", "status": "cancelled"}, ctx=bed.ctx(case_uid=case_uid)
    )
    assert result.ok  # idempotent: a redelivery must be harmless
    assert "already finished" in (result.detail or "")
    assert await bed.case_status(case_uid) == "completed"
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_set_case_status_copes_with_a_legacy_free_form_status(bed: _Bed) -> None:
    """`flows.cases.status` is free-form text (0021), so a v1 case may hold 'in_progress'. Reading
    it as the seven-state machine must not 500."""
    case_uid = await bed.seed_case(status="in_progress")
    result = await bed.run(
        {"type": "set_case_status", "status": "completed"}, ctx=bed.ctx(case_uid=case_uid)
    )
    assert result.ok
    assert await bed.case_status(case_uid) == "completed"


# ---- send_email -----------------------------------------------------------------------------


def _context_with(extra: dict[str, Any]) -> Any:
    """Wrap the real ``build_context`` so a test can add facts a trigger would not carry.

    The real one is called, not replaced: these tests are about the RENDERER, and a stubbed context
    would stop them proving that the contact's own fields still resolve alongside the added ones.
    """
    from app.flows import effects as real

    original = real.build_context

    async def wrapped(session: Any, ctx: Any) -> dict[str, Any]:
        context = await original(session, ctx)
        context.update(extra)
        return context

    return wrapped


@pytest.mark.asyncio
async def test_send_email_renders_the_published_template_and_sends_it(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    await bed.seed_template(
        code="chaser",
        subject="A reminder for {{ contact.name }}",
        html="<p>Dear {{ contact.name || client }}, please act.</p>",
    )
    sent: dict[str, Any] = {}

    async def fake_send(**kwargs: Any) -> bool:
        sent.update(kwargs)
        return True

    monkeypatch.setattr("app.email.transport.send_email", fake_send)

    result = await bed.run({"type": "send_email", "template_code": "chaser", "to": "client"})

    assert result.ok
    assert sent["to"] == f"ada.{bed.nonce}@example.test"
    assert sent["subject"] == "A reminder for Ada Client"
    assert "Dear Ada Client, please act." in sent["html"]
    assert "@page" not in sent["html"]
    assert "font-family" in sent["html"]

    events = await bed.events()
    assert events[0]["event_type"] == "document.sent"
    assert events[0]["metadata"]["subject"] == {"template_code": "chaser", "to": "client"}


@pytest.mark.asyncio
async def test_send_email_escapes_client_data_but_not_the_firms_markup(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch, maker: Maker
) -> None:
    """The injection that matters: a client's own record must not be able to inject markup into
    an email the firm's brand is on."""
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE crm.contacts SET name = :n WHERE id = :c"),
            {"n": "<script>alert(1)</script>", "c": bed.contact_id},
        )
    await bed.seed_template(code="chaser", html="<p><b>Dear</b> {{ contact.name }}</p>")
    sent: dict[str, Any] = {}

    async def fake_send(**kwargs: Any) -> bool:
        sent.update(kwargs)
        return True

    monkeypatch.setattr("app.email.transport.send_email", fake_send)
    assert (await bed.run({"type": "send_email", "template_code": "chaser"})).ok

    assert "<script>" not in sent["html"]
    assert "&lt;script&gt;" in sent["html"]
    assert "<b>Dear</b>" in sent["html"]  # the firm's own markup survived


@pytest.mark.asyncio
async def test_send_email_renders_what_the_editor_writes(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE REGRESSION. This path rendered with ``app/flows/interpolation.py``, whose entire grammar
    is ``{{ key || fallback }}`` — and a non-match is passed through UNCHANGED. The email editor
    writes Jinja: a Format on a merge field, a condition compiled from ``data-if``, a field
    fallback. So a firm that used any of the editor's own controls on a template a flow sends
    delivered the raw Jinja into a client's inbox, verbatim.

    Every assertion here failed before the renderer was unified, and each is one control in the
    editor's rail.
    """
    await bed.seed_template(
        code="editorish",
        subject="Statement for {{ contact.name }}",
        html=(
            "<p>Balance: {{ amount | currency }}</p>"
            "<p>Due: {{ due | date }}</p>"
            "{% if premium %}<p>Premium member</p>{% endif %}"
            "{% if not premium %}<p>Standard member</p>{% endif %}"
            "<p>Hi {{ missing_name | fallback('there') }}</p>"
        ),
    )
    sent: dict[str, Any] = {}

    async def fake_send(**kwargs: Any) -> bool:
        sent.update(kwargs)
        return True

    monkeypatch.setattr("app.email.transport.send_email", fake_send)
    monkeypatch.setattr(
        "app.flows.effects.build_context",
        _context_with({"amount": 1234.5, "premium": True}),
    )

    assert (await bed.run({"type": "send_email", "template_code": "editorish"})).ok

    assert "£1,234.50" in sent["html"]
    assert "Premium member" in sent["html"]
    assert "Standard member" not in sent["html"]
    assert "Hi there" in sent["html"]
    assert "{{" not in sent["html"]
    assert "{%" not in sent["html"]


@pytest.mark.asyncio
async def test_send_email_still_renders_the_grammar_this_path_used_to_own(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other direction, and the reason the fix is a TRANSLATION rather than a swap.
    ``{{ x || y }}``
    is a Jinja syntax error, so unifying the renderer without ``interpolation.to_jinja`` would have
    fixed editor-authored templates by breaking every template already authored here."""
    await bed.seed_template(
        code="legacy",
        subject="Hello {{ contact.name || you }}",
        html="<p>Dear {{ nothing_here || valued client }}, your balance is {{ zero || nil }}.</p>",
    )
    sent: dict[str, Any] = {}

    async def fake_send(**kwargs: Any) -> bool:
        sent.update(kwargs)
        return True

    monkeypatch.setattr("app.email.transport.send_email", fake_send)
    monkeypatch.setattr("app.flows.effects.build_context", _context_with({"zero": 0}))

    assert (await bed.run({"type": "send_email", "template_code": "legacy"})).ok

    assert "Dear valued client," in sent["html"]
    # `0` is a value, not an absence: `interpolation._is_absent` and the `fallback` filter agree.
    assert "balance is 0." in sent["html"]
    assert "nil" not in sent["html"]


@pytest.mark.asyncio
async def test_send_email_refuses_a_template_it_cannot_render(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`interpolation.render` never raised, so this is the one behaviour the swap adds. A template
    the engine cannot parse must PARK the case with a readable reason — not leak a traceback out of
    the worker, and above all not mail a client a page of literal braces."""
    await bed.seed_template(code="broken", html="<p>{% if unclosed %}oh dear</p>")
    calls: list[dict[str, Any]] = []

    async def fake_send(**kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr("app.email.transport.send_email", fake_send)

    result = await bed.run({"type": "send_email", "template_code": "broken"})

    assert not result.ok
    assert "could not be rendered" in (result.detail or "")
    assert "broken" in (result.detail or "")
    assert calls == []


@pytest.mark.asyncio
async def test_send_email_refuses_a_template_whose_filter_raises(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A MISUSED filter is not a `TemplateError`, and this path caught only those.

    Jinja does not wrap what a filter throws, so rounding a name is a plain `TypeError` — which
    escaped `send_email` as an unhandled exception instead of an `ActionRefused`. The case then
    logged a failed action with a raw traceback where an author needs the template's code and a
    door back into the editor. `contact.name` is a real field with a real value, so this is an
    ordinary authoring mistake and not a missing merge field."""
    await bed.seed_template(code="bad_filter", html="<p>{{ contact.name | round(2) }}</p>")
    calls: list[dict[str, Any]] = []

    async def fake_send(**kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr("app.email.transport.send_email", fake_send)

    result = await bed.run({"type": "send_email", "template_code": "bad_filter"})

    assert not result.ok
    assert "could not be rendered" in (result.detail or "")
    assert "bad_filter" in (result.detail or "")
    assert calls == []


@pytest.mark.asyncio
async def test_send_email_renders_the_preheader_through_the_template_engine(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preheader is the line an inbox shows beside the subject, and it is AUTHORED.

    It was the one field this path still rendered with `interpolation.render`, whose grammar is
    `{{ key || text }}`: a token carrying a Format or an If-empty — what the editor writes whenever
    an author sets a fallback — matched nothing and was passed through unchanged, so a journey
    mailed literal braces into the preview line for a preheader the author had just watched render
    correctly in the editor. `preview.py` and `render.py` both already used Jinja here.

    Escaping is autoescape's now, and it is the RIGHT escaping: a merged value is escaped (that is
    what stopped a contact name holding a tag going raw into a client's inbox) while the author's
    own literal text is left alone, exactly as the body treats it. The manual `html.escape` this
    replaces could not tell the two apart — it escaped the firm's own copy too, and stacked on top
    of autoescape it would have double-escaped every merged value."""
    await bed.seed_template(
        code="pre_filter",
        html="<p>hello</p>",
        preheader=(
            "Hi {{ contact.name | fallback('there') }} at {{ nobody_supplies_this }} & co "
            "{{ '<b>' }}"
        ),
    )
    calls: list[dict[str, Any]] = []

    async def fake_send(**kwargs: Any) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr("app.email.transport.send_email", fake_send)

    result = await bed.run({"type": "send_email", "template_code": "pre_filter"})

    assert result.ok, result.detail
    html = calls[0]["html"]
    assert "{{" not in html and "||" not in html, "the preheader reached the inbox unrendered"
    assert "Hi " in html
    # A merged value that IS markup arrives escaped — autoescape is on for this render.
    assert "&lt;b&gt;" in html
    # The author's own literal text is not touched, and nothing is escaped twice.
    assert "& co" in html and "&amp;amp;" not in html
    # An unsourced token in the preheader is a silent blank like anywhere else, so it is REPORTED
    # rather than refused — the detail is what tells an author which line came out empty.
    assert "nobody_supplies_this" in (result.detail or "")


@pytest.mark.asyncio
async def test_send_email_refuses_an_unpublished_template(bed: _Bed) -> None:
    await bed.seed_template(code="draft_only", published=False)
    result = await bed.run({"type": "send_email", "template_code": "draft_only"})
    assert not result.ok
    assert "no published version" in (result.detail or "")


@pytest.mark.asyncio
async def test_send_email_refuses_an_unknown_template(bed: _Bed) -> None:
    result = await bed.run({"type": "send_email", "template_code": "nope"})
    assert not result.ok
    assert "no email template with the code" in (result.detail or "")


@pytest.mark.asyncio
async def test_send_email_reads_the_EMAIL_templates_not_the_document_ones(bed: _Bed) -> None:
    """The action resolved `documents.templates` until Aug 2026, which made a template written in
    the email editor unreachable from a journey. A document template with the same code must NOT
    answer for it — otherwise the switch is untested and could be reverted silently."""
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        tid = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO documents.templates (tenant_id, uid, code, name, "
                        "  document_type, created_by) "
                        "VALUES (:t, :u, 'decoy', 'Decoy', 'letter', CAST(:by AS uuid)) "
                        "RETURNING id"
                    ),
                    {"t": bed.tenant_id, "u": f"dec{bed.nonce}"[:12], "by": bed.user_id},
                )
            ).scalar_one()
        )
        vid = int(
            (
                await s.execute(
                    text(
                        "INSERT INTO documents.template_versions (uid, tenant_id, template_id, "
                        "  version_number, status, schema, created_by) "
                        "VALUES (:u, :t, :tid, 1, 'published', CAST(:sc AS jsonb), "
                        "  CAST(:by AS uuid)) RETURNING id"
                    ),
                    {
                        "u": f"dv{bed.nonce}"[:12],
                        "t": bed.tenant_id,
                        "tid": tid,
                        "sc": json.dumps({"html": "<p>decoy</p>"}),
                        "by": bed.user_id,
                    },
                )
            ).scalar_one()
        )
        await s.execute(
            text("UPDATE documents.templates SET current_published_version_id = :v WHERE id = :t"),
            {"v": vid, "t": tid},
        )
    result = await bed.run({"type": "send_email", "template_code": "decoy"})
    assert not result.ok
    assert "no email template with the code" in (result.detail or "")


@pytest.mark.asyncio
async def test_send_email_refuses_an_arbitrary_recipient(bed: _Bed) -> None:
    """A closed recipient vocabulary. An arbitrary address authored in a rule would be a standing
    exfiltration channel."""
    await bed.seed_template(code="chaser")
    result = await bed.run(
        {"type": "send_email", "template_code": "chaser", "to": "attacker@evil.test"}
    )
    assert not result.ok
    assert "is not an email recipient" in (result.detail or "")
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_the_email_is_a_complete_document_not_a_body_fragment(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The send path with no catalogue ``code`` behind it. ``email_shell.shell`` returns a body
    FRAGMENT,
    and this action handed it straight to ``send_email`` — so a client received no doctype, no
    charset declaration and none of the base reset that normalises tables and linked images across
    clients. It went unseen because the parity suite discovered send sites by scanning for
    ``render_or_default`` calls, and this one makes none; it goes through
    ``email_render.sendable``."""
    await bed.seed_template(code="chaser", html='<p><img src="https://x.test/l.png"> Hello</p>')
    sent: dict[str, Any] = {}

    async def fake_send(**kwargs: Any) -> bool:
        sent.update(kwargs)
        return True

    monkeypatch.setattr("app.email.transport.send_email", fake_send)
    assert (await bed.run({"type": "send_email", "template_code": "chaser"})).ok

    assert sent["html"].startswith("<!DOCTYPE html>"), "the email is still a bare body fragment"
    assert "charset" in sent["html"]
    assert "-ms-interpolation-mode" in sent["html"]
    # The template's CSS is inlined, so no <style> survives. The one exception is the shell's
    # `[if mso]` CTA ink (render/email.py::MSO_CTA_INK) — a stylesheet to Word, inert elsewhere,
    # stripped here by its literal so a SECOND block still fails.
    assert "<style" not in sent["html"].replace(render_email.MSO_CTA_INK, ""), (
        "a <style> block would be dropped by the clients that count"
    )


@pytest.mark.asyncio
async def test_send_email_reports_a_failed_send_rather_than_claiming_success(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PROVIDER rejection — Resend answered no on a deployment that really can mail. An automation
    that reports 'emailed the client' when nothing left the building is the failure a chaser rule
    exists to prevent.

    The settings are set explicitly because the distinction is the whole point: without them the
    test environment's blank ``RESEND_API_KEY`` makes this a SUPPRESSED send — the case below."""
    monkeypatch.setattr(settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(settings, "app_stage", "production")
    await bed.seed_template(code="chaser")

    async def fake_send(**kwargs: Any) -> bool:
        return False

    monkeypatch.setattr("app.email.transport.send_email", fake_send)
    result = await bed.run({"type": "send_email", "template_code": "chaser"})

    assert not result.ok
    assert result.detail == "the email to the client could not be sent"
    # Not 'not configured': that named the wrong cause on the far more common path.
    assert "not configured" not in (result.detail or "")
    assert await bed.events() == []  # no event for an email that never went


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "stage"),
    [("", "production"), ("re_test_key", "staging")],
    ids=["no-credential", "non-production-empty-allowlist"],
)
async def test_a_suppressed_send_does_not_park_the_case(
    bed: _Bed, monkeypatch: pytest.MonkeyPatch, key: str, stage: str
) -> None:
    """SUPPRESSED is not FAILED, and ``app/email/transport.py``'s own docstring already promised as
    much —
    "a suppressed send is an outcome, not an error, so callers (… a flow action) don't hard-fail
    on … being run in dev". The code did the opposite: any False became ``ActionRefused``, latching
    the step ``failed`` and parks the case at ``blocked``. So on a dev or staging deploy whose
    allowlist was empty — a cleared list suppresses every send — every journey carrying an email
    action parked, and the reason given named the wrong cause.

    Both suppression reasons are covered: no credential, and a stage guard with an empty allowlist.
    The real ``email.send_email`` is used rather than a stub, because what is under test is
    precisely the guard's own answer.

    ``changed`` stays False, so an email nobody received writes no action event — the stream is a
    trigger source, and a downstream chaser must not fire off a send that did not happen."""
    monkeypatch.setattr(settings, "email_nonprod_guard", True)
    monkeypatch.setattr(settings, "resend_api_key", key)
    monkeypatch.setattr(settings, "app_stage", stage)
    monkeypatch.setattr(settings, "email_allowlist", [])
    await bed.seed_template(code="chaser")

    result = await bed.run({"type": "send_email", "template_code": "chaser"})

    assert result.ok, f"a suppressed send parked the case: {result.detail}"
    assert "was not sent" in (result.detail or "")
    assert result.detail == "the email to the client was not sent"
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_send_email_refuses_a_client_with_no_address(bed: _Bed, maker: Maker) -> None:
    async with maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        await s.execute(
            text("UPDATE crm.contacts SET email = NULL WHERE id = :c"), {"c": bed.contact_id}
        )
    await bed.seed_template(code="chaser")
    result = await bed.run({"type": "send_email", "template_code": "chaser"})
    assert not result.ok
    assert "no email address" in (result.detail or "")


# ---- The audit story ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_event_names_the_automation_as_the_actor(bed: _Bed) -> None:
    await bed.run({"type": "apply_tag", "tag": "vip"})
    event = (await bed.events())[0]

    assert event["actor_sub"] == f"automation:aut_{bed.nonce}"
    # No auth.users row behind an automation — the rule's author was not the one who sent it.
    assert event["actor_id"] is None
    assert event["tenant_id"] == bed.tenant_id
    assert event["target_tenant_id"] is None
    assert event["status"] == 200


@pytest.mark.asyncio
async def test_the_chain_marker_is_exactly_what_the_self_trigger_guard_reads(bed: _Bed) -> None:
    """THE contract between this module and the consumer's most important safety guard. The three
    keys below are load-bearing: a marker the consumer cannot parse is treated as ABSENT, which
    silently re-enables automations feeding themselves."""
    from app.flows import automation

    await bed.run({"type": "apply_tag", "tag": "vip"}, ctx=bed.ctx(chain_depth=2))
    event = (await bed.events())[0]
    marker = event["metadata"]["automation"]

    assert marker["source"] == "rule"
    assert marker["id"] == f"aut_{bed.nonce}"
    assert marker["chain_depth"] == 2
    assert effects.ACTIONS  # (module is loaded)
    assert set(marker) >= {"source", "id", "chain_depth"}

    parsed = automation._automation_marker(event)
    assert parsed is not None
    assert int(parsed["chain_depth"]) + 1 == 3


@pytest.mark.asyncio
async def test_a_refused_action_writes_no_event(bed: _Bed) -> None:
    """Effects, not attempts — the request-layer floor's rule, one level in."""
    result = await bed.run({"type": "apply_tag", "tag": "   "})
    assert not result.ok
    assert await bed.events() == []


@pytest.mark.asyncio
async def test_an_unknown_action_type_is_refused_not_crashed(bed: _Bed) -> None:
    result = await bed.run({"type": "teleport_client"})
    assert not result.ok
    assert "is not an action type" in (result.detail or "")


@pytest.mark.asyncio
@pytest.mark.pkgs_ui_pin
async def test_an_unimplemented_contract_action_says_it_is_not_available_yet(
    bed: _Bed, monkeypatch
) -> None:
    """A contract action with no handler in THIS build is authorable in the builder (the
    vocabulary is shared) and must refuse loudly rather than quietly report success — the case a
    definition published by a newer build and run by an older one produces.

    Every action is implemented now, so the seam is demonstrated by taking one back out: the flag
    and the ``execute`` branch that reads it are the thing under test, not ``lookup``.
    """
    monkeypatch.setitem(
        effects.ACTIONS, "lookup", replace(effects.ACTIONS["lookup"], implemented=False)
    )
    result = await bed.run({"type": "lookup", "endpoint_key": "x"})
    assert not result.ok
    assert "not available yet" in (result.detail or "")


# ---- Savepoint isolation --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_raising_action_rolls_back_only_its_own_savepoint(bed: _Bed) -> None:
    """The consumer wraps each dispatch in ``begin_nested`` so one bad automation cannot take the
    batch with it. Proved end to end: a tag applied in the same transaction BEFORE the explosion
    survives, and the exploding action leaves nothing behind."""
    target = triggers.target_for("action")
    assert target is not None

    async def boom(session: Any, ctx: Any, config: Any) -> Any:
        await tags_mod.apply(
            session,
            tenant_id=ctx.tenant_id,
            target="contact",
            target_id=ctx.subject_id,
            tag="doomed",
            source="automation",
        )
        raise RuntimeError("the action exploded")

    previous = effects.HANDLERS["apply_tag"]
    try:
        async with bed.maker() as s, s.begin():
            await s.execute(text("SET LOCAL ROLE service_role"))
            async with s.begin_nested():
                await target.run(s, bed.ctx(), {"type": "apply_tag", "tag": "survivor"})

            effects.HANDLERS["apply_tag"] = boom  # type: ignore[assignment]
            with pytest.raises(RuntimeError):
                async with s.begin_nested():
                    await target.run(s, bed.ctx(), {"type": "apply_tag", "tag": "doomed"})
    finally:
        effects.HANDLERS["apply_tag"] = previous

    assert await bed.contact_tags() == ["survivor"]


# ---- lookup, in the RULE tier — where there may be no case ----------------------------------


@pytest.mark.asyncio
async def test_lookup_refuses_a_firing_that_is_not_inside_a_journey(bed: _Bed, monkeypatch) -> None:
    """A lookup's output IS case memory, and the rule tier fires on contact events with no case at
    all. Refusing is the honest answer; the alternative is an action that reports success having
    written its answer nowhere — and a rule whose whole point was to fetch something."""
    from app.config import settings
    from app.flows import lookup as lookup_mod

    monkeypatch.setattr(settings, "automation_lookup_endpoints", {"risk": "https://r.example/x"})

    called = False

    def _never() -> Any:
        nonlocal called
        called = True
        raise AssertionError("a lookup with no case must not reach the network")

    monkeypatch.setattr(lookup_mod, "_build_client", _never)

    result = await bed.run(
        {
            "type": "lookup",
            "endpoint_key": "risk",
            "response_mapping": {"risk_band": "band"},
        }
    )
    assert not result.ok
    assert "not happen inside a journey" in (result.detail or "")
    assert called is False
    assert await bed.events() == []


# ---- Publish preflight ----------------------------------------------------------------------


async def _preflight(bed: _Bed, action: dict[str, Any]) -> str | None:
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        return await effects.preflight(s, tenant_id=bed.tenant_id, action=action)


@pytest.mark.asyncio
async def test_preflight_refuses_an_action_no_role_can_perform(bed: _Bed) -> None:
    """THE preflight refusal. Nobody in this firm may tag a client, so an automation that tags one
    is asserting an authority the firm has not granted anyone."""
    reason = await _preflight(bed, {"type": "apply_tag", "tag": "vip"})
    assert reason is not None
    assert "contacts:write" in reason


@pytest.mark.asyncio
async def test_preflight_passes_once_a_role_holds_the_permission(bed: _Bed) -> None:
    await bed.grant("contacts:write")
    assert await _preflight(bed, {"type": "apply_tag", "tag": "vip"}) is None


@pytest.mark.asyncio
async def test_preflight_accepts_write_in_place_of_read(bed: _Bed) -> None:
    """`satisfies`' write⇒read implication, applied to the "does anyone hold this" question."""
    await bed.grant("contacts:write")
    assert effects.ACTIONS["apply_tag"].permission == "contacts:write"
    async with bed.maker() as s, s.begin():
        await s.execute(text("SET LOCAL ROLE service_role"))
        assert await effects.permission_held_by_any_role(
            s, tenant_id=bed.tenant_id, permission="contacts:read"
        )


@pytest.mark.asyncio
async def test_preflight_refuses_an_unpublished_template_at_authoring(bed: _Bed) -> None:
    """"An unpublished template is a preflight failure, not a runtime surprise.""" ""
    await bed.grant("documents:send")
    await bed.seed_template(code="draft_only", published=False)
    reason = await _preflight(bed, {"type": "send_email", "template_code": "draft_only"})
    assert reason is not None and "no published version" in reason


@pytest.mark.asyncio
async def test_preflight_accepts_a_published_template(bed: _Bed) -> None:
    await bed.grant("documents:send")
    await bed.seed_template(code="chaser")
    assert await _preflight(bed, {"type": "send_email", "template_code": "chaser"}) is None


@pytest.mark.asyncio
async def test_preflight_refuses_a_working_case_status(bed: _Bed) -> None:
    await bed.grant("cases:write")
    reason = await _preflight(bed, {"type": "set_case_status", "status": "waiting"})
    assert reason is not None and "would not survive" in reason
    assert await _preflight(bed, {"type": "set_case_status", "status": "completed"}) is None


@pytest.mark.asyncio
async def test_preflight_refuses_the_unrunnable_and_the_unknown(bed: _Bed, monkeypatch) -> None:
    from app.config import settings

    await bed.grant("documents:write", "automations:write")
    assert "not an action type" in (await _preflight(bed, {"type": "nope"}) or "")

    # `lookup` is implemented, so the 'not available yet' seam is demonstrated on a patched copy.
    monkeypatch.setitem(
        effects.ACTIONS,
        "lookup",
        replace(effects.ACTIONS["lookup"], implemented=False),
    )
    assert "not available yet" in (await _preflight(bed, {"type": "lookup"}) or "")
    monkeypatch.undo()

    monkeypatch.setattr(settings, "automation_lookup_endpoints", {})
    reason = await _preflight(
        bed, {"type": "lookup", "endpoint_key": "risk", "response_mapping": {"band": "band"}}
    )
    assert "AUTOMATION_LOOKUP_ENDPOINTS" in (reason or "")


@pytest.mark.asyncio
async def test_preflight_catches_static_config_mistakes(bed: _Bed) -> None:
    await bed.grant("tasks:write", "contacts:write")
    assert "names no 'title'" in (await _preflight(bed, {"type": "raise_task"}) or "")
    assert "no 'compliance' role" in (
        await _preflight(
            bed,
            {
                "type": "raise_task",
                "title": "x",
                "assign_to": "role",
                "assignee_role_slug": "compliance",
            },
        )
        or ""
    )
    assert "not a task category" in (
        await _preflight(
            bed,
            {
                "type": "raise_task",
                "title": "x",
                "assign_to": "role",
                "assignee_role_slug": "adviser",
                "category": "invented",
            },
        )
        or ""
    )
    assert "not a tag target" in (
        await _preflight(bed, {"type": "apply_tag", "tag": "x", "target": "account"}) or ""
    )
    assert "names no 'tag'" in (await _preflight(bed, {"type": "apply_tag", "tag": "  "}) or "")
    assert "longer than" in (await _preflight(bed, {"type": "apply_tag", "tag": "x" * 200}) or "")
    # 0245: a client team role, not a free-text word -- caught at authoring, same as the others.
    assert "not a client team role" in (
        await _preflight(bed, {"type": "assign_adviser", "adviser_id": bed.user_id, "role": "lead"})
        or ""
    )
    assert await _preflight(bed, {"type": "assign_adviser", "adviser_id": bed.user_id}) is None


@pytest.mark.asyncio
async def test_preflight_does_not_judge_runtime_facts(bed: _Bed) -> None:
    """A rule must be savable when today's client happens to lack an adviser — that is a runtime
    truth that changes hourly, not an authoring mistake."""
    await bed.grant("tasks:write")
    assert (
        await _preflight(bed, {"type": "raise_task", "title": "x", "assign_to": "adviser"}) is None
    )


# A variable fills a BLANK in approved copy: bounded, in the authored grammar, never a case field.


class TestSendEmailVariables:
    """`parse_email_variables` — the shape, the bounds and the two refusals."""

    def test_a_missing_or_blank_mapping_is_no_variables(self) -> None:
        assert effects.parse_email_variables({}, {}) == {}
        assert effects.parse_email_variables({"variables": None}, {}) == {}
        assert effects.parse_email_variables({"variables": "  "}, {}) == {}

    def test_a_json_object_string_is_accepted(self) -> None:
        # The builder's config fields are textareas, so both shapes reach here (`lookup._mapping`).
        assert effects.parse_email_variables(
            {"variables": '{"document_name": "your ISA statement"}'}, {}
        ) == {"document_name": "your ISA statement"}
        assert effects.parse_email_variables(
            {"variables": {"document_name": "your ISA statement"}}, {}
        ) == {"document_name": "your ISA statement"}

    def test_unreadable_json_is_refused_with_the_shape_to_write(self) -> None:
        with pytest.raises(effects.ActionRefused) as exc:
            effects.parse_email_variables({"variables": "document_name: x"}, {})
        assert "document_name" in str(exc.value)  # the refusal shows the shape, not just "invalid"

    def test_a_dotted_name_is_refused(self) -> None:
        # Dots expand into nested dicts, so this would overwrite one token in the firm's brand.
        with pytest.raises(effects.ActionRefused) as exc:
            effects.parse_email_variables(
                {"variables": {"brand.support_email": "me@example.com"}}, {}
            )
        assert "no dots" in str(exc.value)

    def test_a_name_that_shadows_a_context_scope_is_refused(self) -> None:
        # One level up: a bare scope name replaces the whole nested dict the firm's branding owns.
        with pytest.raises(effects.ActionRefused) as exc:
            effects.parse_email_variables(
                {"variables": {"brand": "x"}}, {"brand.support_email": "real@firm.example"}
            )
        assert "already provides" in str(exc.value)

    def test_the_reserved_set_is_derived_from_the_context_not_a_literal_list(self) -> None:
        with pytest.raises(effects.ActionRefused):
            effects.parse_email_variables({"variables": {"invented": "x"}}, {"invented.thing": 1})
        assert effects.parse_email_variables(
            {"variables": {"invented": "x"}}, {"contact.email": "a@b.c"}
        ) == {"invented": "x"}

    def test_the_count_is_bounded(self) -> None:
        many = {f"f{i}": "x" for i in range(effects.MAX_EMAIL_VARIABLES + 1)}
        with pytest.raises(effects.ActionRefused) as exc:
            effects.parse_email_variables({"variables": many}, {})
        assert str(effects.MAX_EMAIL_VARIABLES) in str(exc.value)

    def test_a_value_is_bounded_so_it_stays_a_blank_and_not_a_second_authoring(self) -> None:
        long = "x" * (effects.MAX_EMAIL_VARIABLE_CHARS + 1)
        with pytest.raises(effects.ActionRefused) as exc:
            effects.parse_email_variables({"variables": {"note": long}}, {})
        assert "belongs in the template" in str(exc.value)

    def test_a_non_text_value_is_refused(self) -> None:
        with pytest.raises(effects.ActionRefused):
            effects.parse_email_variables({"variables": {"note": 5}}, {})


class TestSendEmailVariablesAreData:
    """A variable's VALUE is interpolated in the authored grammar and is inert thereafter."""

    def test_a_value_is_composed_from_facts_with_a_literal_fallback(self) -> None:
        rendered = effects._render(
            "{{ document.name || your statement }}", {"document.name": "ISA 2026"}
        )
        assert rendered == "ISA 2026"
        assert effects._render("{{ document.name || your statement }}", {}) == "your statement"

    def test_a_value_cannot_smuggle_jinja_into_approved_copy(self) -> None:
        # Jinja never re-renders a substituted value, so one that resolves to a tag is inert.
        body = jinja_engine.render(
            "<p>{{ note }}</p>", {"note": "{% for x in brand %}{{ x }}{% endfor %}"}
        )
        assert "for x in brand" in body  # present as text…
        assert "{%" in body  # …never executed
