"""Shared read-side query building for the action-event stream (``audit.actions``, 0041).

Two endpoints read the stream — ``GET /admin/audit/actions`` (Graphene operations, cross-tenant)
and ``GET /firm/audit/actions`` (a firm's own slice, waterfalled to the platform portal) — and
they must stay one query apart, not two implementations apart: the filters, the LIKE-escaping,
the keyset cursor and the over-fetch ``has_more`` trick all have pinned semantics
(``tests/test_admin_audit_actions.py``), and a second hand-rolled copy would drift. So the query
lives here once, and each endpoint supplies only what actually differs:

* ``scope_sql`` — the tenant predicate. The admin passes an optional ``?tenant=`` filter over
  both sides; the firm endpoint hard-codes its OWN tenant id from the session (never a client
  parameter). **Developer-written SQL only** — it is interpolated into the statement, so it must
  never contain request input (values go through ``scope_params``).
* ``exclude_event_prefixes`` — server-side deny list on ``event_type`` prefixes. The firm view
  uses it to withhold the AML family (see ``routers/firm.py`` for the rationale); the admin view
  passes none. Applied AFTER the caller's own ``event_type`` filter, as an AND NOT — a filter can
  never un-hide a denied family.

Both callers read as ``service_role`` (``audit.actions`` is service_role-only with no
``authenticated`` grant; the access control is the permission gate at each endpoint, not RLS).

``action_facets`` is the same two inputs again — one scope, one deny list — answering what the
toolbar's dropdowns may OFFER rather than which rows to return. It lives here for that reason: a
facet list is a disclosure too, so it must be scoped and denied with exactly the force the page is,
and a second hand-rolled predicate could disagree with the one above.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, TypedDict

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import storage
from app.listing import DEFAULT_LIMIT


class ActionsPage(TypedDict):
    """One page of the stream: newest-first rows plus the over-fetch `has_more` answer."""

    items: list[dict[str, Any]]
    has_more: bool


#: How far back the facet lists look: the most recent ``FACET_WINDOW`` rows of the caller's scope.
#:
#: ``audit.actions`` is append-only and unbounded, so an un-windowed ``DISTINCT`` over the whole
#: table gets slower for ever — a filter dropdown must not be the thing that eventually times the
#: page out. The bound is deliberate and STATED (see ``action_facets``): the facets describe the
#: RECENT stream, not all history. A value that has fallen out of the window is not lost from the
#: bar — the loaded rows still merge their own options on top of the seed (``mergeOptions`` in
#: ``pkgs/ui/.../event-stream-filters.ts``), so an old type reached by paginating still appears.
FACET_WINDOW = 5000


class ActionFacets(TypedDict):
    """The filter VOCABULARY for one caller's slice of the stream — the option lists the Events
    toolbar's dropdowns offer, computed server-side and independently of every caller filter.

    ``event_types`` are raw ``action.*`` types (the ``event_type`` filter matches them as a prefix,
    so an exact type matches itself); ``actors`` are display names — name else email else
    ``actor_sub``, the precedence the UI's ``actorName`` uses — so a chosen option always matches
    the rows it describes."""

    event_types: list[str]
    actors: list[str]


def _icon_sql(alias: str) -> str:
    """The tenant's branding icon URL, extracted from ``auth.tenants.theme`` for one side of the
    event (``t`` = acting, ``tt`` = proxied target) so the trail can show WHOSE firm acted at a
    glance.

    Two stored shapes (``app/branding.py::parse_theme``): the current per-portal Theme keeps the
    icon at ``{client: {icon_url: …}}``; a legacy pre-per-portal theme is a flat Branding with a
    top-level ``icon_url`` (and no ``client`` key, so the COALESCE arms never both match).

    An icon may legitimately be an inline ``data:`` URI (the no-bucket fallback in
    ``app/storage.py``), and the validator allows those up to ~1.4M characters — repeated on
    every row of a 200-row page that is a multi-hundred-MB response. Anything that large is
    withheld (NULL ⇒ the UI's name-only chip), never truncated: a cut-off data URI is a broken
    image, not a smaller one."""
    icon = f"COALESCE({alias}.theme #>> '{{client,icon_url}}', {alias}.theme ->> 'icon_url')"
    return f"CASE WHEN length({icon}) <= 65536 THEN {icon} END"


def like_escape(value: str) -> str:
    """Escape LIKE wildcards in caller input so a typed ``%`` matches a literal ``%`` rather than
    silently widening a prefix/substring filter over the whole stream."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


#: entity KIND -> the id-shaped field names that mean it, plus the router tags whose ``{uid}``
#: means it. This is the READ-side resolution turned inside out: ``routers/admin.py`` maps a field
#: name to a kind so it can label a row; the tag FILTER needs the inverse: "which keys would make
#: this row carry a document".
#:
#: Kept here rather than imported from the router because this module is the read side's SQL and has
#: no app imports at all (the router imports it, not the other way round). The two must agree — if a
#: field is added there and not here, the tag filter silently stops matching rows whose chips show
#: that kind, so a test asserts every kind in ``admin._ENTITY_FIELD_KINDS`` appears below.
TAG_FIELDS: dict[str, tuple[str, ...]] = {
    "contact": (
        # `contact_uid` first, because it is what every client route has recorded since clients
        # became uid-addressed; `contact_id` stays for the rows written before that.
        "contact_uid",
        "contact_id",
        "client_contact_id",
        "participant_contact_id",
        "intended_contact_id",
        "assignee_contact_id",
        "subject_contact_id",
        "contact_ids",
    ),
    # Both spellings, for `_ENTITY_FIELD_KINDS`' reason: these routes became uid-addressed and the
    # stream records the param the route actually received, so a tag built from one spelling matches
    # only half the history.
    "account": ("account_uid", "account_id"),
    "transaction": ("transaction_id",),
    "conversation": ("conversation_uid", "conversation_id"),
    # The same conversation id resolved to the ADVISER instead — the counterparty on a
    # client's own send. Its own kind, so filtering by it finds the client-sent half of a
    # channel that the "conversation" tag cannot see.
    "conversation_adviser": ("conversation_adviser_id",),
    "user": ("user_id", "adviser_id", "assignee_user_id"),
    # Both spellings: the admin section is uid-addressed, and the stream holds rows recorded
    # before it was. A filter that knew only one would silently miss half the trail.
    # `firm_id` is the same "tenant" chip under the admin Integrations screen's own field name
    # (integrations.platforms.firm_id, 0273_integrations_tenant).
    "tenant": ("tenant_uid", "tenant_id", "firm_id"),
    "model": ("model_uid", "model_id"),
    "template": ("template_uid",),
    "document": ("target_document_uid", "document_uids"),
    # -- added by the September 2026 event-content review, in step with `_ENTITY_FIELD_KINDS` ---
    # Each was a path param the stream recorded and nothing could read. A kind turns the chip into
    # a name; listing it here lets a reader FILTER on that kind. Pinned by
    # `test_the_tag_map_covers_every_kind_the_read_side_resolves`.
    "document_request": ("req_uid",),
    "document_type": ("type_uid",),
    # `exec_uid` is the application, `app_uid` the same thing on the route that starts it.
    "journey": ("exec_uid", "app_uid"),
    "case": ("case_uid",),
    "message_group": ("group_uid",),
    "storage_credential": ("credential_uid",),
    "environment": ("environment_uid",),
    "beneficiary": ("beneficiary_uid",),
    "product": ("product_uid",),
    "fee_package": ("fee_uid",),
    "catalogue_provider": ("provider_uid",),
    # A captured SUBJECT field, not a path param: the flow an automation starts.
    "flow": ("flow_uid",),
    # Operations-only: the firm feed excludes the whole `aml.` family before any of this runs.
    "aml_search": ("search_uid",),
    "aml_profile": ("profile_uid",),
    "aml_hit": ("hit_uid",),
}

#: The generic ``{uid}`` path param resolves by the event type's RESOURCE, so a row can carry a kind
#: with none of the field names above (``document.archived`` names its document as ``uid``). Mirrors
#: ``admin._UID_KIND_BY_RESOURCE``, inverted — including the LEGACY router tags, because the stream
#: is
#: append-only and a filter that ignores history answers "nothing" for most of it.
TAG_UID_RESOURCES: dict[str, tuple[str, ...]] = {
    "document": ("document", "document_share", "documents", "shares"),
    "template": ("template", "templates"),
    "form": ("form", "forms"),
    "flow": ("flow", "flows"),
    "task": ("task", "tasks"),
    "instruction": ("instruction", "firm"),
    # -- added by the September 2026 event-content review, in step with `_UID_KIND_BY_RESOURCE` --
    "automation": ("automation",),
    "content": ("content",),
    "contribution": ("contribution",),
    "transfer": ("transfer",),
    "dataset": ("dataset",),
    "email_template": ("email_template",),
    "file": ("file",),
    "notification": ("notification",),
    # A `platform.` event's `{uid}` is the FIRM being acted on, not a platform record.
    "tenant": ("platform",),
    "notification_rule": ("notification_rule",),
}

#: The resource of a row's event type, in SQL, for both spellings — the ``actions.event_resource``
#: derivation. A legacy row is ``action.{tag}-{name}`` (resource = the tag, after the 8-character
#: prefix); a current one is ``{resource}.{verb}``.
_RESOURCE_SQL = (
    "CASE WHEN a.event_type LIKE 'action.%' "
    "     THEN split_part(substring(a.event_type from 8), '-', 1) "
    "     ELSE split_part(a.event_type, '.', 1) END"
)


def _tags_clause(tags: Sequence[str], params: dict[str, Any]) -> str:
    """AND across the selected entity kinds: a row must carry EVERY one of them.

    AND, not OR, and deliberately: picking ``document`` + ``contact`` asks for the events touching
    both — "what did we do to this client's documents". Under OR a second tag WIDENED the set, so
    adding a filter made the list grow, which reads as broken. It matches the client-side
    ``matchesTags`` the no-fetch fallback uses; the two must not disagree about the same words.

    A kind matches when any of its field names is a KEY of ``path_params`` or of the captured
    ``metadata.subject`` / ``metadata.previous`` objects (``?|`` = "any of these keys exists"), or —
    for the kinds reached through the generic ``{uid}`` — when the row has a ``uid`` param and its
    event type's RESOURCE names that kind. Presence of the KEY is the test, never its value: the
    read side resolves the same keys into the chips a reader sees, so a row is taggable exactly when
    it is labellable.

    An unknown kind (a client sending a word this map has no entry for) yields ``FALSE`` for that
    kind, so the page comes back empty rather than silently unfiltered — a filter that cannot be
    honoured must not answer as though it were.
    """
    if not tags:
        return ""
    sql = ""
    for i, kind in enumerate(tags):
        fields = TAG_FIELDS.get(kind, ())
        uid_tags = TAG_UID_RESOURCES.get(kind, ())
        if not fields and not uid_tags:
            sql += " AND FALSE "
            continue
        parts: list[str] = []
        if fields:
            params[f"tagf_{i}"] = list(fields)
            key = f"CAST(:tagf_{i} AS text[])"
            parts.append(
                f"a.path_params ?| {key} "
                f"OR a.metadata->'subject' ?| {key} "
                f"OR a.metadata->'previous' ?| {key}"
            )
        if uid_tags:
            params[f"tagu_{i}"] = list(uid_tags)
            # The event's own resource — the same derivation the read side does to disambiguate
            # `{uid}`, so the filter and the chips cannot disagree about what a row names.
            parts.append(
                f"(a.path_params ? 'uid' AND {_RESOURCE_SQL} = ANY(CAST(:tagu_{i} AS text[])))"
            )
        sql += " AND (" + " OR ".join(parts) + ") "
    return sql


def _contact_clause() -> str:
    """The "this event NAMES this contact" predicate — shared by the page and the facet query.

    Extracted for the same reason ``_deny_clause`` was: the two must narrow by identical rules, or
    the dropdowns describe a different population from the rows. On the client record the contact is
    a SCOPE the reader cannot clear, so a vocabulary computed without it offers types and actors
    with no events on that client — pick one and the list empties, which reads as a broken filter
    rather than as an honest "nothing here".

    Binds ``:contact`` (int, NULL ⇒ no narrowing), ``:contact_text`` (the same id as text, because
    the captured subject stores whatever the request body held) and ``:contact_uid`` (the public
    handle every contact route records since the rename). Mirrors
    ``admin._ENTITY_FIELD_KINDS`` for the contact kind (the same list as ``TAG_FIELDS['contact']``),
    PLUS the document arm below, which reaches events naming a document rather than a person.
    """
    # noqa: S608 — the only interpolation is `_DOCUMENT_FAMILY_SQL`, a module constant built from a
    # literal tuple of prefixes; the contact id binds as `:contact`.
    return (
        "  AND (CAST(:contact AS int) IS NULL "  # noqa: S608
        # THE DOCUMENT ARM. Most document activity names no contact at all: an upload, a view,
        # an archive, a type change and a render carry the document's `{uid}` and nothing else
        # (only generate / send / notify / update / request-created capture a contact id). So a
        # client's Events tab, scoped by `contact`, could show that a document was SENT to them and
        # never that one was uploaded for them, viewed or archived — which reads as "this client has
        # no document history" on a record whose Documents tab is full.
        #
        # A document filed under a client is what `documents.document_contacts` records, and it is
        # the same link the client-portal RLS branch reads to decide visibility — so scoping the
        # trail through it shows a client's own document history and can never show one they could
        # not already open.
        #
        # Gated to the document FAMILY: a template or form `{uid}` that happened to equal a document
        # uid would otherwise pull an unrelated row into this client's history.
        # The uid comes from the ROUTE for most document events — and from the captured subject for
        # an UPLOAD, which has no `{uid}` segment at all (`POST /documents` creates the row, so its
        # id exists only in the response). Keying on the path param alone meant a client's feed
        # could show a document viewed, archived or sent, but never the upload that put it there —
        # the very case this arm exists for. `jsonb_exists` tests membership of the `document_uids`
        # list — the same shape the `?document=` filter and `_search_clause` already match on.
        "       OR EXISTS (SELECT 1 FROM documents.document_contacts dc "
        "                    JOIN documents.documents d ON d.id = dc.document_id "
        "                   WHERE dc.contact_id = :contact "
        "                     AND (d.uid = a.path_params->>'uid' "
        "                          OR jsonb_exists(a.metadata->'subject'->'document_uids', d.uid)) "
        f"                    AND {_DOCUMENT_FAMILY_SQL}) "
        # BOTH SPELLINGS. Every contact route has recorded `contact_uid` since clients became
        # uid-addressed, so testing only `contact_id` matched nothing written since — a client with
        # a full edit history opened an EMPTY Event logs tab, and the facet dropdowns computed from
        # this same clause offered nothing.
        #
        # BOUND, not re-derived. This first read the uid back out of the database
        # (`(SELECT uid FROM crm.contacts WHERE id = :contact)`, written twice, and the facet query
        # pays for the same clause again on the same page load) on the reasoning that threading it
        # through the callers would add a parameter to carry a value the database already holds.
        # That had it backwards: the REQUEST arrives as `?contact=<uid>` and the router turns it
        # into the serial (`_contact_filter_id`), so the uid is already in the caller's hand and the
        # subquery was converting it straight back. Passing it removes work rather than adding it.
        "       OR a.path_params->>'contact_uid' = :contact_uid "
        "       OR a.metadata->'subject'->>'contact_uid' = :contact_uid "
        "       OR a.path_params->>'contact_id' = :contact_text "
        "       OR a.metadata->'subject'->>'contact_id' = :contact_text "
        "       OR a.metadata->'subject'->>'client_contact_id' = :contact_text "
        "       OR a.metadata->'subject'->>'participant_contact_id' = :contact_text "
        "       OR a.metadata->'subject'->>'intended_contact_id' = :contact_text "
        "       OR a.metadata->'subject'->>'assignee_contact_id' = :contact_text "
        "       OR a.metadata->'subject'->>'subject_contact_id' = :contact_text "
        # The contact_ids LIST — matched by TEXT membership (like the scalars above), not
        # `@> <jsonb number>`: jsonb containment is type-strict and would silently miss ids
        # captured as strings.
        "       OR (jsonb_typeof(a.metadata->'subject'->'contact_ids') = 'array' "
        "           AND EXISTS (SELECT 1 FROM jsonb_array_elements_text("
        "               a.metadata->'subject'->'contact_ids') AS cid "
        "               WHERE cid = :contact_text))) "
    )


def _deny_clause(exclude_event_prefixes: Sequence[str], params: dict[str, Any]) -> str:
    """The endpoint's server-side event-type deny list as SQL, binding one named param per prefix
    into ``params``.

    Shared by the page and the facet query so the two cannot apply it with different force — see
    ``action_facets``: a facet list is a disclosure too, and the firm view's AML exclusion has to
    reach BOTH or the dropdown becomes the very side-channel the deny list exists to close. The
    prefixes are a constant chosen by the endpoint (never request input); ``NOT LIKE`` is emitted
    so it composes as an AND after every caller filter, and an ``event_type`` filter of exactly a
    denied prefix still returns nothing."""
    sql = ""
    for i, prefix in enumerate(exclude_event_prefixes):
        sql += f" AND a.event_type NOT LIKE :deny_{i} "
        params[f"deny_{i}"] = f"{like_escape(prefix)}%"
    return sql


def parse_instant(value: str | None, field: str) -> datetime | None:
    """Parse a caller-supplied date/timestamp filter, or 422.

    A bare ``YYYY-MM-DD`` is taken as midnight UTC. Anything unparseable is a **client** error:
    the alternative — handing the string to Postgres as ``CAST(:since AS timestamptz)`` — turns
    ``?since=yesterday`` into a psycopg DataError and a 500."""
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be an ISO date or timestamp (e.g. 2026-07-28)",
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


#: Ticket mints used to be written as these two types. They are no longer emitted — a ticket
#: is not an action — but ``audit.actions`` is append-only, so the rows already stored must stay
#: out of the feed. They are history, not a live route the tripwire should demand.
FEED_HIDDEN_RETIRED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "notification.stream_opened",
        "message.stream_opened",
        # Retired October 2026: read state and previews. These fired on a page view, a scroll, a
        # bell being cleared or a dialog being opened, and not one of them answers a question
        # anybody brings to an audit trail - see `EXEMPT_MUTATING_ROUTES` for the reasoning. The
        # routes still exist and still work; they simply write no row now. These names stay here so
        # the rows already written stay out of the feed, exactly as they did while they were live.
        "conversation.read",
        "notification.read",
        "notification.all_read",
        "notification.dismissed",
        "notification.all_dismissed",
        "template.preview_rendered",
        "template.preview_pdf_rendered",
        "email_template.previewed",
        "money_movement.previewed",
        # Retired with them: a flow simulation is side-effect-free by construction (an AST scan
        # pins it), so an author iterating on a draft minted a row per attempt and changed nothing.
        "flow.simulated",
        # The Creditsafe console's sync. No route writes it now; its rows are a copy of the
        # vendor's data, so they stay out of the feed.
        "aml.sync_ran",
    }
)

