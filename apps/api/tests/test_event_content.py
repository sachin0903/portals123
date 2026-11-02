"""The September 2026 event-content review, pinned.

The finding: of 224 live event types, 54 carried a path id that resolved to nothing and 32 more
named the record and said nothing about the change ("Set a transfer's status", for every transfer,
to every status). This module pins the two new mechanisms. `extract_changed_keys` records the NAMES
of an update body's keys and never the values, and the whole safety argument is that no value can
land — so the tests attack it from that direction: a crafted key, a value that looks like a key, an
oversized body. The entity resolvers are one label query per kind, where a typo is invisible (an
unresolved id renders as a bare uid, which is the defect the review found), so every one of them is
EXECUTED against a real database here.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit_actions as actions
from app import audit_stream
from app.event_schema import registry
from app.routers.admin import (
    _ENTITY_FIELD_KINDS,
    _ENTITY_RESOLVERS,
    _UID_KIND_BY_RESOURCE,
    _resolve_entities,
)

# --------------------------------------------------------------------------------------------
# extract_changed_keys — names, never values
# --------------------------------------------------------------------------------------------

#: An event that is on the list, whatever the registry is edited to later.
_ON = next(iter(sorted(actions.CHANGED_KEY_EVENTS)))


def test_an_event_not_on_the_list_captures_nothing() -> None:
    """The default is still that a body is not read. An event earns the capture by being declared
    `changed_keys=True`, and every other mutating route in the estate is untouched."""
    assert "client.created" not in actions.CHANGED_KEY_EVENTS
    assert actions.extract_changed_keys("client.created", b'{"name": "Margaret Cole"}') is None


def test_it_records_the_key_names_sorted() -> None:
    body = json.dumps({"phone": "07700 900000", "email": "m@example.com", "dob": "1969-01-02"})
    assert actions.extract_changed_keys(_ON, body.encode()) == ["dob", "email", "phone"]


def test_no_value_can_ever_land() -> None:
    """The guarantee the whole mechanism rests on, asserted against the values most likely to be
    mistaken for a capture: a client's real details, and a credential."""
    secrets = {
        "email": "m.cole@example.com",
        "dob": "1969-01-02",
        "password": "hunter2",
        "workos_api_key": "sk_live_abcdef",
    }
    captured = actions.extract_changed_keys(_ON, json.dumps(secrets).encode())
    assert captured is not None
    rendered = json.dumps(captured)
    for value in secrets.values():
        assert value not in rendered
    # The NAMES survive, including the secret-shaped ones: that a credential was rewritten is the
    # audit fact, and the name is not the credential.
    assert captured == ["dob", "email", "password", "workos_api_key"]


@pytest.mark.parametrize(
    "key",
    [
        "the client asked us to change their bank details to 40-47-84",
        "email; DROP TABLE crm.contacts",
        "m.cole@example.com",
        "field-with-dash",
        "x" * 65,
        "",
    ],
)
def test_a_crafted_key_is_refused(key: str) -> None:
    """A key is caller-supplied text, so without a shape rule somebody could smuggle content into
    the trail by naming a field after it. Only identifier-shaped names — which is every field any
    model in this estate declares — are admitted."""
    assert actions.extract_changed_keys(_ON, json.dumps({key: 1}).encode()) is None


def test_a_crafted_key_does_not_take_the_real_ones_with_it() -> None:
    """One bad key drops itself, not the capture: the real fields are still reported."""
    body = json.dumps({"email": "x", "not a field name": "y"})
    assert actions.extract_changed_keys(_ON, body.encode()) == ["email"]


def test_a_body_with_more_keys_than_any_form_is_refused_whole() -> None:
    """Refuse, never truncate (the house rule for a cap): a truncated list would imply those were
    all the changes, which is a wrong answer where no answer was honest."""
    body = json.dumps({f"field_{i}": i for i in range(actions.MAX_CHANGED_KEYS + 1)})
    assert actions.extract_changed_keys(_ON, body.encode()) is None
    ok = json.dumps({f"field_{i}": i for i in range(actions.MAX_CHANGED_KEYS)})
    assert len(actions.extract_changed_keys(_ON, ok.encode()) or []) == actions.MAX_CHANGED_KEYS


