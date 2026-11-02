// Types for the authorable notification-rule builder (GET /admin/notification-catalogue +
// /admin/tenants/{id}/notification-rules). Shared by the admin + firm builder surfaces.

export interface CatalogueAudience {
	key: string;
	label: string;
	builtin: boolean;
	default_title: string;
	default_body: string;
	default_link: string | null;
	default_in_app: boolean;
	default_email: boolean;
	/** The email template this audience's notice renders by default (an email.templates code); null
	 *  → the general `notification` wrapper. Prefills the builder's "Email template" dropdown. */
	default_email_code: string | null;
}

/** An email template a rule may render — the "Email template" dropdown option (code + label). */
export interface EmailTemplateOption {
	code: string;
	label: string;
}

/** A merge placeholder a trigger's copy may carry — a dotted key, a human label, and an example the
 *  builder previews with. */
export interface CatalogueParam {
	key: string;
	label: string;
	example: string;
}

export interface CatalogueTrigger {
	key: string;
	label: string;
	description: string;
	category: string;
	/** The underlying audit.actions event type(s) this trigger fires on (raw event metadata). */
	event_types: string[];
	/** The kind of thing the event is about (contact | account | task | application …). */
	subject_kind: string;
	/** Ready-made click destinations that resolve to the specific item the event is about — e.g.
	 *  "The document" → `/documents/{document.uid}`, rendered per-notification like the copy.
	 *  `client` marks a target valid for a client-audience notice (the client portal lacks a
	 *  `/clients/{id}` record and task view, so those are staff-only). */
	link_targets: { key: string; label: string; href: string; client: boolean }[];
	/** The `{placeholders}` this trigger's title/body may use (derived from its subject). */
	params: CatalogueParam[];
	audiences: CatalogueAudience[];
}

/** The general wrapper's own template code — `app/email/catalog.py::WRAPPER_CODE`.
 *
 *  Three jobs on one string, which is why the fallback one is named: it is a member of the code
 *  catalogue, it is spelled the same as the `notification` CATEGORY that groups seven codes, and it
 *  is the null-sentinel a rule falls back to when it names no template. */
export const WRAPPER_CODE = 'notification';

/** A stored rule (a row of notifications.rules). */
export interface NotificationRule {
	uid: string;
	trigger_key: string;
	audience: string;
	role_slug: string | null;
	label: string;
	title: string;
	body: string | null;
	link: string | null;
	priority: string;
	default_in_app: boolean;
	default_email: boolean;
	mandatory: boolean;
	firm_editable: boolean;
	authored_by: string;
	enabled: boolean;
	/** The email template this rule renders (an email.templates code); null → the general wrapper. */
	email_code: string | null;
	/** The tier that made the LAST change ('operations' = Graphene, 'firm'); null if never versioned.
	 *  Drives the "who last edited" badge (vs `authored_by`, which is who FIRST made it). */
	last_editor_scope?: 'operations' | 'firm' | null;
}

/** One entry in a rule's change history (a row of notifications.rule_versions). */
export interface RuleVersion {
	uid: string;
	version_number: number;
	action: 'created' | 'updated' | 'deleted';
	title: string;
	body: string | null;
	link: string | null;
	priority: string;
	default_in_app: boolean;
	default_email: boolean;
	mandatory: boolean;
	enabled: boolean;
	email_code: string | null;
	/** Who made this change: 'operations' = Graphene (admin portal), 'firm' = the firm's own staff. */
	authored_by_scope: 'operations' | 'firm';
	created_at: string;
}

/** The fields a create/edit form emits (uid absent = create). */
export interface RuleDraft {
	uid?: string;
	trigger_key: string;
	audience: string;
	label: string;
	title: string;
	body: string;
	link: string;
	default_in_app: boolean;
	default_email: boolean;
	mandatory: boolean;
	firm_editable: boolean;
	enabled: boolean;
	/** The chosen email template code; null/'notification' → the general wrapper. */
	email_code: string | null;
}

/**
 * One row of the event registry: an event this platform records, and what is attached to it.
 *
 * `GET /firm/event-registry` returns one of these per DECLARED event type — all 207, not only the
 * ones a notification can be authored against. `state` is what a reader needs first:
 *
 *  - `assignable` — a catalogue trigger covers it, so `trigger` is present and rules can be made.
 *  - `ready` — a subject SOURCE exists (`automation._SUBJECTS`) but no trigger uses it.
 *  - `declarable` — the event records a subject in its metadata, but no source is declared for it,
 *    so nothing can resolve a recipient. A declaration away, not new capture.
 *  - `actor_only` — it names no client, account or task. `actor_resolvable` is true throughout
 *    (the actor is on every action row), so an actor or role audience could still reach it.
 *
 * `unavailable_reason` is set for the latter two and says which. The row is shown either way: the
 * point of listing every event is that "this cannot notify anyone, and here is why" is an answer,
 * where a missing row is not.
 */
export interface RegistryEvent {
	event_type: string;
	/** The resource half of the event type (`document` for `document.sent`) — the grouping. */
	resource: string;
	state: 'assignable' | 'ready' | 'declarable' | 'actor_only';
	unavailable_reason: string | null;
	subject_kinds: string[];
	/** True on every event: `_resolve_actor` reads `actor_id` and needs no subject. */
	actor_resolvable?: boolean;
	/** The catalogue entry when one covers this event; null otherwise. */
	trigger: CatalogueTrigger | null;
	/** This firm's stored rules for that trigger. */
	rules: NotificationRule[];
	automation_count: number;
	email_count_30d: number;
}