#: THE ROUTINE, as a set: what the FEED does not lead with. Named for what it is rather than for a
#: surface - this was briefly the split behind an Audit tab beside Actions, and the tab is gone.
#:
#: It is left out of the Activity log and its filter options (`fetch_actions`, `action_facets`) and
#: out of a member's recorded activity (`directory.py`, `page_views.py`). The rows themselves stay
#: in `audit.actions` for the trigger consumer and an investigation - contrast the AML deny list and
#: the contact gate, which are security controls that withhold rows from a reader.
#:
#: A row is worth LEADING WITH when all three hold, and a member of this set fails at least one:
#:
#:   1. A PERSON did it - not a clock, not a worker, not a poller.
#:   2. Something DURABLE changed - not a read, not a preview, not a dry run.
#:   3. The change was THE POINT - not a side effect of copying somebody else's data into ours.
#:
#: Nothing is ever DELETED to tidy the feed: ``flows/triggers.py`` tails this same table, so
#: dropping an event type takes a trigger a firm could be given, not merely a row. Leaving the row
#: out of the log is the lever; deleting it never is.
#:
#: Default-to-SHOWN for an unclassified event, so something new surfaces rather than being buried
#: by an omission nobody notices. Pinned to live routes by
#: ``tests/test_actions_tripwire.py``; the per-event reasoning is in
#: ``docs/event-audit-2026-09.md``.
ROUTINE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        # The vocabulary as it stands.
        # The same act at scale, and the line this set was missing: its three siblings were
        # here from the start, so a "dismissed a notification all" row sat in the feed while
        # dismissing ONE was correctly hidden.
        "notification.preferences_updated",
        "notification.client_preferences_updated",
        # -- NO PERSON DID IT ---------------------------------------------------------------
        # A clock. Kept in the stream precisely because a firm may want to NOTIFY on it —
        # deleting the event would take the trigger with it.
        "task.due_soon",
        # -- NOTHING DURABLE CHANGED --------------------------------------------------------
        # `document.viewed` is deliberately NOT here: it changes nothing durable, but it is a
        # read of a CLIENT'S OWN FILE, which is the first thing a complaint review asks about
        # (`test_contact_scope_reaches_the_client_own_document_events`). Rule 2 is about the
        # machine's bookkeeping, not access to client data. Its real problem is VOLUME, and that
        # is a de-duplication job rather than a reason to withhold it.
        # A dry run that DOES still write a row: a test firing changes nothing itself, but it can
        # send a real message, so it stays captured and merely stays out of the feed. (`simulate`
        # is no longer written at all - it moved to `EXEMPT_MUTATING_ROUTES`, and its name is in
        # the retired set above.)
        "automation.tested",
        # -- THE CHANGE WAS A COPY, NOT A DECISION ------------------------------------------
        # Each rewrites our copy of somebody else's data, or re-runs a job. WHO pressed the
        # button is worth keeping — `platform.merge_runs` stamps the run and not the person —
        # but a firm scanning for what its staff DID is not looking for these.
        "firm.synced",
        "firm.roles_synced",
        "firm.usage_refreshed",
        "firm.provider_data_refreshed",
        "firm.provider_refresh_resumed",
        "platform.all_firms_refreshed",
        "platform.rebuilt",
        "environment.registry_refreshed",
        "document.render_retried",
        "flow.dead_letter_replayed",
        # No longer emitted. Kept so a ticket mint already in the stream stays out of the feed.
        *FEED_HIDDEN_RETIRED_EVENT_TYPES,
        # …and what every row written before the ``resource.verb`` rename carries. NOT optional: the
        # stream is append-only, and these are the highest-volume types in it (one ticket row per
        # bell or chat connection — hundreds on a dev database alone). Dropping the old spellings
        # would un-hide all of that history at once and bury the feed, which reads as the feature
        # being broken rather than as a rename with a missed line.
        "action.notifications-ws-ticket",
        "action.messaging-messaging-ws-ticket",
        "action.notifications-mark-notification-read",
        "action.notifications-mark-all-notifications-read",
        "action.notifications-dismiss-notification",
        "action.notifications-dismiss-all-notifications",
        "action.messaging-messaging-thread-read",
        "action.messaging-messaging-conversation-read",
        # …and the same for everything the audit added above, derived from `actions.EVENT_TYPES`
        # (plus `SYNTHETIC_EVENT_TYPES`) — which is why the tripwire pins the two halves to the
        # same size: a type hidden going forward whose history still shows is half-applied.
        "action.aml-run-aml-sync",
        "action.automations-test-automation",
        "action.documents-retry-document",
        "action.email-templates-preview-email-template",
        "action.admin-admin-preview-email-template",
        "action.admin-refresh-environment-registry",
        "action.admin-refresh-tenant-provider",
        "action.admin-resume-tenant-providers",
        "action.admin-sync-tenant-roles",
        "action.admin-sync-tenant",
        "action.admin-refresh-tenant-usage",
        "action.flows-replay-dead-letter",
        "action.flows-simulate-flow",
        "action.admin-refresh-platform-tenants",
        "action.admin-platform-rebuild",
        "action.tasks-due-soon",
        "action.templates-render-template-preview-pdf",
        "action.templates-render-template-preview",
        "action.notifications-set-notification-preferences",
        "action.notifications-set-notification-client-preferences",
    }
)