@pytest.mark.parametrize("body", [b"", b"not json", b"[1,2,3]", b'"a string"', b"{}"])
def test_an_unreadable_body_yields_nothing_rather_than_raising(body: bytes) -> None:
    """Fail-quiet, like both other captures: a multipart upload, an empty PATCH or a JSON array
    each yields no capture and never an error — the action must not fail for the trail's sake."""
    assert actions.extract_changed_keys(_ON, body) is None


def test_the_middleware_buffers_the_body_for_a_changed_key_event() -> None:
    """The wiring, asserted on the source: the body is buffered when an event is on EITHER list.
    A changed-key event that is not also a subject event would otherwise reach
    `extract_changed_keys` with an empty body and capture nothing, silently."""
    import inspect

    source = inspect.getsource(actions.ActionEventsMiddleware)
    for allowlist in ("SUBJECT_FIELDS", "CHANGED_KEY_EVENTS", "BODY_CAPTURE_EVENTS"):
        assert f"event_type in {allowlist}" in source, (
            f"the middleware does not buffer the body for {allowlist} — every capture that reads "
            "the body needs its own arm here, or it silently receives an empty one"
        )


def test_changed_key_events_are_not_creates() -> None:
    """A create's body is the whole record, so listing its field names says only "it was created
    with the fields a client has" — noise. The capture is for acts whose body IS the change."""
    creates = sorted(e for e in actions.CHANGED_KEY_EVENTS if e.endswith(".created"))
    assert creates == [], (
        f"{creates}: a `.created` event on the changed-key list records the shape of a new record "
        "rather than a change — capture the one or two CODES that identify it instead (the way "
        "`flow.created` names its code and entity), or state here why this one is the exception"
    )


# --------------------------------------------------------------------------------------------
# body capture — the widest thing the trail stores
# --------------------------------------------------------------------------------------------

_BODY = next(iter(sorted(actions.BODY_CAPTURE_EVENTS)))


def test_a_declared_event_keeps_its_request() -> None:
    """The point of it: an act whose content is free text or a form's answers is unreadable from a
    handful of captured ids, and no allowlist of values can reach three hundred such fields."""
    body = json.dumps({"title": "Annual review", "answers": {"risk": 4}, "count": 2})
    assert actions.extract_body(_BODY, body.encode()) == {
        "title": "Annual review",
        "answers": {"risk": 4},
        "count": 2,
    }


def test_an_undeclared_event_keeps_nothing() -> None:
    """Declared, never blanket — every other route still streams its body through untouched, which
    is what keeps this a stated exception rather than a new default."""
    assert "authentication.succeeded" not in actions.BODY_CAPTURE_EVENTS
    assert actions.extract_body("authentication.succeeded", b'{"password": "x"}') is None


def test_a_credential_never_lands_however_deep_it_sits() -> None:
    """The one thing body capture does NOT widen. `redact` is flat, which is right for path and
    query params and wrong for a body — a secret does not become safe by sitting one level in."""
    body = json.dumps(
        {
            "name": "Clifton",
            "workos_api_key": "sk_live_abc",
            "config": {"cookie_password": "hunter2", "port": 5432},
            "list": [{"api_key": "nested-in-a-list"}],
            "secrets": {"anything": "in here"},
        }
    )
    kept = actions.extract_body(_BODY, body.encode())
    assert kept is not None
    rendered = json.dumps(kept)
    for secret in ("sk_live_abc", "hunter2", "nested-in-a-list", "in here"):
        assert secret not in rendered
    # A secret-shaped KEY masks its whole subtree, not just a scalar: a bag named `secrets` is a
    # bag of secrets whatever it happens to hold, and descending into it to keep the harmless
    # members would be trusting the name of every key inside it instead of the one on the bag.
    assert kept["secrets"] == "[redacted]"
    # …and the harmless neighbours survive, so the row still says what was done.
    assert kept["name"] == "Clifton"
    assert kept["config"]["port"] == 5432


def test_a_body_past_the_ceiling_is_refused_whole_and_says_so() -> None:
    """Never a truncated object: `audit.actions` is evidence, and half a document read back as
    though it were the whole one is a wrong answer told confidently. The row still records that a
    body was sent, and how big."""
    big = json.dumps({"blob": "x" * (actions.MAX_STORED_BODY_BYTES + 100)})
    kept = actions.extract_body(_BODY, big.encode())
    assert kept is not None
    assert kept["_omitted"] and kept["_bytes"] > actions.MAX_STORED_BODY_BYTES
    assert "xxxx" not in json.dumps(kept)


