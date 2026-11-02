"""The event-schema registry (`app/event_schema/registry.py`) — one declaration of what every
curated action event carries.

Today the "schema" of an action event is spread across several hand-kept lists that mirror one
another by convention: `actions.SUBJECT_FIELDS` (what to capture off the request),
`actions.RESPONSE_SUBJECT_FIELDS` (the created id to lift off the response), the
`_FREE_TEXT_SUBJECT_FIELDS` exception, and the read side's `admin._ENTITY_FIELD_KINDS` (which
captured field names name which entity kind). Each is edited in step with the others by discipline
plus pin tests — exactly the drift `app/permissions.py` and the flow/block CONTRACT files exist to
remove.

This module is the keystone of the convergence: it declares each subject-bearing event ONCE, as a
typed `EventType` with typed `EventField`s, and PROJECTS the shapes the existing consumers already
expect. `actions.py` now reads its capture allowlists from here (one source, not three), and the
read side's per-field entity kinds are pinned against this declaration. The next increments all
project from this same object: driving `_resolve_entities` off it, a mirrored `event-contract.json`
+ generated TS, and feeding the notification/email variable picker from it.

Scope, deliberately:
- It declares the ~38 SUBJECT-bearing events — the curated, interesting subset something actually
  consumes. The long tail of bare mutating routes keeps the generic de-slug humanising fallback; a
  route earns a registry entry when it has fields worth naming.
- It does NOT own the event NAME. `event_type` is still derived from the route
  (`actions.event_type_for` = `"action." + operationId`) — load-bearing for OpenAPI, the generated
  TS client, and the AML-exclusion prefix. The registry declares the SCHEMA attached to that name.
- It does NOT fold `PREVIOUS_FIELDS`: that is a per-event pre-read RECIPE (it carries SQL), not
  schema, so it stays in `actions.py`. A field's "previous" (before) value is the same field read at
  a different time, not a different declaration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FieldSource(StrEnum):
    """Where a captured field's value comes from on the wire."""

    #: Named in the REQUEST (body or query) — the `SUBJECT_FIELDS` allowlist.
    SUBJECT = "subject"
    #: Lifted from the 2xx RESPONSE body (a created row's id) — the `RESPONSE_SUBJECT_FIELDS`
    #: allowlist. The subject key it lands under is `EventField.name`; the response body key it is
    #: read from is `EventField.response_key`.
    RESPONSE = "response"


class FieldType(StrEnum):
    """The shape of a captured value. Ids and enumerations are the rule; free text is the one
    guarded exception (`TEXT`). Types are documentary today and drive entity resolution
    (`ID`/`ID_LIST` carry an `entity_kind`); they are what a schema-driven variable picker will read
    to label and example each field."""

    ID = "id"  # a single entity identifier (int / uuid / text uid) — carries entity_kind
    ID_LIST = "id_list"  # a list of entity identifiers (e.g. contact_ids) — carries entity_kind
    ENUM = "enum"  # a closed enumeration (kind, status, category, decision, portal, priority…)
    AMOUNT = "amount"  # a money figure (major units)
    DATE = "date"  # a date / datetime
    COUNT = "count"  # an integer count (e.g. expires_in_days)
    CODE = "code"  # an opaque short code / slug with no entity resolver (role_slug, flow_code…)
    CODE_LIST = "code_list"  # a list of such codes (a member invited to several roles at once)
    BOOL = "bool"  # a boolean (e.g. the AML screen's `monitor` flag)
    PERMISSION_LIST = "permission_list"  # a list of permission slugs (role permission grants)
    TEXT = "text"  # deliberately free text (the one exception — see EventField.free_text)


@dataclass(frozen=True)
class EventField:
    """One field an event carries. `entity_kind` is set exactly for `ID`/`ID_LIST` fields and names
    the kind the read side resolves the id into (must equal `admin._ENTITY_FIELD_KINDS[name]`).
    `response_key` is set exactly for `source=RESPONSE` fields (the response body key). `free_text`
    marks the guarded prose exception — captured TRUNCATED, never dropped for length."""

    name: str
    type: FieldType
    source: FieldSource = FieldSource.SUBJECT
    entity_kind: str | None = None
    response_key: str | None = None
    free_text: bool = False


@dataclass(frozen=True)
class EventType:
    """One curated event type and the fields it carries, in capture order.

    ``fields`` names the values worth having as first-class, resolvable subjects. ``changed_keys``
    records the NAMES of the top-level body keys an update carried and never their values — which
    is what makes it safe to turn on across forty update routes that no per-field allowlist could
    reach. ``capture_body`` keeps the redacted request itself, for the eighty-eight acts whose
    content is free text, a form's answers or a nested object.

    `capture_body` WIDENS WHAT THE TRAIL HOLDS: a client's date of birth, tax position and address
    now land in `audit.actions`, readable by every holder of `activity:read`. Bounded by existing
    access rather than new, but deliberate. A secret-shaped key never lands — `actions.redact`
    masks it and the tripwire refuses a declaration that names one, which is why an event whose
    body is only credentials stays unreplayable by design.
    """

    event_type: str
    fields: tuple[EventField, ...]
    changed_keys: bool = False
    capture_body: bool = False


def _id(name: str, kind: str) -> EventField:
    return EventField(name, FieldType.ID, entity_kind=kind)


def _ids(name: str, kind: str) -> EventField:
    return EventField(name, FieldType.ID_LIST, entity_kind=kind)


def _enum(name: str) -> EventField:
    return EventField(name, FieldType.ENUM)