def _event_type_clause(event_type: str | None, params: dict[str, Any]) -> str:
    """The ``event_type`` filter as an OR of PREFIX matches — one comma-separated param, N prefixes.

    ONE prefix is the whole of what this used to be, and that spelling is unchanged:
    ``action.firm-`` still narrows to a family and an exact type is its own prefix. What is new is
    that the picker sends several, because a trail question usually names alternatives ("who touched
    a document or a request") and asking it one operation at a time means reading the page N times.

    ORed, NOT ANDed — the opposite of ``_tags_clause`` beside it, and deliberately: two event types
    are two alternatives, since no single row can be both, where two entity KINDS are two things one
    row must touch. ANDing here would answer every multi-select with an empty page, which reads as
    "nobody did this" rather than as a filter that cannot be satisfied.

    Blank elements are dropped, so ``event_type=,`` / ``event_type=`` is no filter rather than a
    filter for the empty prefix — which ``LIKE '%'`` would make match everything, so a hand-built
    ``,x`` would silently widen to the whole stream instead of narrowing to ``x``.
    """
    prefixes = [p.strip() for p in (event_type or "").split(",") if p.strip()]
    if not prefixes:
        return ""
    parts = []
    for i, prefix in enumerate(prefixes):
        params[f"et_{i}"] = f"{like_escape(prefix)}%"
        parts.append(f"a.event_type LIKE :et_{i}")
    return " AND (" + " OR ".join(parts) + ") "