@pytest.mark.parametrize("body", [b"", b"not json", b"[1,2]", b'"a string"'])
def test_an_unreadable_body_is_kept_as_nothing(body: bytes) -> None:
    """Named apart from the changed-keys case above on purpose: two test functions with one name
    means the first is silently shadowed and never runs. Ruff's F811 caught exactly that here."""
    assert actions.extract_body(_BODY, body) is None


def test_recursion_is_bounded() -> None:
    """A pathological document cannot recurse away with the request thread."""
    deep: dict[str, object] = {"v": 1}
    for _ in range(40):
        deep = {"nested": deep}
    kept = actions.extract_body(_BODY, json.dumps(deep).encode())
    assert kept is not None and "[deep]" in json.dumps(kept)


def test_body_capture_is_declared_for_the_acts_that_need_it() -> None:
    """It is on where the row could not otherwise say what was done, and off where the event's own
    captured values already do. `tests/test_event_replayability.py` is what keeps that true."""
    assert "client.updated" in actions.BODY_CAPTURE_EVENTS  # a PATCH of thirty optional fields
    assert "flow.version_saved" in actions.BODY_CAPTURE_EVENTS  # a whole definition
    # …and NOT on an act whose content is one closed value the event already names.
    assert "contribution.status_updated" not in actions.BODY_CAPTURE_EVENTS
    assert "account.model_updated" not in actions.BODY_CAPTURE_EVENTS


def test_an_event_whose_body_is_only_credentials_stays_unreplayable() -> None:
    """Deliberate, and the one place the requirement is not met: keeping the body of
    `environment.secrets_updated` would keep `[redacted]`, so nothing is declared for it and the
    catalogue says so rather than implying the row could reproduce the call."""
    assert "environment.secrets_updated" not in actions.BODY_CAPTURE_EVENTS


# --------------------------------------------------------------------------------------------
# the entity resolvers — every one of them must actually read the schema it names
# --------------------------------------------------------------------------------------------


def test_every_resolvable_kind_has_a_resolver() -> None:
    """A kind named by a field or by a `{uid}` resource with no resolver is a KeyError inside
    `_resolve_entities` — which is a 500 for the whole feed, not a missing chip."""
    for field, kind in _ENTITY_FIELD_KINDS.items():
        assert kind in _ENTITY_RESOLVERS, f"{field!r} names kind {kind!r}, which has no resolver"
    for resource, kind in _UID_KIND_BY_RESOURCE.items():
        assert kind in _ENTITY_RESOLVERS, (
            f"a {resource!r} `uid` names kind {kind!r}, which has no resolver"
        )


def test_every_resolver_kind_is_reachable() -> None:
    """The converse: a resolver nothing names is dead SQL that will rot without anyone noticing."""
    named = set(_ENTITY_FIELD_KINDS.values()) | set(_UID_KIND_BY_RESOURCE.values())
    assert set(_ENTITY_RESOLVERS) == named, (
        f"resolvers nothing names: {sorted(set(_ENTITY_RESOLVERS) - named)}; "
        f"kinds with no resolver: {sorted(named - set(_ENTITY_RESOLVERS))}"
    )


@pytest.mark.asyncio
async def test_every_resolver_executes_against_the_real_schema(
    service_session: AsyncSession,
) -> None:
    """EXECUTE every label query, empty-handed.

    A resolver that names a column or a table wrong does not raise where anyone sees it: it simply
    matches nothing, `_resolve_entities` leaves the label null, and the chip renders as a bare uid —
    which is indistinguishable from the record having been deleted, and is precisely the defect
    this whole change set exists to fix. Running each one proves the schema it reads is the schema
    that exists. Empty arrays, so this asserts on the SQL rather than on any seeded row.
    """
    for kind, sql in _ENTITY_RESOLVERS.items():
        try:
            await service_session.execute(text(sql), {"ids": [], "int_ids": [], "uuid_ids": []})
        except Exception as exc:  # noqa: BLE001 - the failure IS the assertion
            pytest.fail(f"the {kind!r} entity resolver does not run: {exc}")