def _code(name: str) -> EventField:
    return EventField(name, FieldType.CODE)


def _codes(name: str) -> EventField:
    return EventField(name, FieldType.CODE_LIST)


def _bool(name: str) -> EventField:
    return EventField(name, FieldType.BOOL)


def _date(name: str) -> EventField:
    return EventField(name, FieldType.DATE)


def _amount(name: str) -> EventField:
    return EventField(name, FieldType.AMOUNT)


def _text(name: str) -> EventField:
    """The guarded free-text exception. Admitted ONLY where the text IS the act — the covering
    note on a document send, the reason a cancellation was made, the SQL an operator ran. Captured
    TRUNCATED, never dropped for length. A field that merely HAPPENS to be prose (a name, a note,
    a description) is not admitted: the record itself holds it, and the trail does not need it."""
    return EventField(name, FieldType.TEXT, free_text=True)


def _resp_id(name: str, kind: str, response_key: str) -> EventField:
    return EventField(
        name, FieldType.ID, source=FieldSource.RESPONSE, entity_kind=kind, response_key=response_key
    )


def _resp_codes(name: str, response_key: str) -> EventField:
    """A closed-ish VALUE the response yields, not an id — a filename, a code, a key. No
    `entity_kind`, because there is no record to resolve it into: the value IS the answer. A list,
    because the responses that carry one carry several (one message may attach several files)."""
    return EventField(
        name, FieldType.CODE_LIST, source=FieldSource.RESPONSE, response_key=response_key
    )


def _resp_ids(name: str, kind: str, response_key: str) -> EventField:
    """As `_resp_id`, but the response yields SEVERAL ids — an upload creates one document per file,
    so its subject is a list. Declared `ID_LIST` rather than `ID` because the type is what a
    schema-driven variable picker reads; calling a list a single id would mislabel it there."""
    return EventField(
        name,
        FieldType.ID_LIST,
        source=FieldSource.RESPONSE,
        entity_kind=kind,
        response_key=response_key,
    )