def _actor_clause(actor: str | None, params: dict[str, Any]) -> str:
    """The ``actor`` filter as an OR of SUBSTRING matches — one comma-separated param, N names.

    ONE name is what this used to be — a single substring tested against the WorkOS sub, the
    joined email and the joined name. WIDENED because "Andy Gaunt or Craig Currie" is an ordinary
    question a lone picker cannot ask, the same argument that grew ``event_type`` into a list — the
    picker's options are exact display names (``actorSuggestions``' own precedence), so in practice
    each element is a whole name, but the ILIKE substring match survives per element for a
    hand-edited URL or a bookmark carrying a partial one.

    ORed, like ``_event_type_clause`` and for the same reason: two named actors are two
    alternatives, since no row was written by both.

    Blank elements are dropped, so ``actor=,`` / ``actor=`` is no filter rather than a filter for
    the empty substring — which ``ILIKE '%%'`` would match every row.
    """
    names = [a.strip() for a in (actor or "").split(",") if a.strip()]
    if not names:
        return ""
    parts = []
    for i, name in enumerate(names):
        params[f"actor_{i}"] = f"%{like_escape(name)}%"
        parts.append(
            f"(a.actor_sub ILIKE :actor_{i} OR u.email ILIKE :actor_{i} OR u.name ILIKE :actor_{i})"
        )
    return " AND (" + " OR ".join(parts) + ") "


def _method_clause(method: str | None, params: dict[str, Any]) -> str:
    """The ``method`` filter as an OR of EXACT matches — one comma-separated param, N verbs.

    ONE verb, exact, is what this used to be. WIDENED because "Created or Deleted" is an ordinary
    question a lone picker cannot ask. Each element is upper-cased at this boundary, same as the
    single-value form was, so a caller need not know the stream stores upper-case verbs.

    Bound as ONE ``text[]`` array (the ``ROUTINE_EVENT_TYPES``/``_tags_clause`` uid-array
    pattern) rather than N named params like ``_actor_clause``: an exact match needs no per-element
    bind, so one array and one ``= ANY`` says the same thing with one parameter instead of N.

    Blank elements are dropped, so ``method=,`` / ``method=`` is no filter.
    """
    methods = [m.strip().upper() for m in (method or "").split(",") if m.strip()]
    if not methods:
        return ""
    params["methods"] = methods
    return " AND a.method = ANY(CAST(:methods AS text[])) "


def tenant_scope_sql(tenant: str | None, params: dict[str, Any]) -> str:
    """The admin endpoint's operator-chosen firm SCOPE: comma-separated tenant UIDs, ORed, each
    matching EITHER side of an event (the acting tenant or the proxied target) — an action Graphene
    took ON a firm belongs in that firm's answer as much as one its own staff took.

    UNLIKE ``_actor_clause``/``_method_clause`` beside it, this returns the WHOLE ``scope_sql``
    (the ``fetch_actions``/``action_facets`` contract — a bare boolean expression spliced straight
    after ``WHERE``), not an "AND …" fragment: no tenant named ⇒ UNSCOPED, every firm this operator
    may read, which is the admin surface's default and the reason the predicate has to stay
    self-contained rather than composing onto a `1=1` some other caller supplies.

    ONE uid is what this used to be. WIDENED because "Firm A or Firm B" is an ordinary question once
    an operator is scanning several firms at once — the same argument that grew
    actor/method/event_type into lists. Public (no leading underscore, unlike its siblings): the
    router builds `scope_sql` from it directly, once, and hands the SAME string to both
    `fetch_actions` and `action_facets` — see `routers/admin.py::list_audit_actions`.

    Bound as ONE ``text[]`` array (the ``_method_clause`` pattern). Blank elements are dropped, so
    ``tenant=,`` / ``tenant=`` is unscoped rather than a scope for the empty uid, which would match
    no row at all — the opposite of the admin surface's "nothing named ⇒ everything" default.
    """
    tenants = [t.strip() for t in (tenant or "").split(",") if t.strip()]
    params["tenants"] = tenants or None
    return (
        "(CAST(:tenants AS text[]) IS NULL "
        "OR t.uid = ANY(CAST(:tenants AS text[])) "
        "OR tt.uid = ANY(CAST(:tenants AS text[])))"
    )