def test_the_tag_map_covers_every_new_kind() -> None:
    """The filter and the chips must agree about the words — the same pin
    `test_admin_audit_actions` makes, restated here so a kind added by this change set cannot be
    given a resolver and left unfilterable."""
    for field, kind in _ENTITY_FIELD_KINDS.items():
        assert field in audit_stream.TAG_FIELDS.get(kind, ()), (
            f"{field!r} resolves to {kind!r} but is not in TAG_FIELDS[{kind!r}]"
        )
    for resource, kind in _UID_KIND_BY_RESOURCE.items():
        assert resource in audit_stream.TAG_UID_RESOURCES.get(kind, ()), (
            f"a {resource!r} `uid` resolves to {kind!r} but is not in TAG_UID_RESOURCES[{kind!r}]"
        )


def test_the_review_left_no_status_change_unnamed() -> None:
    """The review's own headline case, kept honest: an act whose whole content is a status must
    record the status it moved TO — and, where the route can afford the pre-read, the one it came
    FROM. Without this the row says a status changed and nothing else, which is the sentence that
    started this work."""
    for event in ("contribution.status_updated", "transfer.status_updated"):
        fields = registry.subject_fields()[event]
        assert "status" in fields, f"{event} does not capture the status it set"
        assert event in actions.PREVIOUS_FIELDS, f"{event} does not pre-read the status it left"
        assert actions.PREVIOUS_FIELDS[event].fields == ("status",)


# --------------------------------------------------------------------------------------------
# a label names the RECORD, never its type
# --------------------------------------------------------------------------------------------
# `describe.ts::namedSubject` prints "{verb} {label}", so a label naming a TYPE gives "Started
# Onboarding" on a firm running fifty onboardings — true of the template, silent about what
# happened. The two sets below make that rule checkable: every resolver is declared as one shape or
# the other and their union is asserted against `_ENTITY_RESOLVERS`, so a later one cannot skip it.

#: THE LABEL CARRIES A PERSON — either because the record IS one, or because it belongs to one.
#: Asserted structurally: the SQL must read `crm.contacts` or `auth.users`.
_LABELS_NAME_A_PERSON = frozenset(
    {
        "contact",
        "user",
        "account",
        "document",
        "task",
        "instruction",
        "transaction",
        "conversation",
        "conversation_adviser",
        "journey",
        "case",
        "contribution",
        "transfer",
        "document_request",
        "beneficiary",
        "file",
        "notification",
        "storage_credential",
    }
)

#: THE RECORD'S OWN NAME IS ITS IDENTITY, and appending anything would be noise. A definition the
#: firm authored once (a model, a product, a fee package, a template, a flow, a form, a dataset, an
#: automation, a notification rule); or the firm or its environment.
_LABELS_ARE_SELF_NAMING = frozenset(
    {
        "tenant",
        "environment",
        "model",
        "template",
        "flow",
        "form",
        "automation",
        "content",
        "dataset",
        "document_type",
        "email_template",
        "message_group",
        "product",
        "fee_package",
        "catalogue_provider",
        "notification_rule",
    }
)


def test_every_resolver_declares_which_shape_of_label_it_is() -> None:
    """A new resolver must be classified, because the two shapes are the whole rule and an
    unclassified one is how "Started Onboarding" got written in the first place."""
    declared = _LABELS_NAME_A_PERSON | _LABELS_ARE_SELF_NAMING
    assert not (_LABELS_NAME_A_PERSON & _LABELS_ARE_SELF_NAMING), "a kind cannot be both"
    assert declared == set(_ENTITY_RESOLVERS), (
        f"undeclared resolvers: {sorted(set(_ENTITY_RESOLVERS) - declared)}; "
        f"declared but absent: {sorted(declared - set(_ENTITY_RESOLVERS))}"
    )


def test_a_label_that_should_name_a_person_actually_reads_one() -> None:
    """The structural half of the rule. It cannot check that the join is the RIGHT one — the
    seeded test below does that — but it does catch the failure this change set was raised for: a
    client-scoped record labelled from its own table alone, which names the wrapper, the template
    or the flow and leaves the reader unable to say whose it was."""
    for kind in sorted(_LABELS_NAME_A_PERSON):
        sql = _ENTITY_RESOLVERS[kind]
        assert "crm.contacts" in sql or "auth.users" in sql, (
            f"the {kind!r} label names no person: {sql}"
        )