# The declaration. Order within each event's `fields` mirrors the capture order the consumers
# iterate (so the projected `SUBJECT_FIELDS` tuples are byte-identical to what they were). Events
# with BOTH a subject and a response field (the two invites) list the subject field(s) first.
REGISTRY: tuple[EventType, ...] = (
    # -- clients <-> logins / advisers (which login/adviser was linked to which client) -----------
    EventType("client.portal_user_linked", (_id("user_id", "user"), _enum("capacity"))),
    EventType("client.adviser_assigned", (_id("adviser_id", "user"),), capture_body=True),
    # -- members / portals / roles (which role was handed out; the invited login off the response) -
    EventType(
        "member.invited",
        (_code("role_slug"), _resp_id("user_id", "user", "user_id")),
        capture_body=True,
    ),
    EventType("member.portal_granted", (_code("role_slug"),)),
    # BOTH SPELLINGS, because `GrantPortal` accepts either and the UI sends the LIST: a
    # singular-only declaration captured nothing on a real request, so the row showed the
    # pre-read ("was Adviser") with no sign of what it became.
    EventType("member.portal_role_updated", (_code("role_slug"), _codes("role_slugs"))),
    EventType("role.created", (_code("slug"), _enum("portal")), capture_body=True),
    EventType("role.permissions_updated", (EventField("permissions", FieldType.PERMISSION_LIST),)),
    EventType(
        "member.created",
        (_code("role_slug"), _resp_id("user_id", "user", "user_id")),
        capture_body=True,
    ),
    # The ops door for granting a portal, setting a portal role, creating a role and setting its
    # permissions declared the same four entries again, because it was a separate event type per
    # route. It is one act per name now — the schema of an act cannot depend on which door reached
    # it
    # — and the four ops copies were field-for-field identical, so this is a de-duplication rather
    # than a choice between two shapes.
    # -- money movement (amount is material to the trail — a deliberate inclusion) -----------------
    EventType(
        "instruction.created",
        (
            _enum("kind"),
            _id("account_id", "account"),
            EventField("amount", FieldType.AMOUNT),
            _id("model_id", "model"),
            EventField("fee_package_id", FieldType.COUNT),
        ),
        capture_body=True,
    ),
    # -- contributions / transfers -----------------------------------------------------------------
    EventType(
        "contribution.created",
        (
            _id("account_id", "account"),
            _enum("category"),
            EventField("amount", FieldType.AMOUNT),
            _code("tax_year"),
            _date("contribution_date"),
            _date("due_date"),
            _bool("lodge_expectation"),
            _amount("prefunded_ras_amount"),
        ),
        capture_body=True,
    ),
    EventType(
        "contribution.transaction_classified",
        (
            _id("transaction_id", "transaction"),
            _enum("category"),
            _code("tax_year"),
            _amount("prefunded_ras_amount"),
        ),
        capture_body=True,
    ),
    EventType(
        "transfer.created",
        (
            _id("account_id", "account"),
            _enum("transfer_type"),
            _amount("crystallised_value"),
            _date("expected_date"),
            EventField("pension_type", FieldType.COUNT),
            _bool("safeguarded_benefits"),
            _amount("total_value"),
            _amount("uncrystallised_value"),
        ),
        capture_body=True,
    ),
    # -- messaging (which client's channel was opened; whom a group spans) -------------------------
    EventType("conversation.opened", (_id("contact_id", "contact"),)),
    EventType("message_group.created", (_ids("contact_ids", "contact"),), capture_body=True),
    # A message names its COUNTERPARTY as a first-class subject, and its TEXT is on the row too now
    # via `capture_body`. That discloses nothing new, which is the test worth applying rather than
    # the old blanket rule: `messaging.messages`' read policy (0080) already lets a firm's staff
    # read every conversation in that firm, and `activity:read` is a firm-staff permission.
    #
    # Both ends of the channel emit this one event type, and only the STAFF end carries a
    # conversation on its route; a client's thread route derives their conversation from their own
    # scope and names nothing, so their message read as "Craig Currie sent a message" with no
    # recipient at all. The id is lifted off the RESPONSE to cover that end, and resolved to the
    # ADVISER rather than the conversation's client — on a client's own send the client IS the
    # sender, so naming them would print "Craig Currie sent a message to Craig Currie".
    #
    # `contact_id` names WHOSE channel it is, and is the sender on a client's own message. The
    # actor cannot answer that: one login may act for several contacts, and even at capacity
    # 'self' the login is an `auth.users` row while the client is a `crm.contacts` one - a real
    # book has the two names differing, and the adviser's notice was reading "New message from
    # <the login>". Capturing it also lets `context._fallback_contact_id` resolve `{client.name}`
    # with no special case, and gives the audit line the sender it was missing.
    #
    # This is a participant ID, which the catalogue's own note asks us to think twice about. The
    # rule it is protecting is the message BODY, which is still never captured; participants are
    # already named in the audit UI for a staff send (the conversation entity resolves to the
    # client, by design - see describe.ts), so this makes the two directions consistent rather
    # than exposing something new.
    EventType(
        "message.sent",
        (
            _resp_id("conversation_adviser_id", "conversation_adviser", "conversation_id"),
            _resp_id("contact_id", "contact", "contact_id"),
        ),
        capture_body=True,
    ),
    EventType(
        "message.attachment_sent",
        (
            _resp_id("conversation_adviser_id", "conversation_adviser", "conversation_id"),
            _resp_id("contact_id", "contact", "contact_id"),
            # WHICH FILE. The row named who it went to and not what was sent, which is the one
            # thing a reader opens this event for - "sent an attachment" is true of every one of
            # them. Lifted off the response because the uid does not exist when the request
            # arrives, and a LIST because one message may carry several.
            _resp_codes("attachment_filenames", "attachments[].filename"),
        ),
    ),
    # -- model assignment --------------------------------------------------------------------------
    EventType("account.model_updated", (_id("model_id", "model"),)),
    # -- illustrations -----------------------------------------------------------------------------
    EventType(
        "illustration.created",
        (
            _id("account_id", "account"),
            _amount("crystallised_fund_value"),
            _code("drawdown_type"),
            _amount("employer_contribution"),
            EventField("escalation_percent", FieldType.COUNT),
            _amount("existing_fund_value"),
            EventField("exit_age", FieldType.COUNT),
            EventField("guaranteed_period", FieldType.COUNT),
            _bool("include_annuity_basis"),
            _bool("inflation_adjustment"),
            _amount("initial_adviser_fee_amount"),
            _amount("member_contribution"),
            _id("model_id", "model"),
            EventField("ongoing_adviser_fee_percent", FieldType.COUNT),
            _code("product_type"),
            _code("quotation_type"),
            EventField("spouse_percentage", FieldType.COUNT),
            _amount("transfer_value"),
            _amount("withdrawal_amount"),
        ),
        capture_body=True,
    ),
    # A GIA or ISA illustration is for a client, not an existing account. The model is what
    # the projection was asked to use.
    EventType(
        "illustration.investment_created",
        (_id("model_id", "model"), _enum("wrapper")),
        capture_body=True,
    ),
    # -- documents: upload / generate / send / request / update / reassign -------------
    # An upload had NO capture at all, so its event named nothing: it could not appear in the
    # document's own Events tab (which matches on the document a row references) and the humanised
    # sentence had no document to mention — "Uploaded a document", about which one nobody could say.
    # The uid does not exist when the request arrives, so it is lifted off the RESPONSE, and it is a
    # LIST because one drop may carry several files (`UploadResult.documents`).
    # EVERY field here is RESPONSE-lifted, and that is forced, not stylistic: `POST /documents` is
    # `multipart/form-data` (its `document_type` / `subject_contact_id` are `Form(...)` params), and
    # `extract_subject` only ever `json.loads()` the body — so a SUBJECT-declared field on this
    # event can never populate. An earlier version of this entry declared `subject_contact_id` and
    # `document_type` as SUBJECT: both were permanently dead, which reads as "the upload event knows
    # nothing about its client" and quietly kept uploads out of that client's Events feed.
    # `file.uploaded` below got this right first — see its own note about the unreadable multipart
    # request; this now follows it.
    EventType(
        "document.uploaded",
        (
            _resp_ids("document_uids", "document", "documents[].uid"),
            # The client each created document is filed under, off the response's own rows.
            # Requires `DocumentOut.client_contact_id` to be DECLARED — it was aliased in `_SELECT`
            # but absent from the model, so pydantic dropped it and this would have lifted nothing.
            _resp_ids("client_contact_id", "contact", "documents[].client_contact_id"),
        ),
    ),
    EventType(
        "document.generated",
        (
            _id("template_uid", "template"),
            _id("client_contact_id", "contact"),
            _bool("allow_incomplete"),
            _code("idempotency_key"),
        ),
        capture_body=True,
    ),
    EventType(
        "document.sent",
        (
            _ids("contact_ids", "contact"),
            # The adviser's covering note — the ONE deliberate free-text field (captured truncated,
            # never dropped for length). See the doctrine note in actions.py.
            EventField("message", FieldType.TEXT, free_text=True),
        ),
    ),
    # Telling a client about a document they can already see. The capture is REQUIRED twice over:
    # the trail must name WHO was told (an entity chip per contact, exactly as the send does), and
    # the `document.notice` notification trigger resolves its recipients from
    # `metadata.subject.contact_ids` (`notifications.registry._resolve_document_contacts`) — without
    # it the notice resolves nobody and the endpoint silently notifies no one. Ids only: unlike the
    # send there is no covering note, so this event carries no free text at all.
    # `document.notified` was declared here for the notify route. This registry is a CAPTURE
    # allowlist — what to lift off a live REQUEST — and the route is gone, so nothing can be
    # captured against it again. The read side keeps the name so history still humanises.
    EventType(
        "document_request.created",
        (_id("contact_id", "contact"), EventField("due_date", FieldType.DATE), _enum("priority")),
        capture_body=True,
    ),
    EventType(
        "document.updated",
        (_id("intended_contact_id", "contact"), _code("document_type")),
        changed_keys=True,
        capture_body=True,
    ),
    EventType(
        "document_request.item_reassigned",
        (_id("target_document_uid", "document"),),
        capture_body=True,
    ),
    # `document_share.created` was declared here for the document share-links feature. This
    # registry is a CAPTURE allowlist — what to lift off a live REQUEST — so it went with the
    # route: no request can ever be captured against it again. The READ side keeps the share
    # vocabulary (see `actions.py`'s note) because historic rows must still humanise.
    # -- tasks (who must act, on what subject) -----------------------------------------------------
    EventType(
        "task.created",
        (
            _enum("assignee_kind"),
            _id("assignee_user_id", "user"),
            _code("assignee_role_slug"),
            _id("assignee_contact_id", "contact"),
            _enum("subject_type"),
            _code("subject_id"),
            _enum("priority"),
            _code("assignee_role_id"),
            _date("due_date"),
        ),
        capture_body=True,
    ),
    EventType(
        "task.updated",
        (
            _enum("assignee_kind"),
            _id("assignee_user_id", "user"),
            _code("assignee_role_slug"),
            _id("assignee_contact_id", "contact"),
            # `status` ON THE WIRE, not `task_status`: `UpdateTask` aliases the field and
            # `extract_subject` reads the raw body, so the `task_status` this said for months
            # matched nothing and every task update went unrecorded. The tripwire now compares
            # wire names.
            _enum("status"),
            _enum("priority"),
            _code("assignee_role_id"),
            _date("due_date"),
            _code("status"),
        ),
        changed_keys=True,
        capture_body=True,
    ),
    # -- applications / flow steps -----------------------------------------------------------------
    EventType(
        "journey.created",
        (_code("flow_code"), _code("provider"), _id("adviser_id", "user")),
        capture_body=True,
    ),
    EventType("journey.started", (_code("flow_code"),)),
    EventType(
        "journey.step_actioned",
        (
            _id("case_uid", "case"),
            _code("step_key"),
            _enum("outcome"),
            _id("participant_contact_id", "contact"),
            _bool("adviser_confirmed"),
        ),
        capture_body=True,
    ),
    EventType(
        "journey.step_retried",
        (_id("case_uid", "case"), _code("step_key"), _id("participant_contact_id", "contact")),
    ),
    # -- AML (which client was screened; each decision, a closed enum) -----------------------------
    EventType("aml.screen_ran", (_id("contact_uid", "contact"),)),
    EventType("aml.match_decided", (_enum("decision"),)),
    EventType("aml.check_decided", (_enum("decision"),)),
    EventType("aml.monitoring_updated", (_bool("enabled"),)),
    # Which photos the client was asked for (one or both of a closed pair) and the client-safe
    # reason code. The operator's comment is internal and never captured.
    EventType("aml.photos_requested", (_enum("photos"), _enum("reason"))),
    # Which photos an operator uploaded for a client (the document, the selfie, or both), off the
    # response: the request is multipart. The photos and the operator's comment never land.
    EventType("aml.identity_photos_uploaded", (_resp_codes("photos", "photos"),)),
    # How many identity images a page showed (the check is the route's `uid`); never which bytes.
    EventType("aml.identity_images_viewed", (EventField("image_count", FieldType.COUNT),)),
    # Which document the client photographed, for which step. The photos themselves never land.
    EventType(
        "journey.identity_photos_submitted",
        (_id("case_uid", "case"), _code("step_key"), _enum("document_type")),
    ),
    # -- the firm action registry (which action, on what, and who may run or approve it) --------
    # BOTH role spellings, for `member.portal_role_updated`'s reason: the editor sends the lists.
    EventType(
        "action_policy.updated",
        (
            _code("action_key"),
            _code("provider"),
            _bool("enabled"),
            _bool("approval_enabled"),
            _bool("require_justification"),
            # No provider API yet: accepting the held run completes it by hand.
            _bool("manual"),
            # Which channels an approval notice uses.
            _bool("notify_in_app"),
            _bool("notify_email"),
            _code("approval_role_slug"),
            _codes("approval_role_slugs"),
            _codes("role_slugs"),
        ),
    ),
    # `entity_uid` as `_code`: the subject is polymorphic (`entity_type` says which table), the
    # same reason `task.created`'s `subject_id` is a code.
    EventType(
        "action_run.started", (_code("action_key"), _code("entity_type"), _code("entity_uid"))
    ),
    # -- partner access, management reports, client money ----------------------------------------
    EventType("api_key.created", (_codes("scopes"), _date("expires_at"))),
    EventType("mi_report.switched", (_bool("enabled"),)),
    EventType(
        "money_movement.created",
        (_enum("kind"), _id("account_uid", "account"), _amount("amount")),
    ),
    # -- exports (a listing leaving the platform as a file; no entity to name, so what format and
    # how much) --------------------------------------------------------------------------------
    EventType(
        "export.listing_exported",
        (_enum("format"), _code("listing"), EventField("rows", FieldType.COUNT)),
    ),
    # -- response-only creates (the new row's id is named only in the 2xx body) --------------------
    EventType(
        "client.created",
        # "Created a client" could not name WHO from the request (free-text name/email, refused);
        # the new contact id is response-only. Lifting it is the highest-value gap in the stream.
        (
            _resp_id("contact_id", "contact", "id"),
            _code("service_type"),
            _code("sub_type"),
            _code("type"),
        ),
        capture_body=True,
    ),
    EventType(
        "file.uploaded",
        # A multipart upload never reaches extract_subject; the response echoes the filed contact.
        (_resp_id("contact_id", "contact", "contact_id"),),
    ),
    # -- reviews (four-eye). Lifecycle events (requested / approved / rejected / cancelled /
    # conflicted) are written in-transaction by ``reviews.events``, not captured off a
    # request body — they must not appear here or SUBJECT_FIELDS would claim routes that
    # do not exist (requested, conflicted) or re-emit an empty-subject row on decide.
    # `reviewer_role_id`/`reviewer_role_slug` as `_code`, not `_id`: every other role field in this
    # registry (`role_slug`, `role_slugs`, `role.created`'s `slug`) is a code, and there is no
    # per-role entity resolver to give an id form anywhere to resolve to.
    EventType(
        "review.policy_updated",
        (_enum("mode"), _code("reviewer_role_id"), _code("reviewer_role_slug")),
    ),
    # -- client books (a firm's own segmentation catalogue, and pinning one to a client) ---------
    # `classification_uid` as `_code`, not `_id`: it addresses `crm.classifications`, which has no
    # entity resolver (`admin._ENTITY_RESOLVERS`) to turn it into a name — the same reason
    # `task.created`'s polymorphic `subject_id` is a code rather than a resolved id.
    EventType("client.book_pinned", (_code("classification_uid"),)),
    # Client groups are the firm's own tags (a business stream). The catalogue
    # edit names the slug; setting a client's groups names the set, because
    # that is what a file review asks — which streams this person was put in.
    EventType(
        "client_group.created",
        (_code("slug"), EventField("sort_order", FieldType.COUNT)),
    ),
    EventType(
        "client_group.updated",
        (_code("slug"), EventField("sort_order", FieldType.COUNT)),
    ),
    EventType("client.groups_set", (_codes("group_uids"),)),
    EventType(
        "client_book.created",
        (_code("slug"), EventField("sort_order", FieldType.COUNT), _bool("is_default")),
    ),
    EventType(
        "client_book.updated",
        (_code("slug"), EventField("sort_order", FieldType.COUNT), _bool("is_default")),
    ),
    # -- terms (a firm's or Graphene's published terms, and a client's acceptance of one) --------
    # `application_uid` as `_code`, not `_id`: it addresses `crm.applications`, a different table
    # from the one the `journey`/`case` resolvers read (`flows.executions`/`flows.cases`) —
    # aliasing it to either would resolve against the wrong table rather than fail loudly.
    EventType("terms.created", (_code("code"), _enum("audience"), _enum("body_kind"))),
    EventType("terms.updated", (_enum("audience"), _enum("body_kind"))),
    EventType("terms.accepted", (_code("application_uid"), _bool("attested"))),
    # -- the illustration reference tables. Each is a DATED row a firm publishes and then quotes
    # against, so `effective_from` is the fact that tells two of them apart — a growth set for
    # April and one for October are the same table, the same firm and different numbers.
    EventType("illustration.custody_created", (_date("effective_from"), _enum("provider"))),
    EventType(
        "illustration.disclosure_updated",
        (
            EventField("frn", FieldType.COUNT),
            _bool("gia_isa_illustrations"),
            _bool("illustrations_auto_share"),
        ),
    ),
    EventType(
        "illustration.fee_schedule_created",
        (_date("effective_from"), _enum("tier_basis"), _enum("wrapper")),
    ),
    EventType("illustration.growth_set_created", (_bool("approved"), _date("effective_from"))),
    EventType(
        "illustration.instrument_costs_imported", (_date("data_date"), _enum("instrument_type"))
    ),
    EventType("illustration.isa_allowance_created", (_date("effective_from"),)),
    # The login a journey was handed to — `_id`, because `user_id` already resolves to a person
    # (`_ENTITY_FIELD_KINDS`), so the row names them rather than printing a uuid.
    EventType("journey.assigned", (_id("user_id", "user"),)),
    # ======================================================================================
    # Everything below was added by the September 2026 event-content review: of 224 live event
    # types, 54 could not name the record they touched and 32 more said nothing about the change.
    # Which mechanism an event gets is a judgement about the act — a CLOSED VALUE the act turns on
    # is captured; a body whose shape is "the fields I am changing" gets `changed_keys`, names
    # only; and an act with one meaning ("Archived a document") is left alone.
    # ======================================================================================
    EventType(
        "contribution.categorised",
        (_enum("category"), _code("tax_year"), _enum("frequency"), _enum("paid_by")),
        changed_keys=True,
        capture_body=True,
    ),
    EventType("contribution.status_updated", (_enum("status"),)),
    # The reason is the ACT here, not decoration: a cancellation is refused past `ras_applied`
    # precisely because money has moved, so why somebody cancelled one is the question a file
    # review opens this row to ask. Free text by the `_text` rule.
    EventType("contribution.cancelled", (_text("reason"), _bool("stop_at_morgan_lloyd"))),
    EventType("transfer.status_updated", (_enum("status"),)),
    # -- beneficiaries: a nomination is a share of a fund, so the share is the fact -------------
    # The NAME is deliberately absent - it is a third party's, the record itself holds it, and an
    # audit row is read by more people than the nomination is.
    EventType(
        "account.beneficiary_added",
        (_code("relationship"), _amount("proportion"), _date("nominated_on")),
        capture_body=True,
    ),
    # -- clients: which access was handed out, and which fields moved ---------------------------
    # Both spellings: `role_slugs` is the multi-role form and `role_slug` the single one it grew
    # out of; a row written by either must read the same way.
    EventType("client.portal_invited", (_code("role_slug"), _codes("role_slugs"))),
    EventType(
        "client.updated",
        (_bool("enabled"), _code("service_type"), _code("sub_type")),
        changed_keys=True,
        capture_body=True,
    ),
    EventType(
        "client.vulnerability_updated",
        (_bool("is_vulnerable"), _bool("is_temporary"), _codes("reasons")),
        capture_body=True,
    ),
    # Bank details are the destination money leaves by, so WHICH fields were rewritten is the
    # audit fact - and the values are exactly what an audit row must not carry.
    EventType("client.bank_account_updated", (), changed_keys=True, capture_body=True),
    EventType("account.bank_accounts_updated", (), changed_keys=True, capture_body=True),
    EventType("account.regular_movements_updated", (), changed_keys=True, capture_body=True),
    EventType("account.fee_packages_updated", (), changed_keys=True, capture_body=True),
    # -- members / firms -------------------------------------------------------------------------
    EventType(
        "member.profile_updated",
        (_bool("is_adviser"), _codes("type")),
        changed_keys=True,
        capture_body=True,
    ),
    EventType("member.provider_ref_updated", (_code("external_id"),)),
    EventType(
        "firm.created",
        (_code("alias"), _bool("from_model"), _code("workos_project_id")),
        capture_body=True,
    ),
    EventType("firm.updated", (_bool("enabled"),), changed_keys=True, capture_body=True),
    EventType(
        "firm.ai_settings_updated", (_bool("enabled"),), changed_keys=True, capture_body=True
    ),
    EventType(
        "firm.currency_settings_updated", (_code("base_currency"), _codes("allowed_currencies"))
    ),
    EventType("firm.provider_updated", (_bool("enabled"),), changed_keys=True, capture_body=True),
    # Wiping a firm's operational data. The `kind` says how far it went; `confirm_alias` is the
    # caller echoing the firm's own name back as a confirmation, which the row already states.
    EventType("firm.data_reset_requested", (_enum("kind"),), capture_body=True),
    EventType("branding.updated", (), changed_keys=True, capture_body=True),
    # -- authoring surfaces: the code is how a firm refers to the thing it just made ------------
    EventType("flow.created", (_code("code"), _enum("entity")), capture_body=True),
    EventType("flow.updated", (_enum("status"),), changed_keys=True, capture_body=True),
    # The code the flow lands under (the file's, or the importer's override). The bundle itself —
    # the whole definition plus every form it names — is not captured: the draft version it
    # becomes is the record, and `flow.version_saved` captures a definition when it is edited.
    EventType("flow.imported", (_code("code"),)),
    EventType(
        "form.created",
        (
            _code("code"),
            _enum("entity_type"),
            _code("document_type"),
            _code("questionnaire_dataset_code"),
        ),
        capture_body=True,
    ),
    EventType(
        "form.updated",
        (
            _code("code"),
            _code("document_type"),
            _enum("entity_type"),
            _code("questionnaire_dataset_code"),
        ),
        changed_keys=True,
        capture_body=True,
    ),
    EventType(
        "template.created",
        (_code("code"), _enum("renderer"), _code("document_type"), _code("input_form_code")),
        capture_body=True,
    ),
    EventType(
        "template.updated",
        (_code("code"), _code("document_type"), _code("input_form_code"), _enum("renderer")),
        changed_keys=True,
        capture_body=True,
    ),
    EventType(
        "dataset.created", (_code("code"), _enum("scope"), _enum("format")), capture_body=True
    ),
    EventType("dataset.updated", (_bool("enabled"),), changed_keys=True, capture_body=True),
    EventType("email_template.created", (_code("code"), _enum("category")), capture_body=True),
    EventType(
        "email_template.updated",
        (_enum("category"), _code("code")),
        changed_keys=True,
        capture_body=True,
    ),
    EventType("content.created", (_enum("kind"),), capture_body=True),
    EventType(
        "content.updated",
        (_enum("audience"), _code("category_uid"), _bool("clear_expires"), _date("expires_at")),
        changed_keys=True,
        capture_body=True,
    ),
    EventType(
        "document_type.created",
        (_enum("kind"), _code("slug"), EventField("sort_order", FieldType.COUNT)),
        capture_body=True,
    ),
    EventType(
        "automation.created",
        (
            _code("trigger_key"),
            _id("flow_uid", "flow"),
            _bool("enabled"),
            _bool("allow_automation_source"),
            EventField("priority", FieldType.COUNT),
            EventField("rearm_minutes", FieldType.COUNT),
        ),
        capture_body=True,
    ),
    EventType(
        "automation.updated",
        (
            _bool("allow_automation_source"),
            _bool("enabled"),
            _id("flow_uid", "flow"),
            EventField("priority", FieldType.COUNT),
            EventField("rearm_minutes", FieldType.COUNT),
            _code("trigger_key"),
        ),
        changed_keys=True,
        capture_body=True,
    ),
    EventType(
        "automation.settings_updated",
        (
            EventField("case_trigger_budget", FieldType.COUNT),
            EventField("default_rearm_minutes", FieldType.COUNT),
            EventField("executions_per_minute", FieldType.COUNT),
            EventField("flow_concurrency", FieldType.COUNT),
            EventField("max_chain_depth", FieldType.COUNT),
            _bool("paused"),
        ),
        changed_keys=True,
        capture_body=True,
    ),
    EventType(
        "automation.tested",
        (_code("trigger_key"), _bool("override_conditions"), _code("subject_id")),
        capture_body=True,
    ),
    EventType(
        "notification_rule.created",
        (
            _code("trigger_key"),
            _enum("audience"),
            _code("email_code"),
            _bool("default_email"),
            _bool("default_in_app"),
            _bool("enabled"),
            _bool("mandatory"),
            _code("role_slug"),
            _enum("priority"),
        ),
        capture_body=True,
    ),
    EventType(
        "notification_rule.updated",
        (
            _code("email_code"),
            _bool("enabled"),
            _bool("default_email"),
            _bool("default_in_app"),
            _bool("mandatory"),
            _enum("priority"),
        ),
        changed_keys=True,
        capture_body=True,
    ),
    # -- catalogue: inline-dict bodies, so there is no field list to allowlist - names only -----
    EventType("product.updated", (), changed_keys=True),
    EventType("model.updated", (), changed_keys=True),
    EventType("fee_package.updated", (), changed_keys=True),
    EventType("provider.updated", (), changed_keys=True),
    # -- journeys: who the step was for, and which step -----------------------------------------
    EventType(
        "journey.terms_accepted",
        (
            _id("case_uid", "case"),
            _code("step_key"),
            _id("participant_contact_id", "contact"),
            _bool("attested"),
        ),
        capture_body=True,
    ),
    EventType(
        "journey.step_form_submitted",
        (_id("participant_contact_id", "contact"),),
        capture_body=True,
    ),
    # Progress saved without fulfilling the step: the same payload as a submission (whose
    # answers it is), so the same record of whose answers they are and what they were.
    EventType(
        "journey.step_form_saved",
        (_id("participant_contact_id", "contact"),),
        capture_body=True,
    ),
    EventType(
        "journey.identity_evidence_uploaded",
        (_id("case_uid", "case"), _code("step_key"), _id("participant_contact_id", "contact")),
    ),
    EventType("journey.cancelled", (_text("reason"),)),
    # -- documents / messaging ------------------------------------------------------------------
    # A BATCH generate is one event for up to 250 clients (one row per REQUEST), which is why it
    # has its own name. Without the template and the clients the row cannot say to whom.
    EventType(
        "document.batch_generated",
        (
            _id("template_uid", "template"),
            _ids("contact_ids", "contact"),
            _bool("allow_incomplete"),
            _code("idempotency_key"),
        ),
        capture_body=True,
    ),
    EventType("document_request.updated", (_date("due_date"),), changed_keys=True),
    EventType(
        "message_group.document_requested",
        (_date("due_date"), _enum("priority")),
        capture_body=True,
    ),
    # ======================================================================================
    # Added by the REPLAYABILITY audit: every live event checked against what its route receives.
    # Of 224, 114 were already whole, 60 had nothing left but prose, an object or a credential
    # (deliberately absent), and these are the rest — closed vocabularies, amounts, dates and ids
    # that were never captured. PERSONAL DATA IS RECORDED, and that line moved in September 2026;
    # the reasoning and the open question are above `actions.CHANGED_COLUMNS`.
    # ======================================================================================
    EventType("automation.platform_pause_updated", (_bool("paused"),), capture_body=True),
    EventType("contribution.application_updated", (_amount("amount"),)),
    EventType("contribution.transaction_attached", (_id("transaction_id", "transaction"),)),
    EventType(
        "document_type.updated",
        (_bool("archived"), EventField("sort_order", FieldType.COUNT)),
        capture_body=True,
    ),
    EventType("email_template.test_sent", (_enum("audience"),), capture_body=True),
    # The addresses are personal, so the subject records only the closed decision: reset to the
    # deployment default, or a saved list. An empty saved list and a populated one share
    # use_default=false; the row in auth.outbound_email_allowlist is what was saved.
    EventType("email.allowlist_updated", (_bool("use_default"),)),
    # The domain is the act. The From address is a mailbox on it; `changed_keys` records that
    # it moved without copying the address into the subject.
    EventType("email.sender_domain_registered", (_code("domain"),)),
    EventType("email.sender_address_updated", (), changed_keys=True),
    EventType(
        "environment.applications_updated",
        (
            _code("client_portal_client_id"),
            _code("operations_client_id"),
            _code("platform_client_id"),
        ),
    ),
    EventType("environment.registered", (_code("workos_client_id"),), capture_body=True),
    EventType("journey.identity_handed_to_staff", (_id("case_uid", "case"), _code("step_key"))),
    EventType(
        "journey.identity_taken_by_adviser",
        (_id("case_uid", "case"), _code("step_key"), _id("participant_contact_id", "contact")),
    ),
    EventType("journey.identity_phone_link_created", (_id("case_uid", "case"), _code("step_key"))),
    EventType(
        "journey.identity_phone_link_withdrawn", (_id("case_uid", "case"), _code("step_key"))
    ),
    EventType("platform.action_ran", (_code("entity_id"),), capture_body=True),
    EventType("template.version_saved", (_enum("renderer"),), capture_body=True),
    EventType("transfer.transaction_attached", (_id("transaction_id", "transaction"),)),
    # ======================================================================================
    # BODY CAPTURE ONLY. These acts have no first-class subject of their own — their content is
    # free text, a form's answers or a nested object — so the request itself is what makes the row
    # readable (`EventType.capture_body`; `actions.BODY_CAPTURE_EVENTS` for what that costs).
    # ======================================================================================
    EventType("automation.reordered", (), capture_body=True),
    EventType("client.note_added", (), capture_body=True),
    EventType("content_category.created", (), capture_body=True),
    EventType("dataset.version_added", (), capture_body=True),
    EventType("document_request.form_submitted", (), capture_body=True),
    # Progress on a requested form, saved without submitting: the same answers the submission
    # will carry, so the same record of what they were.
    EventType("document_request.form_saved", (), capture_body=True),
    EventType("email_template.version_saved", (), capture_body=True),
    EventType("flow.version_saved", (), capture_body=True),
    EventType("form.fields_updated", (), capture_body=True),
    EventType("form.response_submitted", (), capture_body=True),
    EventType("message_group.message_sent", (), capture_body=True),
    EventType("notification.client_preferences_updated", (), capture_body=True),
    EventType("notification.preferences_updated", (), capture_body=True),
    EventType("notification.settings_updated", (), capture_body=True),
    EventType("task.completed", (), capture_body=True),
    EventType("template.preview_pdf_rendered", (), capture_body=True),
    EventType("template.preview_rendered", (), capture_body=True),
    # -- operations acts on a firm's own data ---------------------------------------------------
    # The SQL IS the act: a read-only warehouse console, run by an operator against a firm's book.
    # "Ran a warehouse query" without it records that somebody looked and not what they looked at,
    # which is the one thing the row exists to say.
    EventType("platform.queried", (_text("sql"), EventField("limit", FieldType.COUNT))),
    # -- the admin Integrations screen (SEI platform approvals) ---------------------------------
    EventType(
        "integration.updated",
        (_id("firm_id", "tenant"), _enum("status"), _bool("enabled")),
    ),
    EventType(
        "integration.client_account_linked_to_io",
        (_id("contact_uid", "contact"), _id("account_uid", "account")),
    ),
    # The account is the route's path parameter, which the audit row records on its own. What the
    # row adds is whether Intelliflo was left alone (a link removed without blanking the plan).
    EventType("integration.account_unlinked_from_io", (_bool("skip_intelliflo"),)),
    EventType("integration.client_linked_to_io", (_id("contact_uid", "contact"),)),
    # The client is the route's path parameter, which the audit row records on its own, and the
    # request has no body. What only the response knows is WHICH Intelliflo client was released.
    EventType(
        "integration.client_unlinked_from_io",
        (_resp_codes("client_id_io", "client_id_io"),),
    ),
    EventType("integration.account_linked_to_io_plan", (_id("account_uid", "account"),)),
)