#: The DOCUMENT family as a SQL predicate: the four resources whose events are document activity,
#: plus the two prefixes every row written before the ``resource.verb`` rename carries.
#:
#: Both spellings, and the legacy pair is not optional: ``audit.actions`` is append-only, so a
#: document's activity rail is mostly HISTORY. Matching only the current names would empty the rail
#: for every document that existed before the rename — which reads as "nothing ever happened to this
#: document", the one answer an activity trail must never give wrongly.
#:
#: Exact prefixes INCLUDING the dot: ``document.`` must not also match ``document_type.``, or a type
#: rename would surface in a single document's rail.
_DOCUMENT_FAMILY_PREFIXES: tuple[str, ...] = (
    "document.",
    "document_type.",
    "document_request.",
    "document_share.",
    "action.documents-",
    "action.shares-",
)
#: The same as one OR-ed LIKE, built once, PARENTHESISED. A developer-written constant, never
#: request input.
#:
#: The brackets are load-bearing. This is spliced into a clause that ANDs it with a uid test, and
#: `AND` binds tighter than `OR` — so a bare chain reads as `(uid = :document AND first_prefix) OR
#: second_prefix OR …`, which matches EVERY document-family row in the stream whatever uid it
#: carries. One document's activity rail then shows every document's, and it answers 200 doing it.
_DOCUMENT_FAMILY_SQL = (
    "("
    + " OR ".join(f"a.event_type LIKE '{prefix}%'" for prefix in _DOCUMENT_FAMILY_PREFIXES)
    + ")"
)


#: Every place a row can carry a contact id (the same list as ``TAG_FIELDS['contact']`` and
#: ``_contact_clause``), as a SQL array expression — so a search can ask "does this row name anybody
#: called X" without knowing which field held the id.
#:
#: The list-valued ``contact_ids`` (a document sent to several people) is unioned in through a
#: ``jsonb_typeof`` guard: ``jsonb_array_elements_text`` RAISES on a non-array, and a single
#: malformed
#: old row would fail the whole page rather than simply not matching.
#:
#: An ACCOUNT names its holder. An account event (`account.bank_accounts_updated` and the rest)
#: records only the account's id, so the holder is read off `platform.accounts`: without it the row
#: names nobody, the contact gate reads it as firm business, and an adviser outside the holder's
#: scope reads the account's bank details. The serial is cast only when it is all digits, so a
#: malformed old row fails to match instead of failing the page.
#: EVERY handle a row can name a contact by, as one text array — SERIALS AND UIDS MIXED, which is
#: why the caller compares against both `c.id::text` and `c.uid`. A contact route has recorded
#: `contact_uid` since clients became uid-addressed, so a serial-only array made the free-text
#: search by client NAME blind to every row written since — and that search is the one control on
#: the toolbar whose whole purpose is that an id is not something a reader knows.
_ROW_CONTACT_IDS_SQL = """ARRAY(
  SELECT v FROM (
    SELECT a.path_params->>'contact_id' AS v
    UNION ALL SELECT a.path_params->>'contact_uid'
    UNION ALL SELECT a.metadata->'subject'->>'contact_id'
    UNION ALL SELECT a.metadata->'subject'->>'contact_uid'
    UNION ALL SELECT a.metadata->'subject'->>'client_contact_id'
    UNION ALL SELECT a.metadata->'subject'->>'participant_contact_id'
    UNION ALL SELECT a.metadata->'subject'->>'intended_contact_id'
    UNION ALL SELECT a.metadata->'subject'->>'assignee_contact_id'
    UNION ALL SELECT a.metadata->'subject'->>'subject_contact_id'
    UNION ALL SELECT cid FROM jsonb_array_elements_text(
      CASE WHEN jsonb_typeof(a.metadata->'subject'->'contact_ids') = 'array'
           THEN a.metadata->'subject'->'contact_ids' ELSE '[]'::jsonb END) AS cid
    UNION ALL SELECT pa.contact_id::text FROM platform.accounts pa
     WHERE pa.uid IN (a.path_params->>'account_uid', a.metadata->'subject'->>'account_uid')
        OR pa.id IN (
          SELECT x.v::int FROM (VALUES (a.path_params->>'account_id'),
                                       (a.metadata->'subject'->>'account_id')) x(v)
           WHERE x.v ~ '^[0-9]{1,9}$')
  ) s WHERE v IS NOT NULL
)"""


#: WHAT THIS READER MAY SEE, as a scope clause — the trail's half of `crm.contacts`' read policy.
#:
#: THE HOLE IT CLOSES. `activity:read` and `contacts:read_all` are different permissions, and the
#: firm roles do not hold them together: an ADVISER has `activity:read` and NOT `contacts:read_all`.
#: So an adviser could read every event about every client in the firm — including the bank details
#: on `client.bank_account_updated` — while `crm.contacts` would refuse them the record those
#: details live on. The trail was a way round the client book.
#:
#: The rule, mirroring `crm.contacts`:
#:
#:   * `contacts:read_all` sees the firm's whole trail, exactly as it sees the whole book;
#:   * otherwise a row is readable only if the client it NAMES is in `auth.contact_scope()`;
#:   * a row that names NO client is firm business (branding, a template, a flow) and stays visible.
#:
#: NO ACTOR EXEMPTION, deliberately. "I did it" is not a reason to be shown it afterwards: an
#: operator or a colleague who touched a client they cannot open must not get the details back out
#: of the audit row. The trail answers what happened to the records you may read, not what you once
#: had your hands on.
#:
#: Applied through `scope_sql` so it narrows the FACETS too (`action_facets` takes the same clause)
#: — a dropdown offering a client you may not read is a disclosure in its own right, which is the
#: same reasoning as the AML facet rail in `routers/firm.py`.
# noqa: S608 — the only interpolation is `_ROW_CONTACT_IDS_SQL`, a module constant of
# developer-written SQL. Request input never reaches this string: the reader's own scope
# arrives through `auth.contact_scope()`, read from the claims the session published, and
# there is no bind parameter here at all.
CONTACT_SCOPE_SQL = f"""(
  auth.can_read_all()
  OR cardinality({_ROW_CONTACT_IDS_SQL}) = 0
  OR EXISTS (
    SELECT 1 FROM crm.contacts c
     WHERE c.id = ANY(auth.contact_scope())
       AND (c.id::text = ANY({_ROW_CONTACT_IDS_SQL}) OR c.uid = ANY({_ROW_CONTACT_IDS_SQL}))
  )
)"""  # noqa: S608