@pytest.mark.asyncio
async def test_a_client_scoped_label_carries_the_client(service_session: AsyncSession) -> None:
    """The join, not just the SQL shape, proved against a real row.

    The two tests above cannot tell a resolver that joins the WRONG column from one that joins the
    right one: `ct.id = a.contact_id` and `ct.id = a.id` both compile and both run, and the second
    returns a client-less label for every account in the estate — indistinguishable from an
    honestly client-less record. Only a seeded row read back through `_resolve_entities` catches it.

    One tenant, one contact and one row per client-scoped kind, each built the way the real route
    leaves it, checked against the exact label text. Two accounts cover the stutter guard; the
    document request covers the three-item cap.
    """
    mark = uuid.uuid4().hex[:10]  # unique per run: `service_session` commits, nothing truncates.

    tenant_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO auth.tenants (uid, alias, name) VALUES (:u, :u, :n) RETURNING id"
                ),
                {"u": f"ten_evc{mark}", "n": f"Event Content Co {mark}"},
            )
        ).scalar_one()
    )
    contact_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO crm.contacts (tenant_id, uid, type, first_name, surname) "
                    "VALUES (:t, :uid, 'individual', 'Margaret', 'Cole') RETURNING id"
                ),
                {"t": tenant_id, "uid": f"con_evc{mark}"},
            )
        ).scalar_one()
    )

    # Two accounts: one whose own name does not carry the client ("Individual Savings Account" is
    # what fifty of them are called), one whose name already does.
    isa_uid = f"acc_evc{mark}a"
    isa_account_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO platform.accounts (tenant_id, uid, contact_id, name) "
                    "VALUES (:t, :uid, :c, 'Individual Savings Account') RETURNING id"
                ),
                {"t": tenant_id, "uid": isa_uid, "c": contact_id},
            )
        ).scalar_one()
    )
    sipp_uid = f"acc_evc{mark}b"
    await service_session.execute(
        text(
            "INSERT INTO platform.accounts (tenant_id, uid, contact_id, name) "
            "VALUES (:t, :uid, :c, 'Margaret Cole SIPP')"
        ),
        {"t": tenant_id, "uid": sipp_uid, "c": contact_id},
    )

    instruction_uid = f"ins_evc{mark}"
    await service_session.execute(
        text(
            "INSERT INTO platform.instructions "
            "  (tenant_id, uid, account_id, contact_id, kind, amount) "
            "VALUES (:t, :uid, :acc, :c, 'withdrawal', 500)"
        ),
        {"t": tenant_id, "uid": instruction_uid, "acc": isa_account_id, "c": contact_id},
    )

    task_uid = f"tsk_evc{mark}"
    await service_session.execute(
        text(
            "INSERT INTO tasks.tasks "
            "  (tenant_id, uid, assignee_kind, title, subject_type, subject_id) "
            "VALUES (:t, :uid, 'user', 'Review', 'contact', :subj)"
        ),
        {"t": tenant_id, "uid": task_uid, "subj": str(contact_id)},
    )

    beneficiary_uid = f"ben_evc{mark}"
    await service_session.execute(
        text(
            "INSERT INTO crm.beneficiaries "
            "  (tenant_id, uid, account_id, contact_id, name, proportion) "
            "VALUES (:t, :uid, :acc, :c, 'Jane Cole', 100)"
        ),
        {"t": tenant_id, "uid": beneficiary_uid, "acc": isa_account_id, "c": contact_id},
    )

    document_uid = f"doc_evc{mark}"
    await service_session.execute(
        text(
            "INSERT INTO documents.documents "
            "  (tenant_id, uid, document_type, name, intended_contact_id) "
            "VALUES (:t, :uid, 'suitability_report', 'Suitability report', :c)"
        ),
        {"t": tenant_id, "uid": document_uid, "c": contact_id},
    )

    request_uid = f"req_evc{mark}"
    request_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO documents.requests (tenant_id, uid, contact_id) "
                    "VALUES (:t, :uid, :c) RETURNING id"
                ),
                {"t": tenant_id, "uid": request_uid, "c": contact_id},
            )
        ).scalar_one()
    )
    # Four items, inserted in this order — the label caps at the first three (id order), so a
    # fourth on the row proves the cap rather than the label happening to be short.
    for n, item_title in enumerate(("Passport", "Proof of address", "Bank statement", "P60")):
        await service_session.execute(
            text(
                "INSERT INTO documents.request_items (tenant_id, request_id, uid, action, title) "
                "VALUES (:t, :r, :uid, 'upload', :title)"
            ),
            {"t": tenant_id, "r": request_id, "uid": f"{request_uid}i{n}", "title": item_title},
        )

    transaction_id = int(
        (
            await service_session.execute(
                text(
                    "INSERT INTO platform.transactions "
                    "  (tenant_id, contact_id, source_system, source_transaction_id, type, "
                    "   amount, currency) "
                    "VALUES (:t, :c, 'seccl', :src, 'deposit', 1500, 'GBP') RETURNING id"
                ),
                {"t": tenant_id, "c": contact_id, "src": f"txn-evc-{mark}"},
            )
        ).scalar_one()
    )

    notification_uid = f"ntf_evc{mark}"
    await service_session.execute(
        text(
            "INSERT INTO notifications.notifications "
            "  (tenant_id, uid, recipient_kind, recipient_contact_id, portal, event_type, "
            "   category, title, source_kind) "
            "VALUES (:t, :uid, 'contact', :c, 'client', 'document.shared', 'documents', "
            "  'A document was shared with you', 'domain')"
        ),
        {"t": tenant_id, "uid": notification_uid, "c": contact_id},
    )

    # Shaped like real audit rows: a bare `uid` resource resolves via the event's resource
    # (`instruction`, `task`, `document`, `notification`); a named field resolves on its own
    # regardless of resource (`account_uid`, `beneficiary_uid`, `req_uid`); the transaction has no
    # uid at all, so it is carried as a captured subject id, the way a contribution classification
    # writes it.
    items: list[dict[str, object]] = [
        {"event_type": "account.updated", "path_params": {"account_uid": isa_uid}},
        {"event_type": "account.updated", "path_params": {"account_uid": sipp_uid}},
        {"event_type": "instruction.created", "path_params": {"uid": instruction_uid}},
        {"event_type": "task.created", "path_params": {"uid": task_uid}},
        {"event_type": "beneficiary.removed", "path_params": {"beneficiary_uid": beneficiary_uid}},
        {"event_type": "document.created", "path_params": {"uid": document_uid}},
        {"event_type": "document_request.created", "path_params": {"req_uid": request_uid}},
        {
            "event_type": "contribution.transaction_classified",
            "metadata": {"subject": {"transaction_id": transaction_id}},
        },
        {"event_type": "notification.created", "path_params": {"uid": notification_uid}},
    ]
    await _resolve_entities(service_session, items)

    def label(index: int, kind: str) -> object:
        entities = items[index]["entities"]
        assert isinstance(entities, list)
        matches = [e["label"] for e in entities if e["kind"] == kind]
        assert len(matches) == 1, entities
        return matches[0]

    assert label(0, "account") == "Individual Savings Account - Margaret Cole"
    assert label(1, "account") == "Margaret Cole SIPP"  # already carries the client — not doubled
    assert label(2, "instruction") == "Margaret Cole - Withdrawal £500.00"
    assert label(3, "task") == "Review - Margaret Cole"
    assert label(4, "beneficiary") == "Jane Cole - Margaret Cole"
    assert label(5, "document") == "Suitability report - Margaret Cole"
    assert label(6, "document_request") == (
        "Margaret Cole - Passport, Proof of address, Bank statement"
    )
    assert label(7, "transaction") == "Margaret Cole - Deposit GBP 1500.00"
    assert label(8, "notification") == "A document was shared with you - Margaret Cole"


