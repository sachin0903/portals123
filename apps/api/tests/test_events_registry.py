"""The event-schema registry (`app/event_schema/registry.py`) is the single source the action-event
capture allowlists project from. These pins guarantee (a) the projection is byte-identical to the
three hand-kept dicts it replaced — no event or field silently dropped or renamed in the fold — and
(b) the registry's per-field entity kinds agree with the read side's `admin._ENTITY_FIELD_KINDS`, so
the one declaration can supersede that global dict in a later increment without a mismatch.

The live-route / redact / resolvability guarantees are NOT re-asserted here: `SUBJECT_FIELDS` /
`RESPONSE_SUBJECT_FIELDS` are now registry projections, so `test_actions_tripwire.py` validates the
registry transitively (a stale event type, an undeclared field or a secret-shaped one fails there).
"""

from __future__ import annotations

import re

from app import audit_actions as actions
from app.event_schema import registry
from app.event_schema.registry import FieldSource, FieldType
from app.routers.admin import _ENTITY_FIELD_KINDS

#: `resource.verb` — the vocabulary since the rename. The old assertion was `startswith("action.")`,
#: which is now exactly the shape that must NOT appear: an `action.`-prefixed entry would be a
#: legacy
#: name nothing emits, so the schema it declares would never be applied and the drift would be
#: silent.
_SHAPE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

# The FROZEN contract — the exact three literals the registry replaced. If a registry edit changes
# what is captured, it must change this snapshot too (and a reviewer sees the behavioural diff).

EXPECTED_RESPONSE_SUBJECT_FIELDS: dict[str, dict[str, str]] = {
    "client.created": {"id": "contact_id"},
    "file.uploaded": {"contact_id": "contact_id"},
    "member.invited": {"user_id": "user_id"},
    "member.created": {"user_id": "user_id"},
    # Which photos an operator uploaded for a client: the request is multipart, so only the
    # response can say. The photos themselves and the operator's note never land.
    "aml.identity_photos_uploaded": {"photos": "photos"},
    # The only `list[].field` paths in the map: one upload request may create several documents, and
    # both the uid and the filed client are fields of an element of `UploadResult.documents`.
    "document.uploaded": {
        "documents[].uid": "document_uids",
        "documents[].client_contact_id": "client_contact_id",
    },
    # Both ends of a chat channel emit these, and only the STAFF end carries a conversation on its
    # route — a client's thread route derives theirs from their own scope. The id is lifted off the
    # response so the client-sent half can name its recipient too, and it resolves to the ADVISER
    # rather than the conversation's client, who on that send IS the sender.
    # `contact_id` names WHOSE channel it is - the sender on a client's own message, and what the
    # adviser's notice must say. The actor cannot supply it: the login is an `auth.users` row and
    # the client a `crm.contacts` one, so the names differ even at capacity 'self', and one login
    # may act for several contacts. It also makes `context._fallback_contact_id` resolve
    # {client.name} with no special case. The message BODY is still never captured.
    "message.sent": {
        "conversation_id": "conversation_adviser_id",
        "contact_id": "contact_id",
    },
    "message.attachment_sent": {
        "conversation_id": "conversation_adviser_id",
        "contact_id": "contact_id",
        # WHICH file was sent. "Sent an attachment" is true of every one of these rows; the
        # filename is the only thing that tells two of them apart.
        "attachments[].filename": "attachment_filenames",
    },
    # Which Intelliflo client an admin released a client from: the request has no body, so only the
    # response can say. The Intelliflo client's id, nothing of the client's own record.
    "integration.client_unlinked_from_io": {"client_id_io": "client_id_io"},
}


#: The events whose body KEY NAMES are recorded (`EventType.changed_keys`). Frozen for the
#: same reason as the two above: turning this on for an event changes what the trail stores
#: about every one of that firm's requests, so it is a decision a reviewer should see.
EXPECTED_CHANGED_KEY_EVENTS: frozenset[str] = frozenset(
    {
        "account.bank_accounts_updated",
        "account.fee_packages_updated",
        "account.regular_movements_updated",
        "automation.settings_updated",
        "automation.updated",
        "branding.updated",
        "client.bank_account_updated",
        "client.updated",
        "content.updated",
        "contribution.categorised",
        "dataset.updated",
        "document.updated",
        "document_request.updated",
        "email_template.updated",
        "fee_package.updated",
        "firm.ai_settings_updated",
        "firm.provider_updated",
        "firm.updated",
        "flow.updated",
        "form.updated",
        "member.profile_updated",
        "model.updated",
        "notification_rule.updated",
        "product.updated",
        "provider.updated",
        "task.updated",
        "template.updated",
    }
)