def _search_clause(query: str | None, params: dict[str, Any]) -> str:
    """The toolbar's free-text search: one box over everything a reader might recognise a row by.

    WHAT THIS REPLACED, AND WHY. The box used to be a second event-TYPE control — it filtered on the
    `event_type` prefix, which is what the event-type picker beside it now does properly (with the
    whole vocabulary, several at a time). So the box was answering a question that already had a
    better control, while the question people actually bring to an audit trail — "what happened to
    Margaret Cole", "who touched the suitability report" — had no control at all.

    Searched, ORed:

    * the ACTOR (WorkOS sub, email or display name) — the same reach as the `actor` filter, as a
      substring, so a partial name works;
    * the CLIENT the row names, by NAME, through every field an id can arrive in
      (`_ROW_CONTACT_IDS_SQL`). This is the one that makes the box worth having: an id is not
      something a reader knows, and the name is.
    * the DOCUMENT the row names, by name or its type, for the `{uid}` document family;
    * the EVENT TYPE and the ROUTE, so a name pasted out of the detail panel or a support ticket
      still finds its rows.

    A substring match (`ILIKE %q%`), deliberately: a reader typing "cole" should find "Margaret
    Cole",
    and prefix-only matching on a person's name is a filter that appears broken. The cost is that
    this
    cannot use an index and is evaluated per candidate row — the same property the pre-existing
    `actor`
    filter already has. If the stream outgrows that, the answer is a generated tsvector column, not
    a
    narrower search: a search that misses is worse than a search that is slow, because nothing on
    screen says it missed.

    The clause is ANDed with everything else, so a search NARROWS the current filters rather than
    replacing them — and it composes with the scope predicate and the AML deny list like every other
    filter, so it can never surface a row the caller may not read.
    """
    text_query = (query or "").strip()
    if not text_query:
        return ""
    params["q"] = f"%{like_escape(text_query)}%"
    # noqa: S608 — the only interpolation is `_DOCUMENT_FAMILY_SQL`, a module constant built from a
    # literal tuple of prefixes. The reader's text binds as `:q` and never reaches the SQL text.
    return (
        "  AND ("  # noqa: S608 - see the note above; only module constants are interpolated
        # The actor, by any of the three things we hold for them.
        "       a.actor_sub ILIKE :q OR u.email ILIKE :q OR u.name ILIKE :q "
        # The client this row names, BY NAME — the same person form the read side labels chips with.
        # Either handle, ONE evaluation. `_ROW_CONTACT_IDS_SQL` mixes serials and uids (the stream
        # holds rows from both sides of the uid rename), so both of the contact's own handles have
        # to be tested against it — but written as two `= ANY(...)` arms that whole eleven-branch
        # correlated `ARRAY(... UNION ALL ... jsonb_array_elements_text ...)` is BUILT TWICE, per
        # candidate row, on a clause this function's own docstring already describes as
        # un-indexable and evaluated per row. Postgres will not reliably factor two independently
        # written correlated subqueries into one. Array overlap (`&&`) asks the same question once.
        "    OR EXISTS (SELECT 1 FROM crm.contacts c "
        f"                WHERE ARRAY[c.id::text, c.uid] && {_ROW_CONTACT_IDS_SQL} "
        "                  AND COALESCE(NULLIF(trim(concat_ws(' ', c.first_name, c.surname)), ''), "
        "                               c.name) ILIKE :q) "
        # The document this row names (the `{uid}` param, gated to the document family so a template
        # or form uid that coincides cannot match), plus the reassign's superseded target.
        #
        # THE NAME TEST COMES FIRST, and the row match is one bracketed alternative. Written the
        # other way round — `(uid AND family) OR target AND name` — `AND` binds tighter than
        # `OR`, so the first branch loses the name test and every document event in the stream
        # matches whatever was typed. Second time this shape has bitten in this file (see
        # `_DOCUMENT_FAMILY_SQL`), and both times it answers 200 with a wrong page.
        "    OR EXISTS (SELECT 1 FROM documents.documents d "
        "                WHERE COALESCE(d.name, d.document_type) ILIKE :q "
        "                  AND ((d.uid = a.path_params->>'uid' "
        f"                        AND {_DOCUMENT_FAMILY_SQL}) "
        "                       OR d.uid = a.metadata->'subject'->>'target_document_uid' "
        # …and an upload, which names its documents as a list — see the `?document=` filter.
        "                       OR jsonb_exists(a.metadata->'subject'->'document_uids', d.uid))) "
        # …and the machine facts, for a value pasted out of the detail panel.
        "    OR a.event_type ILIKE :q OR a.route ILIKE :q) "
    )