# --------------------------------------------------------------------------------------------
# the change diff — what a field WAS, beside what it became
# --------------------------------------------------------------------------------------------


def test_every_changed_columns_event_is_a_declared_event() -> None:
    """A spec for an event type nothing emits is dead weight that will never be noticed: the
    pre-read simply never fires, and the panel goes on showing only the after-state."""
    declared = set(registry.subject_fields()) | set(registry.body_capture_events())
    declared |= set(registry.changed_key_events())
    for event in actions.CHANGED_COLUMNS:
        assert event in declared, f"{event!r} has a row spec but is not a declared event"


def test_every_changed_columns_spec_binds_a_param_its_routes_declare() -> None:
    """The pre-read binds BY NAME off the routed scope's path params. A spec naming a param no
    door of that act declares reads `None`, returns nothing, and the diff silently never appears —
    the same failure mode `test_actions_tripwire` pins for `PREVIOUS_FIELDS`."""
    from app.main import app
    from tests.conftest import api_routes

    doors: dict[str, list[set[str]]] = {}
    for route in api_routes(app):
        if isinstance(route, APIRoute):
            doors.setdefault(actions.event_type_for(route), []).append(
                set(re.findall(r"\{(\w+)\}", route.path))
            )
    for event, spec in actions.CHANGED_COLUMNS.items():
        params = doors.get(event)
        assert params, f"{event!r} has a row spec and no live route"
        for one_door in params:
            assert spec.param in one_door, (
                f"{event!r} binds {spec.param!r}, which one of its doors does not declare"
            )