# ---- projections the existing consumers read -------------------------------------------------


def subject_fields() -> dict[str, tuple[str, ...]]:
    """`actions.SUBJECT_FIELDS`: event_type -> the request field names to capture, in order. Only
    events with at least one SUBJECT field appear (a response-only create is absent, as it was)."""
    out: dict[str, tuple[str, ...]] = {}
    for et in REGISTRY:
        names = tuple(f.name for f in et.fields if f.source is FieldSource.SUBJECT)
        if names:
            out[et.event_type] = names
    return out


def response_subject_fields() -> dict[str, dict[str, str]]:
    """`actions.RESPONSE_SUBJECT_FIELDS`: event_type -> {response body key -> subject key}. Only
    events with at least one RESPONSE field appear."""
    out: dict[str, dict[str, str]] = {}
    for et in REGISTRY:
        mapping = {
            f.response_key: f.name
            for f in et.fields
            if f.source is FieldSource.RESPONSE and f.response_key is not None
        }
        if mapping:
            out[et.event_type] = mapping
    return out


def free_text_subject_fields() -> frozenset[str]:
    """`actions._FREE_TEXT_SUBJECT_FIELDS`: the deliberately free-text field names (captured
    truncated, never dropped for length). The union across the registry."""
    return frozenset(f.name for et in REGISTRY for f in et.fields if f.free_text)


def changed_key_events() -> frozenset[str]:
    """`actions.CHANGED_KEY_EVENTS`: the event types whose body KEY NAMES are recorded (never
    the values) - see `EventType.changed_keys`."""
    return frozenset(et.event_type for et in REGISTRY if et.changed_keys)


def body_capture_events() -> frozenset[str]:
    """`actions.BODY_CAPTURE_EVENTS`: the event types whose whole (redacted) request body is
    recorded — see `EventType.capture_body`."""
    return frozenset(et.event_type for et in REGISTRY if et.capture_body)


def entity_field_kinds() -> dict[str, str]:
    """Every captured field that names an entity -> its kind. A read-side pin: this must agree with
    `admin._ENTITY_FIELD_KINDS` for the fields both know, so the registry can supersede that global
    dict for subject/response fields in a later increment."""
    out: dict[str, str] = {}
    for et in REGISTRY:
        for f in et.fields:
            if f.entity_kind is not None:
                out[f.name] = f.entity_kind
    return out
