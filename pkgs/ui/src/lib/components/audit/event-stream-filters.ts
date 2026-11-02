// The pure derivations behind the Events toolbars' filters — extracted so they can be unit-tested and
// shared (the actor options serve the action-stream EventStream, the compact EventFeed and the admin
// audit-log's own toolbar; the tag filter refines by the SAME entity kinds the detail pane shows as
// chips, so filter and chips can never disagree).
//
// TWO JOBS, and they are not the same job:
//
//   * THE OPTION LISTS feed the EventStream's dropdowns, whose filters are applied by the API. The
//     vocabulary therefore CANNOT come from the answer: choosing one actor returns only that actor's
//     rows, so a list re-derived from them offers only the types that actor produced — the value you
//     wanted to switch to is gone, and the only way back is to clear a filter whose effect you can no
//     longer see. So each list is the FULL vocabulary — the endpoint's own filter-independent `facets`
//     for event types and actors, the closed entity-kind set for tags — UNIONed with whatever the
//     loaded rows carry, because a row paged in from beyond the facet window is legitimately outside
//     it. A union can only ever grow a list, which is what makes it safe.
//   * THE MATCHERS are the FALLBACK. A mount that passes no `onFilterApply` narrows the loaded rows
//     with them instead of fetching, so an unwired surface filters something rather than nothing.
//     They mirror what the server-side filters do, so such a surface answers the same question over a
//     smaller set rather than a subtly different one: event type by PREFIX, actor/method/firm exact
//     (all ORed across their list), source over the presence of a proxied target, `since`/`until`
//     inclusive, tags ANDed. `matchesTags` is the one exception — noted on that function.

import { actionLogUntilBound } from './action-log-query.js';
import { describeActionEvent, entityKindLabel, eventCategory } from './describe.js';
import type { ActionCategory, ActionEntity } from './describe.js';
import type { ActionLogFilters } from './event-stream.svelte';

/** One `FilterSelect` entry: the stored value, the label a reader scans, and optionally the section
 *  it sits under (`FilterSelect` renders grouped entries as labelled sections in first-seen order,
 *  so a grouped list must be sorted by group — see `eventTypeFilterOptions`). */
export interface FilterOption {
	value: string;
	label: string;
	/** Secondary text beside the label — the humanised sentence for a raw-id option. */
	hint?: string;
	group?: string;
}

/** Just the actor fields the option list reads — structurally typed so both the action-stream
 *  `ActionEvent` and the DML `AuditEvent` (which has no `actor_sub`) qualify. */
export interface ActorLike {
	actor_name?: string | null;
	actor_email?: string | null;
	actor_sub?: string | null;
}

/**
 * A raw `action.*` type as one option: the type is the value and the HUMANISED sentence is the label
 * ("Created a client"), so the picker reads the way the rows do.
 *
 * THE one humanising path, deliberately private and used by both option builders below. A second
 * `describeActionEvent` call site is how a raw id (`client.created`) ends up rendered as
 * itself beside the sentences in the same list — which is exactly what happens when the facet
 * vocabulary is turned into options separately from the loaded rows'.
 */
function eventTypeOption(value: string): FilterOption {
	return { value, label: describeActionEvent(value) };
}

/** The glyph categories as SECTION HEADINGS. `flows` reads as "Journeys" because that is the word the
 *  product uses for a flow (the platform portal's own `/journeys`); the rest are the category name
 *  said in full. Total by construction — `eventCategory` returns one of these nine. */
const CATEGORY_LABELS: Record<ActionCategory, string> = {
	people: 'People',
	roles: 'Roles & permissions',
	documents: 'Documents & forms',
	money: 'Money',
	tasks: 'Tasks',
	flows: 'Journeys',
	screening: 'Compliance',
	security: 'Security',
	settings: 'Settings'
};

/** The namespace every action event carries (`app/audit_actions.py::EVENT_PREFIX`). Dropped from a display
 *  label, never from the value — see `eventTypeFilterOptions`. */
const ACTION_PREFIX = 'action.';

/** The section heading an event type belongs under — the SAME categories the row glyphs use, so a
 *  reader scanning by glyph and a reader scanning the dropdown are sorting by one vocabulary. */