def test_a_secret_shaped_column_is_never_compared() -> None:
    """The belt runs on the NAME and BEFORE the comparison, so a credential column cannot land
    even as evidence that it moved. This is the runtime half of the same rule `extract_subject`
    and `capture_previous` each carry."""
    diff = actions.diff_rows(
        "task.updated",
        {"workos_api_key": "old", "name": "A"},
        {"workos_api_key": "new", "name": "B"},
    )
    assert diff == {"name": {"from": "A", "to": "B"}}


def test_a_column_that_did_not_move_is_not_recorded() -> None:
    """The whole affordability argument: a PATCH of one field records one field however wide the
    table is — and a save that changed nothing records nothing, which is the honest answer rather
    than a row implying something happened."""
    assert actions.diff_rows("task.updated", {"a": 1, "b": 2}, {"a": 1, "b": 2}) is None
    assert actions.diff_rows("task.updated", {"a": 1, "b": 2}, {"a": 1, "b": 3}) == {
        "b": {"from": 2, "to": 3}
    }


def test_bookkeeping_columns_are_left_out() -> None:
    """`updated_at` moves on every write and is not what anybody means by a change."""
    assert actions.diff_rows("task.updated", {"updated_at": "t1"}, {"updated_at": "t2"}) is None


def test_an_oversized_value_is_recorded_as_its_shape() -> None:
    """A `theme` or a `data` column is a whole document. Two of them side by side is not a change
    a reader can see, and storing them is the trail becoming a copy of the table."""
    before = {"theme": {"k": "x" * (actions.MAX_CHANGED_VALUE_CHARS + 50)}}
    after = {"theme": {"k": "y" * (actions.MAX_CHANGED_VALUE_CHARS + 50)}}
    diff = actions.diff_rows("task.updated", before, after)
    assert diff is not None
    assert diff["theme"]["from"].endswith("characters]")
    assert diff["theme"]["to"].endswith("characters]")


def test_an_overflowing_diff_says_how_much_it_dropped() -> None:
    """Dropped WITH A COUNT, never silently: a bulk edit that reports three changes and hides
    forty is a wrong answer told confidently, which is the exact failure the body cap exists to
    avoid."""
    wide = actions.MAX_CHANGED_VALUE_CHARS - 10
    before = {f"c{i}": "a" * wide for i in range(40)}
    after = {f"c{i}": "b" * wide for i in range(40)}
    diff = actions.diff_rows("task.updated", before, after)
    assert diff is not None
    assert actions.OMITTED_CHANGES_KEY in diff
    assert "further columns changed" in diff[actions.OMITTED_CHANGES_KEY]["to"]
    assert len(json.dumps(diff)) < actions.MAX_STORED_CHANGES_CHARS * 2


def test_a_missing_read_yields_no_diff_rather_than_half_a_story() -> None:
    """Fail-quiet by contract: every read can come back empty (a deleted row, a junk path param,
    a database that blinked), and half a diff would assert a change that was never observed."""
    assert actions.diff_rows("task.updated", None, {"a": 1}) is None
    assert actions.diff_rows("task.updated", {"a": 1}, None) is None