EXPECTED_BODY_CAPTURE_EVENTS: frozenset[str] = frozenset(
    {
        "account.bank_accounts_updated",
        "account.beneficiary_added",
        "account.fee_packages_updated",
        "account.regular_movements_updated",
        "automation.created",
        "automation.platform_pause_updated",
        "automation.reordered",
        "automation.settings_updated",
        "automation.tested",
        "automation.updated",
        "branding.updated",
        "client.adviser_assigned",
        "client.bank_account_updated",
        "client.created",
        "client.note_added",
        "client.updated",
        "client.vulnerability_updated",
        "content.created",
        "content.updated",
        "content_category.created",
        "contribution.categorised",
        "contribution.created",
        "contribution.transaction_classified",
        "dataset.created",
        "dataset.updated",
        "dataset.version_added",
        "document.batch_generated",
        "document.generated",
        "document.updated",
        "document_request.created",
        "document_request.form_submitted",
        "document_request.item_reassigned",
        "document_type.created",
        "document_type.updated",
        "email_template.created",
        "email_template.previewed",
        "email_template.test_sent",
        "email_template.updated",
        "email_template.version_saved",
        "environment.registered",
        "firm.ai_settings_updated",
        "firm.created",
        "firm.data_reset_requested",
        "firm.provider_updated",
        "firm.updated",
        "flow.created",
        "flow.simulated",
        "flow.updated",
        "flow.version_saved",
        "form.created",
        "form.fields_updated",
        "form.response_submitted",
        "form.updated",
        "illustration.created",
        "illustration.investment_created",
        "instruction.created",
        "journey.created",
        "journey.step_actioned",
        "journey.step_form_submitted",
        "journey.terms_accepted",
        "member.created",
        "member.invited",
        "member.profile_updated",
        "message.sent",
        "message_group.created",
        "message_group.document_requested",
        "message_group.message_sent",
        "notification.client_preferences_updated",
        "notification.preferences_updated",
        "notification.settings_updated",
        "notification_rule.created",
        "notification_rule.updated",
        "platform.action_ran",
        "role.created",
        "task.completed",
        "task.created",
        "task.updated",
        "template.created",
        "template.preview_pdf_rendered",
        "template.preview_rendered",
        "template.updated",
        "template.version_saved",
        "transfer.created",
    }
)


def test_response_subject_fields_projection_is_unchanged() -> None:
    assert registry.response_subject_fields() == EXPECTED_RESPONSE_SUBJECT_FIELDS


def test_free_text_subject_fields_projection_is_unchanged() -> None:
    # The guarded exception, and the rule that bounds it (`registry._text`): free text is
    # admitted ONLY where the text IS the act. The covering note on a document send, the
    # reason a contribution or a journey was cancelled, and the SQL an operator ran against a
    # firm's warehouse. A field that merely HAPPENS to be prose - a name, a note, a description -
    # is not admitted: the record itself holds it, and an audit row is read by more people than
    # the record is.
    assert registry.free_text_subject_fields() == frozenset({"message", "reason", "sql"})


def test_changed_key_events_projection_is_unchanged() -> None:
    assert actions.CHANGED_KEY_EVENTS == registry.changed_key_events()


def test_body_capture_events_projection_is_unchanged() -> None:
    """Frozen for the same reason as the two above, and more so: turning body capture on for an
    event widens what the trail stores about every one of that firm's requests — from a handful of
    declared values to the request itself. That belongs in a diff a reviewer reads, not in a flag
    somebody flips."""
    assert actions.BODY_CAPTURE_EVENTS == registry.body_capture_events()


def test_actions_reads_its_allowlists_from_the_registry() -> None:
    """The rewire itself: `actions.py`'s live constants ARE the registry projections, so there is
    one source, not two that could drift."""
    assert actions.SUBJECT_FIELDS == registry.subject_fields()
    assert actions.RESPONSE_SUBJECT_FIELDS == registry.response_subject_fields()
    assert actions._FREE_TEXT_SUBJECT_FIELDS == registry.free_text_subject_fields()


def test_entity_kinds_agree_with_the_read_side() -> None:
    """Every id field the registry types must name the SAME entity kind the read side resolves it
    into — so the registry can supersede `admin._ENTITY_FIELD_KINDS` for subject/response fields
    without changing a single resolution."""
    for name, kind in registry.entity_field_kinds().items():
        assert name in _ENTITY_FIELD_KINDS, (
            f"registry declares {name!r} as entity kind {kind!r}, but the read side "
            f"(_ENTITY_FIELD_KINDS) does not resolve it — a captured id that never becomes a chip."
        )
        assert _ENTITY_FIELD_KINDS[name] == kind, (
            f"{name}: registry says kind {kind!r}, read side says {_ENTITY_FIELD_KINDS[name]!r}"
        )


def test_field_typing_is_internally_consistent() -> None:
    """The invariants the projections rely on: an entity_kind exists exactly for id-shaped fields,
    a response_key exists exactly for RESPONSE fields, and free_text implies the TEXT type."""
    seen: set[str] = set()
    for et in registry.REGISTRY:
        assert et.event_type not in seen, f"duplicate event type {et.event_type}"
        seen.add(et.event_type)
        assert _SHAPE.match(et.event_type), et.event_type
        assert et.fields or et.changed_keys or et.capture_body, (
            f"{et.event_type}: an event that captures nothing at all - no fields, no changed keys "
            "and no body - should not be in the registry"
        )
        for f in et.fields:
            is_id = f.type in (FieldType.ID, FieldType.ID_LIST)
            assert (f.entity_kind is not None) == is_id, (
                f"{et.event_type}.{f.name}: entity_kind must be set iff the field is an id"
            )
            assert (f.response_key is not None) == (f.source is FieldSource.RESPONSE), (
                f"{et.event_type}.{f.name}: response_key must be set iff source is RESPONSE"
            )
            if f.free_text:
                assert f.type is FieldType.TEXT, f"{et.event_type}.{f.name}: free_text ⇒ TEXT"