async def fetch_actions(
    session: AsyncSession,
    *,
    scope_sql: str,
    scope_params: dict[str, Any] | None = None,
    exclude_event_prefixes: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
    before: int | None = None,
    after: int | None = None,
    event_id: int | None = None,
    actor: str | None = None,
    event_type: str | None = None,
    query: str | None = None,
    method: str | None = None,
    proxied: bool | None = None,
    since: str | None = None,
    until: str | None = None,
    contact: int | None = None,
    #: The same client as the caller received it — `?contact=<uid>`. Passed rather than read
    #: back out of the database; see `_contact_clause`.
    contact_uid: str | None = None,
    document: str | None = None,
    document_family: bool = False,
    tags: Sequence[str] = (),
) -> ActionsPage:
    """One page of the stream, newest first, joined to display names: ``{items, has_more}``.

    Rows come back verbatim — ``path_params``/``metadata`` were redacted at WRITE time
    (``app/audit_actions.py::redact``), so there is nothing left to filter here — with the actor and
    both tenant sides (acting + proxied target) joined in the same query (no N+1), each side
    carrying its branding icon (``tenant_icon_url``/``target_tenant_icon_url``, see ``_icon_sql``)
    so the UI can render firm chips without a second lookup. The table
    aliases ``a`` (actions), ``u`` (actor), ``t`` (acting tenant) and ``tt`` (target tenant) are
    part of this function's contract: ``scope_sql`` may reference them.

    Filters (server-side, over the whole stream): ``actor`` (comma-separated NAMES, ORed —
    substring of the WorkOS sub, email or name each; see ``_actor_clause``), ``query`` (the
    toolbar's free-text search — actor, the CLIENT's name, the document's name, the event type and
    the route; see ``_search_clause``), ``event_type`` (comma-separated PREFIXES, ORed — a resource
    narrows to a family, and several name alternatives), ``method`` (comma-separated VERBS, ORed —
    see ``_method_clause``), ``proxied`` (target_tenant_id present), ``since``/``until`` (ISO;
    inclusive/exclusive, parsed here so ``?since=yesterday`` is a 422 not a 500), ``contact``
    (every event that NAMES a given client — its own-record edits under
    ``/crm/contacts/{id}`` plus every event whose path or
    allowlisted subject carries a contact id, the ``contact_ids`` LIST included) and ``document``
    (one document's events — the view/render/share/send/reassign it appears in). Keyset pagination
    via ``before`` on the monotonic id; ``limit`` clamped to 200; ``has_more`` from an over-fetch of
    one row rather than the full-page heuristic.

    ``contact``/``document`` AND with ``scope_sql`` and the deny list exactly as every other filter,
    so neither can surface an event the caller's scope or the endpoint's AML deny list would hide.
    They match on the ids the write path already stores (``metadata.subject`` / ``path_params``);
    an event that names a client only INDIRECTLY (via an account, or a document the client is linked
    to but the event does not name) is a documented limitation, not caught by this static filter.

    ``document_family`` narrows to the DOCUMENT family — the ``action.documents-`` and
    ``action.shares-`` prefixes together (what the old ``documents.audit_log`` feed carried; the
    share-links feature is retired, but its historic rows must still read) — for
    the document-activity glances (the library's recent-activity card, a client's document rail). It
    is a two-prefix OR the single-prefix ``event_type`` filter cannot express; it composes with
    ``contact`` (a client's document events) exactly as the other filters compose.

    ``after`` is the OPPOSITE keyset end from ``before`` and exists for the live poll: it asks
    "what has happened since the newest row I hold", newest first, so a watching reader can be told
    there are three new events and be given them without refetching the pages they already hold.
    Critically it composes with every other filter — the count a reader is offered must be a count
    of events they would actually see, or the pill says "3 new" and showing them reveals none — and
    it is ANDed with the deny list like everything else, so the firm view can never COUNT an AML
    event it is forbidden to display. Combining it with ``before`` is a bounded window, and unused.

    ``event_id`` fetches a SINGLE event by id — the master-detail deep-link, for a selected event
    not on the current page. It sits inside the same ``WHERE`` as ``scope_sql`` and the deny list,
    so it is ANDed with both: it can never surface an event the caller's scope (their firm) or the
    endpoint's deny list (the firm view's AML family) would otherwise hide — a guessed id returns
    nothing rather than a leaked row."""
    limit = max(1, min(limit, 200))
    params: dict[str, Any] = {
        **(scope_params or {}),
        "before": before,
        "after": after,
        "event_id": event_id,
        "proxied": proxied,
        "since": parse_instant(since, "since"),
        "until": parse_instant(until, "until"),
        "contact": contact,
        "contact_uid": contact_uid,
        # Text for the ``->>`` comparisons AND the jsonb cast: ``str(96)`` == ``"96"`` is a valid
        # jsonb number literal, so ``contact_ids @> CAST('96' AS jsonb)`` tests array membership.
        "contact_text": str(contact) if contact is not None else None,
        "document": document.strip() if document and document.strip() else None,
        # True narrows to the document family (documents- + shares-); None disables the filter.
        "document_family": True if document_family else None,
        # Over-fetch one row: its presence IS the has_more answer, and it never ships.
        "limit": limit + 1,
        "routine": list(ROUTINE_EVENT_TYPES),
    }
    # The deny list is a server-side constant chosen by the endpoint (never request input), one
    # named bind param per prefix. NOT LIKE, after every caller filter: an event_type filter of
    # exactly a denied prefix must still return nothing. Built by the shared `_deny_clause` so the
    # facet query (`action_facets`) cannot apply it with anything less than this force.
    deny_sql = _deny_clause(exclude_event_prefixes, params)
    # The entity-kind filter, ANDed per selected kind — see `_tags_clause`.
    tags_sql = _tags_clause(tags, params)
    # The event-type filter, ORed per selected prefix — see `_event_type_clause`.
    event_type_sql = _event_type_clause(event_type, params)
    actor_sql = _actor_clause(actor, params)
    method_sql = _method_clause(method, params)
    # The toolbar's free-text search over actor / client name / document / type / route.
    search_sql = _search_clause(query, params)

    rows = (
        (
            await session.execute(
                text(
                    "SELECT a.id, a.event_type, a.created_at, a.method, a.route, a.status, "  # noqa: S608 - developer-written constants (see docstring); request input only ever binds
                    "  a.path_params, a.metadata, a.actor_id, a.actor_sub, "
                    "  u.email AS actor_email, u.name AS actor_name, "
                    "  a.tenant_id, t.uid AS tenant_uid, t.name AS tenant_name, "
                    f"  {_icon_sql('t')} AS tenant_icon_url, "
                    "  a.target_tenant_id, tt.uid AS target_tenant_uid, "
                    "  tt.name AS target_tenant_name, "
                    f"  {_icon_sql('tt')} AS target_tenant_icon_url "
                    "FROM audit.actions a "
                    "LEFT JOIN auth.users u ON u.id = a.actor_id "
                    "LEFT JOIN auth.tenants t ON t.id = a.tenant_id "
                    "LEFT JOIN auth.tenants tt ON tt.id = a.target_tenant_id "
                    f"WHERE {scope_sql} "
                    "  AND (CAST(:before AS bigint) IS NULL OR a.id < :before) "
                    "  AND (CAST(:after AS bigint) IS NULL OR a.id > :after) "
                    "  AND (CAST(:event_id AS bigint) IS NULL OR a.id = :event_id) "
                    f"{actor_sql}"
                    # The caller's event-type filter: an OR of prefix matches, or nothing at all.
                    f"{event_type_sql}"
                    # …and their free-text search, ANDed so it narrows rather than replaces.
                    f"{search_sql}"
                    f"{method_sql}"
                    "  AND (CAST(:proxied AS boolean) IS NULL "
                    "       OR (a.target_tenant_id IS NOT NULL) = :proxied) "
                    "  AND (CAST(:since AS timestamptz) IS NULL OR a.created_at >= :since) "
                    "  AND (CAST(:until AS timestamptz) IS NULL OR a.created_at < :until) "
                    # Every event NAMING this client: every path/subject that carries a contact id
                    # — which covers its own-record edits too, because EVERY contact route spells
                    # the param `contact_id` (`PATCH /firm/contacts/{contact_id}`,
                    # `/crm/contacts/{contact_id}/…`). There is deliberately no `path_params->>'id'`
                    # branch: no route under contacts uses a bare `{id}`, so such a clause could
                    # never match a real event. Plus the contact_ids LIST (a document sent to many).
                    # Mirrors admin._ENTITY_FIELD_KINDS.
                    f"{_contact_clause()}"
                    # One document's events: the {uid} path param (gated to the document/shares
                    # families so a template/form {uid} that coincides can't match) + the reassign's
                    # superseded target.
                    # The `action.shares-%` arm is a RETIRED feature's vocabulary, kept on purpose:
                    # share links are gone and nothing writes such a row any more, but the rows
                    # already written must keep appearing on a document's history. Same for the
                    # family filter below. Do not remove for symmetry with the deleted producers.
                    "  AND (CAST(:document AS text) IS NULL "
                    "       OR (a.path_params->>'uid' = :document "
                    f"           AND {_DOCUMENT_FAMILY_SQL}) "
                    "       OR a.metadata->'subject'->>'target_document_uid' = :document "
                    # An UPLOAD names the documents it created as a LIST (`document_uids`): one
                    # drop may carry several files, and the uid cannot come from the path because
                    # the row does not exist when the request arrives. Without this arm a
                    # document's Events tab began at its first LATER event, never at its upload.
                    # `jsonb_exists` rather than the `?` operator: `?` is a bind marker to several
                    # drivers, and this string is interpolated into a text() query.
                    "       OR jsonb_exists(a.metadata->'subject'->'document_uids', :document)) "
                    # The document FAMILY, for the document-activity glances — several
                    # prefixes the caller's own single-prefix filter cannot express.
                    "  AND (CAST(:document_family AS boolean) IS NULL "
                    f"       OR {_DOCUMENT_FAMILY_SQL}) "
                    # Routine rows stay in the table and out of the feed.
                    "  AND a.event_type <> ALL(CAST(:routine AS text[])) "
                    f"{deny_sql}"
                    f"{tags_sql}"
                    "ORDER BY a.id DESC LIMIT :limit"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    items = [dict(r) for r in rows[:limit]]
    for item in items:
        for field, uid_field in (
            ("tenant_icon_url", "tenant_uid"),
            ("target_tenant_icon_url", "target_tenant_uid"),
        ):
            value = item.get(field)
            if isinstance(value, str):
                src = storage.public_asset_src(value)
                name = storage.public_asset_name(src)
                tenant_uid = item.get(uid_field)
                item[field] = (
                    f"{storage.PUBLIC_ASSET_PREFIX}{tenant_uid}/{name}"
                    if name and isinstance(tenant_uid, str)
                    else src
                )
    return {"items": items, "has_more": len(rows) > limit}


async def action_facets(
    session: AsyncSession,
    *,
    scope_sql: str,
    scope_params: dict[str, Any] | None = None,
    exclude_event_prefixes: Sequence[str] = (),
    window: int = FACET_WINDOW,
    contact: int | None = None,
    #: The same client as the caller received it — `?contact=<uid>`. Passed rather than read
    #: back out of the database; see `_contact_clause`.
    contact_uid: str | None = None,
) -> ActionFacets:
    """The filter VOCABULARY for the caller's slice: ``{event_types, actors}``, both sorted.

    THE BUG THIS EXISTS FOR. The toolbar's dropdown options used to be derived from the LOADED
    rows, and most of these filters are applied by the SERVER — so picking an actor returned only
    that actor's events, and the event-type dropdown then offered only the types that actor
    produced. The value you wanted to switch to was gone, and the only way back was to clear a
    filter whose effect you could no longer see. Narrowing the ROWS is the point of a filter;
    narrowing the CHOICES is not. Two holes the client-side accumulator could not close on its own:
    first paint on an already-filtered URL (a bookmark) starts from a filtered page, and a list
    derived from a 50-row page is never complete. So the server states the vocabulary directly.

    **Filter-independent, deliberately.** ``event_type``/``actor``/``method``/``since``/``until``/
    ``proxied``/``contact``/``document`` are all ignored here — that IS the contract. The only
    things that narrow a facet list are the ones that narrow what the caller may READ at all:

    * ``scope_sql``/``scope_params`` — the SAME predicate the page uses, supplied by the caller so
      the two physically cannot disagree about whose rows these are (the endpoints hoist it into
      one local and hand it to both). The alias contract is ``fetch_actions``': ``a`` (actions),
      ``u`` (actor), ``t`` (acting tenant), ``tt`` (target tenant), all joined here too.
    * ``exclude_event_prefixes`` — **the safety rail, and the reason this function needs care.** A
      facet list is a DISCLOSURE, not just a convenience: a firm whose event-type dropdown offered
      "Screened a client" would learn from the dropdown alone that screening happened, which is
      exactly what the firm view's AML exclusion (``routers/firm.py::FIRM_EXCLUDED_EVENT_PREFIXES``,
      the POCA 2002 s.333A tipping-off control — docs/aml.md) exists to prevent. It is applied here
      with the same force as to the page, through the shared ``_deny_clause``, and pinned by
      ``tests/test_firm_audit_actions.py``. Do not weaken it for symmetry with the admin view.
    * ``ROUTINE_EVENT_TYPES`` — the always-applied feed hiding. Offering an option for a type
      the feed never shows would filter to zero rows.

    **The facets describe the RECENT stream, not all history.** They are computed over the most
    recent ``window`` (``FACET_WINDOW``) rows of the caller's readable scope, id DESC — ``id`` is
    the PK, and ``actions_tenant_cursor_idx (tenant_id, id)`` supports the per-tenant case. This is
    a deliberate, stated bound, not an oversight: ``audit.actions`` is append-only and unbounded, so
    an un-windowed ``DISTINCT`` would get slower for ever. Nothing is lost from the bar — the client
    merges the loaded rows' own options on top of this seed, so a type or actor reached by
    paginating past the window still appears.

    **Opt-in at both endpoints** (``?facets=true``), never inferred: the toolbar applies a filter
    in place now, so every later fetch re-reads page one with NO cursor and a cursor-derived test
    would recompute the vocabulary on precisely the requests whose contract is that it does not
    change. One windowed scan on page entry, none thereafter.

    ONE round trip: two ``array_agg(DISTINCT …)`` sub-selects over a single windowed CTE. Two
    separate queries would read the tail twice and could straddle a concurrent insert, so the two
    lists would describe slightly different row sets — and the tail is the hot end of an
    append-only table, which is precisely where that happens."""
    params: dict[str, Any] = {
        **(scope_params or {}),
        "window": max(1, window),
        "routine": list(ROUTINE_EVENT_TYPES),
        # The client-record SCOPE, when there is one — see `_contact_clause`. A scope narrows the
        # vocabulary; a FILTER never does. That is the whole distinction this parameter carries: the
        # reader can clear a filter and wants the option back, but cannot clear the record they
        # are standing on, so offering it types and actors it has no events for is a dead choice.
        "contact": contact,
        "contact_uid": contact_uid,
        "contact_text": None if contact is None else str(contact),
    }
    deny_sql = _deny_clause(exclude_event_prefixes, params)
    row = (
        (
            await session.execute(
                text(
                    "WITH recent AS ("  # noqa: S608 - developer-written constants (see docstring); request input only ever binds
                    "  SELECT a.event_type, "
                    # The display name the UI shows and the actor filter matches — name else email
                    # else sub, `actorName`'s precedence exactly (auth.users.name is a generated
                    # column already NULLIF'd to NULL when blank, so plain COALESCE mirrors `??`).
                    # A facet value that did not match the rows it came from would be a dropdown
                    # entry that filters to nothing.
                    "    COALESCE(u.name, u.email, a.actor_sub) AS actor "
                    "  FROM audit.actions a "
                    "  LEFT JOIN auth.users u ON u.id = a.actor_id "
                    "  LEFT JOIN auth.tenants t ON t.id = a.tenant_id "
                    "  LEFT JOIN auth.tenants tt ON tt.id = a.target_tenant_id "
                    f"  WHERE {scope_sql} "
                    "    AND a.event_type <> ALL(CAST(:routine AS text[])) "
                    f"  {deny_sql}"
                    f"  {_contact_clause()}"
                    # The window is taken AFTER the scope and both exclusions, so it is the most
                    # recent N rows the caller may actually READ — not N rows of denied/hidden
                    # noise that would leave the dropdowns empty.
                    "  ORDER BY a.id DESC LIMIT :window"
                    ") "
                    "SELECT COALESCE((SELECT array_agg(DISTINCT event_type ORDER BY event_type) "
                    "                 FROM recent), CAST('{}' AS text[])) AS event_types, "
                    "       COALESCE((SELECT array_agg(DISTINCT actor ORDER BY actor) FROM recent "
                    # A row with no name, email or sub at all offers no actor to filter by — the
                    # same skip `actorSuggestions` makes on the client.
                    "                 WHERE actor IS NOT NULL AND actor <> ''), "
                    "                CAST('{}' AS text[])) AS actors"
                ),
                params,
            )
        )
        .mappings()
        .one()
    )
    return {
        "event_types": [str(v) for v in row["event_types"]],
        "actors": [str(v) for v in row["actors"]],
    }