@pytest.mark.asyncio
async def test_every_row_spec_reads_the_schema_it_names(service_session: AsyncSession) -> None:
    """EXECUTE every spec, empty-handed — `_ENTITY_RESOLVERS`' rule, for the same reason. A spec
    naming a table or a key column wrong does not raise anywhere a person sees it: `read_row`
    catches, logs at debug, and the event is written with no `changes` at all, which is
    indistinguishable from a save that moved nothing."""
    for event, spec in actions.CHANGED_COLUMNS.items():
        bind = f"CAST(:key AS {spec.cast})" if spec.cast else ":key"
        sql = f"SELECT to_jsonb(t) AS row FROM {spec.table} t WHERE t.{spec.key} = {bind} LIMIT 2"  # noqa: S608
        key = "00000000-0000-0000-0000-000000000000" if spec.cast else "no-such-row"
        try:
            await service_session.execute(text(sql), {"key": key})
        except Exception as exc:  # noqa: BLE001 - the failure IS the assertion
            pytest.fail(f"the {event!r} row spec does not run: {exc}")


# --------------------------------------------------------------------------------------------
# a client's own data: recorded in full, and GATED rather than masked
# --------------------------------------------------------------------------------------------
# These fields were masked at capture for a while (a sort code kept as its last four, a
# vulnerability note dropped outright). That was the right call while anyone holding
# `activity:read` could read the whole firm's trail - an ADVISER has that permission and not
# `contacts:read_all`, so the trail was a way round the client book.
#
# `audit_stream.CONTACT_SCOPE_SQL` closes that properly: a row naming a client you may not open is
# not returned at all. Once the reader is someone who may open the record, masking the value buys
# nothing - they can read it on the record itself - and costs the trail its whole point, which is
# saying what the value WAS. So the mask came off and the gate went in.
#
# The CREDENTIAL belt is untouched and deliberately so: a password is not a client's data, and no
# reader is ever meant to see one. The last test here pins that distinction.


def test_a_clients_bank_details_are_recorded_as_they_are() -> None:
    """The trail says what changed. A masked sort code cannot, and the reader who can see this row
    is by definition one who can open the record it is about (`CONTACT_SCOPE_SQL`)."""
    body = json.dumps(
        {
            "sort_code": "12-34-56",
            "account_number": "12345678",
            "iban": "GB29NWBK60161331926819",
            "name": "A Client",
        }
    )
    kept = actions.extract_body("client.bank_account_updated", body.encode())
    assert kept == {
        "sort_code": "12-34-56",
        "account_number": "12345678",
        "iban": "GB29NWBK60161331926819",
        "name": "A Client",
    }


def test_the_row_diff_states_both_sides_of_a_bank_change() -> None:
    """ "12345642 -> 13345642" is the answer somebody opens this event for. Two masked tails are
    not - they can be identical while the number changed."""
    before = {"bank_account": {"sort_code": "12-34-56", "account_number": "12345678"}}
    after = {"bank_account": {"sort_code": "98-76-54", "account_number": "87654321"}}
    diff = actions.diff_rows("client.bank_account_updated", before, after)
    assert diff is not None
    assert diff["bank_account"]["from"] == {"sort_code": "12-34-56", "account_number": "12345678"}
    assert diff["bank_account"]["to"] == {"sort_code": "98-76-54", "account_number": "87654321"}


def test_a_vulnerability_note_is_recorded_rather_than_dropped() -> None:
    """A vulnerability note is the most consequential thing on that form, so a trail that drops it
    cannot answer the complaint review it exists for. Who may READ it is the gate's question."""
    before = {"vulnerability_notes": "Recently bereaved", "is_vulnerable": False}
    after = {"vulnerability_notes": "Recently bereaved; check in monthly", "is_vulnerable": True}
    diff = actions.diff_rows("client.vulnerability_updated", before, after)
    assert diff is not None
    assert diff["vulnerability_notes"] == {
        "from": "Recently bereaved",
        "to": "Recently bereaved; check in monthly",
    }
    assert diff["is_vulnerable"] == {"from": False, "to": True}


def test_a_credential_is_still_never_recorded() -> None:
    """THE LINE THAT DID NOT MOVE. A client's data is theirs and a reader who may open their record
    may read it; a credential is nobody's to read, so the credential belt stays exactly where it
    was - in the body, nested, and on the column name in the diff."""
    kept = actions.extract_body(
        "environment.registered",
        json.dumps({"name": "prod", "secrets": {"workos_api_key": "sk_live_x"}}).encode(),
    )
    assert kept is not None
    # A secret-shaped KEY masks its whole subtree, so the bag goes, not just the value in it.
    assert kept["secrets"] == actions.REDACTED
    assert kept["name"] == "prod"
    diff = actions.diff_rows("client.updated", {"api_key": "old"}, {"api_key": "new"})
    assert diff is None