export function eventCategoryLabel(eventType: string): string {
	return CATEGORY_LABELS[eventCategory(eventType)];
}

/**
 * The distinct EVENT TYPES present in the loaded rows, as flat `FilterSelect` options, sorted by
 * label; the "all" entry is FilterSelect's.
 *
 * The COMPACT feed's picker (`EventFeed`) — a handful of types over one already-complete list, no
 * search box, no sections. The EventStream's own picker is `eventTypeFilterOptions`.
 */
export function eventTypeSuggestions(events: { event_type?: string | null }[]): FilterOption[] {
	const seen = new Set<string>();
	for (const e of events) if (e.event_type) seen.add(e.event_type);
	return [...seen].map(eventTypeOption).sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * The EventStream's event-type vocabulary: the endpoint's `facets.event_types` (the whole vocabulary
 * for the caller's scope, independent of the filters in force) UNIONed with the types the loaded rows
 * carry, GROUPED by category.
 *
 * THE LABEL IS THE RAW `event_type`, AND NOTHING ELSE. A filter picks a value out of a closed machine
 * vocabulary: the id is what the detail panel shows under "Type", what a support ticket quotes, and
 * what distinguishes two operations a sentence cannot ("Created an email template" is a true answer
 * for both the ops route and the firm one, so a prose list has two identical entries).
 *
 * The humanised sentence is deliberately NOT carried as a second column. It was, and two things went
 * wrong: the identifier — the thing actually being picked — lost the width to the prose and truncated
 * to `ac…`, and the row then read as two competing labels for one option. Prose belongs in the ROWS,
 * where a reader is asking what happened; here they are choosing a value.
 *
 * The `action.` namespace is dropped from the LABEL and kept in the VALUE. Every event type in the
 * stream carries it, so as a display prefix it distinguishes nothing and repeats itself down the whole
 * list, taking the width that tells one option from another. The value keeps it because it is what the
 * API filters on — and because `MultiSelect` searches the value as well as the label, pasting a full
 * `client.created` out of the detail panel or a support ticket still finds its option.
 *
 * The union is not belt-and-braces: the facets describe the most recent window of the stream, so a
 * type reached by paging past that window is real and pickable and would otherwise be absent from its
 * own list. No facets at all (an unwired mount, or an API that predates them) degrades to exactly the
 * loaded rows' types plus their sections.
 *
 * SORTED BY SECTION, THEN VALUE, and it has to be: the picker walks the list and opens a new section
 * whenever the group changes, so two non-adjacent entries of one category would render as two sections
 * with the same heading. Sorting is also what makes the grouping stable as rows page in. Within a
 * section the sort is by the raw id, which clusters an operation family together (`documents-create-…`
 * beside `documents-classify-…`) where sorting by sentence would scatter it by whichever verb it
 * happens to start with.
 */
export function eventTypeFilterOptions(
	events: { event_type?: string | null }[],
	facetTypes: readonly string[] = []
): FilterOption[] {
	const seen = new Set<string>();
	for (const type of facetTypes) if (type) seen.add(type);
	for (const e of events) if (e.event_type) seen.add(e.event_type);
	return [...seen]
		.map((value) => ({
			value,
			label: value.startsWith(ACTION_PREFIX) ? value.slice(ACTION_PREFIX.length) : value,
			group: eventCategoryLabel(value)
		}))
		.sort((a, b) => a.group.localeCompare(b.group) || a.value.localeCompare(b.value));
}

/** The display name an event's actor is shown and filtered by — name, else email, else sub, else ''
 *  (fully anonymous). The single precedence `actorSuggestions` and `matchesActor` both read — and the
 *  API's facet query mirrors it in SQL (`COALESCE(u.name, u.email, a.actor_sub)`), so a chosen option
 *  always matches the rows it was offered for. */
export function actorName(e: ActorLike): string {
	return e.actor_name ?? e.actor_email ?? e.actor_sub ?? '';
}

/**
 * The actors to offer, as `FilterSelect` options — the endpoint's `facets.actors` (the whole
 * vocabulary for the caller's scope) UNIONed with the actors the loaded rows name, sorted so the
 * dropdown scans; FilterSelect prepends the "all" entry. The display name is both value and label.
 *
 * A row with no name/email/sub at all is skipped (`actorName` returns ''), which is the same skip the
 * facet query makes. `facetActors` defaults to none, so the EventFeed and the admin audit-log — which
 * have no facets and filter their own loaded rows — are unaffected.
 */
export function actorSuggestions(
	events: ActorLike[],
	facetActors: readonly string[] = []
): FilterOption[] {
	const seen = new Set<string>();
	for (const actor of facetActors) if (actor) seen.add(actor);
	for (const e of events) {
		const name = actorName(e);
		if (name) seen.add(name);
	}
	return [...seen]
		.map((value) => ({ value, label: value }))
		.sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * A list filter read DEFENSIVELY: an array as given, a lone string as one element, anything else as
 * empty.
 *
 * `ActionLogFilters` crosses a package boundary — four app mounts and eight loaders/proxies build it,
 * and `eventType` was a single string before it was a list of prefixes. A component that assumes the
 * new shape and meets the old one does not degrade, it THROWS, and a thrown component is a page with
 * no working controls at all (Svelte stops hydrating, so every OTHER filter dies with it) rather than
 * one filter behaving oddly. That failure is invisible in a type check, because the mismatch arrives
 * as data from a caller that compiled against a different version of the type.
 *
 * So the shape is normalised where it is read, and a stale caller gets the filter it asked for.
 */
export function filterList(value: readonly string[] | string | null | undefined): string[] {
	if (Array.isArray(value)) return value.map((v) => String(v).trim()).filter(Boolean);
	if (typeof value === 'string') return value.trim() ? [value.trim()] : [];
	return [];
}

/** Does the event's type match the selected one EXACTLY? '' = no filter. What the EventFeed's picker
 *  needs: its options are single loaded types and it carries no text box, so an exact match is the
 *  whole question. Contrast `matchesEventTypePrefix`, which the EventStream uses. */
export function matchesEventType(event: { event_type?: string | null }, type: string): boolean {
	return type === '' || event.event_type === type;
}

/**
 * Does the event's type fall UNDER ANY of the selected prefixes? Empty = no filter.
 *
 * A PREFIX match, because that is both what the API's `event_type` filter does
 * (`a.event_type LIKE 'action.firm-%'`) and what the EventStream toolbar's free-text box promises
 * ("Search event types (action.firm-…)"): a family narrows to a family. One matcher serves the box and
 * the picker, since an exact type picked from the list is its own prefix — which is why the exact
 * `matchesEventType` is NOT what this surface uses: typing "action.firm-" there matches nothing, and a
 * search box that silently empties the table is worse than no search box.
 *
 * ORed across the list, matching the API. Contrast the tags, which AND: two selected event types are
 * two alternatives (one row cannot be both), where two selected entity kinds are two things one row
 * must touch. Getting that the wrong way round yields an always-empty page, which reads as "nobody
 * did this" rather than as a filter bug.
 */
export function matchesEventTypePrefix(
	event: { event_type?: string | null },
	types: readonly string[]
): boolean {
	const prefixes = filterList(types);
	if (prefixes.length === 0) return true;
	const type = event.event_type ?? '';
	return prefixes.some((prefix) => type.startsWith(prefix));
}

/**
 * Does the row match the free-text search? Empty = no filter.
 *
 * The CLIENT-SIDE half, for a mount with no fetcher. It searches what the row already carries — the
 * actor's display name, the resolved ENTITY LABELS (which is where a client's or document's name is,
 * once the API has resolved it), the event type and the route.
 *
 * It is deliberately NARROWER than the API's `q`, and cannot be otherwise: the API searches
 * `crm.contacts` directly, so it finds a client named on a row whose page has not been loaded, and it
 * matches a name the row references but whose label the read side did not resolve. A mount without a
 * fetcher only has the rows in front of it. That gap is why every real Events surface passes
 * `onFilterApply` — this exists so a fallback mount is consistent, not so it is equivalent.
 */
export function matchesSearch(
	event: FilterableEvent & {
		route?: string | null;
		entities?: { label?: string | null }[] | null;
	},
	search: string
): boolean {
	const needle = search.trim().toLowerCase();
	if (!needle) return true;
	const haystack = [
		actorName(event),
		event.event_type ?? '',
		event.route ?? '',
		...(event.entities ?? []).map((e) => e.label ?? '')
	];
	return haystack.some((value) => value.toLowerCase().includes(needle));
}

/** Matches one of the selected display names, ORed (empty = no filter). */
export function matchesActor(event: ActorLike, names: readonly string[]): boolean {
	const list = filterList(names);
	if (list.length === 0) return true;
	return list.includes(actorName(event));
}

/** Matches ONE OF the selected verbs, exactly (empty = no filter), ORed across the list. */
export function matchesMethod(
	event: { method?: string | null },
	methods: readonly string[]
): boolean {
	const list = filterList(methods);
	if (list.length === 0) return true;
	return list.includes(event.method ?? '');
}

/**
 * The SOURCE tri-state: '' = all, 'proxied' = only actions taken on a firm's behalf, 'direct' = only
 * actions with no proxied target.
 *
 * Keyed on `target_tenant_id` because that is the column the API tests
 * (`(a.target_tenant_id IS NOT NULL) = :proxied`). Any value that is neither 'proxied' nor 'direct'
 * means ALL — the same tolerance `actionLogFiltersFromSearchParams` has, and for the same reason: a
 * filter bar is not a place to fail, and 'direct' as the fallback would silently hide every action
 * Graphene support took on a firm's behalf, which is the opposite of unfiltered.
 */
export function matchesSource(
	event: { target_tenant_id?: number | null },
	source: string
): boolean {
	if (source !== 'proxied' && source !== 'direct') return true;
	const isProxied = event.target_tenant_id != null;
	return source === 'proxied' ? isProxied : !isProxied;
}

/** The instant a bound names, or null for "no bound" — a blank or unreadable date filters nothing
 *  rather than emptying the table (the filter-bar tolerance again). */
function boundInstant(value: string): number | null {
	const trimmed = value.trim();
	if (!trimmed) return null;
	const at = Date.parse(trimmed);
	return Number.isNaN(at) ? null : at;
}

/**
 * Is the event inside the typed date range? Either bound blank = open on that side.
 *
 * `since` is INCLUSIVE and `until` is inclusive of the WHOLE typed day — the rule that lives in the
 * query builder, reused from it (`actionLogUntilBound`) rather than spelled a second time, because it
 * is the one that fails silently: "up to the 5th" read as midnight on the 5th drops that whole day,
 * and a missing day reads as "the firm did nothing" rather than as a bug. A plain `yyyy-mm-dd` is read
 * as UTC by `Date.parse`, which is the same reading the API makes of the same string.
 *
 * A row whose `created_at` cannot be read matches NOTHING once a bound is set: it cannot be shown to
 * be inside the range, and the tag filter sets the precedent (an event with no entities matches no
 * tag).
 */
export function matchesDateRange(
	event: { created_at?: string | null },
	since: string,
	until: string
): boolean {
	const from = boundInstant(since);
	const to = boundInstant(actionLogUntilBound(until));
	if (from === null && to === null) return true;
	const at = Date.parse(event.created_at ?? '');
	if (Number.isNaN(at)) return false;
	if (from !== null && at < from) return false;
	if (to !== null && at > to) return false;
	return true;
}

/** Just the two firm sides of an event — the acting firm and the proxied target. */
export interface TenantScopedLike {
	tenant_uid?: string | null;
	target_tenant_uid?: string | null;
}

/** Matches one of the selected firms, ORed (empty = no filter); either side counts per firm. */
export function matchesTenant(event: TenantScopedLike, uids: readonly string[]): boolean {
	const list = filterList(uids);
	if (list.length === 0) return true;
	return list.some((uid) => event.tenant_uid === uid || event.target_tenant_uid === uid);
}

/** Just the entities an event references — the DOCUMENT/CLIENT/… chips the detail pane resolves. */
export interface TaggedLike {
	entities?: ActionEntity[] | null;
}

/**
 * The entity kinds the tag filter can be asked for — a CLOSED vocabulary, mirroring the kinds the API
 * can honour (`audit_stream.TAG_FIELDS` ∪ `TAG_UID_TAGS`, which its own tests hold to the read side's
 * chip resolution). Kept as a literal because that is what makes the picker offer a kind BEFORE an
 * event carrying it happens to be on screen: with the filter applied by the API, "you may not ask for
 * documents unless a document event is already loaded" is the option-shrinking defect in another
 * costume.
 *
 * A kind the API cannot honour answers with an empty page, so this list must not run ahead of it.
 */
export const ENTITY_TAG_KINDS: readonly string[] = [
	'account',
	'contact',
	'conversation',
	'document',
	'flow',
	'form',
	'instruction',
	'model',
	'task',
	'template',
	'tenant',
	'transaction',
	'user'
];

/** The tag options: the closed vocabulary above, UNIONed with any kind the loaded rows carry (the
 *  read side may grow a kind first — its chips and this filter move together), humanised via
 *  `entityKindLabel` so the picker says the same words as the detail pane's chips, sorted by label. */
export function entityTagSuggestions(events: TaggedLike[]): FilterOption[] {
	const kinds = new Set<string>(ENTITY_TAG_KINDS);
	for (const e of events) for (const ent of e.entities ?? []) if (ent.kind) kinds.add(ent.kind);
	return [...kinds]
		.map((kind) => ({ value: kind, label: entityKindLabel(kind) }))
		.sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * Does the event reference an entity of EVERY selected kind? An empty selection is "no filter", so
 * the tag picker only narrows. An event with no entities matches nothing once a tag is chosen.
 *
 * AND, not OR — deliberately, and it was OR until Aug 2026. Picking `document` + `contact` asks for
 * the events that touch BOTH: "what did we do to this client's documents". Under OR it answered
 * "every document event, plus every client event", which is a WIDER set than either tag alone — so
 * adding a second tag made the list grow, and the control read as broken. A multi-select whose extra
 * choices narrow is the behaviour every other filter in the bar has. The API's `_tags_clause` ANDs the
 * kinds for the same reason; do not change one without the other.
 *
 * The one asymmetry with the API, stated because it is the reason this is the FALLBACK path and not a
 * second opinion on a fetched answer: this reads the RESOLVED `entities`, where the API tests for the
 * captured id KEYS. A row can carry a key the read side could not resolve into a chip, so re-running
 * this over a server-filtered page could hide rows the API rightly returned — which is why the
 * component narrows client-side only when there is no fetcher at all.
 */
export function matchesTags(event: TaggedLike, kinds: readonly string[]): boolean {
	if (filterList(kinds).length === 0) return true;
	const present = new Set((event.entities ?? []).map((ent) => ent.kind));
	return kinds.every((kind) => present.has(kind));
}

/** Everything the EventStream toolbar reads off one row — structurally typed, so an `ActionEvent`
 *  qualifies and a test can hand in only the fields its case is about. */
export interface FilterableEvent extends ActorLike, TaggedLike, TenantScopedLike {
	event_type?: string | null;
	method?: string | null;
	created_at?: string | null;
	target_tenant_id?: number | null;
}

/**
 * Does the row survive EVERY active filter? The whole of the EventStream's FALLBACK narrowing (a
 * mount with no `onFilterApply`), in one pure function so that "several filters at once" is testable
 * at all — this package's vitest runs in a plain node environment (no jsdom anywhere in the tree, see
 * ui/theme-toggle/scheme.test.ts), so the component's own derivation cannot be driven from a test.
 *
 * The tags are read off `filters.tags` rather than passed beside them: a tag has a wire name now
 * (`tags=document,contact`), so it is a filter like the rest and a second channel for it would be a
 * second place to forget it.
 */
export function matchesAllFilters(event: FilterableEvent, filters: ActionLogFilters): boolean {
	return (
		matchesTenant(event, filters.tenant) &&
		matchesEventTypePrefix(event, filters.eventType) &&
		matchesActor(event, filters.actor) &&
		matchesMethod(event, filters.method) &&
		matchesSource(event, filters.proxied) &&
		matchesDateRange(event, filters.since, filters.until) &&
		matchesTags(event, filterList(filters.tags)) &&
		matchesSearch(event, filters.search ?? '')
	);
}
