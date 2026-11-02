// The action-log filter↔query mapping, in ONE place. Pure: no SvelteKit, no `fetch`, no DOM — so
// both portals' loaders and their same-origin "Load more" proxies read it, and it is unit-tested
// without either.
//
// THE DRIFT THIS EXISTS FOR. The Events stream is mounted by four surfaces (admin `/events`, admin
// `/tenants/[uid]/activity`, platform `/activity`, platform `/clients/[id]/activity`), each with a loader
// AND a `more/+server.ts` proxy — eight copies of the same URL → filters → API-query mapping, and
// two of the rules in it are silent when they go wrong:
//
//   * `until` is EXCLUSIVE at the API. A date typed as "up to the 5th" must be sent as the END of
//     the 5th or the whole of that day is missing from the answer — an omission that reads as "the
//     firm did nothing that day", never as a bug.
//   * `proxied` is a THREE-way filter over a bool: absent means "all". Sending `proxied=false` for
//     the unfiltered case silently hides every proxied action, i.e. exactly the Graphene-support
//     activity a firm's own audit trail exists to disclose.
//   * `tags` is ONE comma-separated param (`tags=document,contact`), ANDed per kind at the API, and
//     it must be ABSENT when nothing is selected. The API answers `AND FALSE` for a kind it cannot
//     honour, so a stray blank element filters the page to zero rows — an empty answer that reads as
//     "this firm did nothing" rather than as a malformed request.
//
// None of them is visible in the output, so a copy that loses one keeps answering 200 with a wrong
// page. Hence one builder, one set of tests.

import type { ActionLogFilters } from './event-stream.svelte';

// Type-only import, erased at build — this module pulls in no component. `ActionLogFilters` and
// `EMPTY_ACTION_LOG_FILTERS` still live in `event-stream.svelte`'s `<script module>` block; moving
// them into a plain `.ts` module beside this one is a follow-up (that file is being edited
// elsewhere in this change).
export type { ActionLogFilters };

/**
 * The keyset page this query asks for, on top of the filters.
 *
 * `before` is the cursor: the API returns events with an id BELOW it, which is how "Load more"
 * walks back without a growing `limit` in the URL. It is `number | string` because it arrives as a
 * number from a row and as a string from a query param, and stringifying it here is cheaper than
 * making four call sites agree on which.
 */
export interface ActionLogPage {
	limit?: number;
	before?: number | string | null;
	/** The OTHER keyset end, for the live poll: events with an id ABOVE it, newest first — "what has
	 *  happened since the newest row I hold". Same `number | string` reason as `before`. */
	after?: number | string | null;
}

/** Set `key` only when `value` has content, so an empty filter is ABSENT rather than sent blank —
 *  `event_type=` is not "no event-type filter" at the API, it is a filter for the empty string. */
function setIf(query: URLSearchParams, key: string, value: string): void {
	const trimmed = value.trim();
	if (trimmed) query.set(key, trimmed);
}

/** A plain calendar date, the only shape the toolbar's date-range filter emits. */
const PLAIN_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** A list filter as ONE comma-separated param value, or '' when nothing is selected (the caller then
 *  omits the param entirely). Blanks are dropped, so a hand-edited `tags=document,,` carries one kind
 *  rather than a kind the API cannot honour — which it answers with an empty page. Serves both list
 *  filters: `tags` (ANDed at the API) and `event_type` (ORed) — the wire FORMAT is the same, only the
 *  API's reading of it differs. */
function listParam(values: readonly string[] | string | null | undefined): string {
	// A lone string is accepted as one element for the same reason `filterList` exists: `eventType` was
	// a single string before it was a list, and a caller still on that shape must have its filter
	// honoured rather than dropped (`String.prototype.map` does not exist, so the alternative is a throw
	// inside a loader — a 500 on the page instead of a filter).
	const list = Array.isArray(values) ? values : typeof values === 'string' ? [values] : [];
	return list
		.map((value) => String(value).trim())
		.filter(Boolean)
		.join(',');
}

/** The same reading back: one comma-separated param to a list, blanks dropped. */
function listValues(raw: string): string[] {
	return raw
		.split(',')
		.map((value) => value.trim())
		.filter(Boolean);
}

/**
 * The exclusive `until` bound for an inclusive end date: the last instant of the typed day.
 *
 * A value that is NOT a plain date is passed through untouched — it is already an instant (a
 * hand-edited URL, a future caller), and appending a time to it would build the nonsense
 * `2026-08-05T12:00:00ZT23:59:59.999Z` that the API can only answer 422 to. The eight call sites
 * this replaces appended unconditionally.
 */
export function actionLogUntilBound(until: string): string {
	const value = until.trim();
	return PLAIN_DATE.test(value) ? `${value}T23:59:59.999Z` : value;
}

/**
 * Read the Events filters out of a URL — the ONE reading of the wire names, shared by every loader
 * (which reads the page URL) and every "Load more" proxy (which reads the query the browser sent
 * it), so the two can never disagree about what a link means.
 *
 * Absent ⇒ '' (unfiltered); values are trimmed, so a link carrying `?actor=%20` filters by nothing
 * rather than by a space. `proxied` accepts only its two real values — anything else, including a
 * stale or hand-typed one, means "all" rather than an error, because a filter bar is not a place to
 * fail a page load. `tags` splits the one comma-separated param back into kinds, dropping blanks for
 * the same reason: `tags=` or `tags=document,` must mean the kinds that are there, never an unknown
 * kind the API can only answer with an empty page.
 */
export function actionLogFiltersFromSearchParams(params: URLSearchParams): ActionLogFilters {
	const read = (key: string) => params.get(key)?.trim() ?? '';
	const source = read('proxied');
	return {
		tenant: listValues(read('tenant')),
		eventType: listValues(read('event_type')),
		search: read('q'),
		actor: listValues(read('actor')),
		method: listValues(read('method')),
		since: read('since'),
		until: read('until'),
		proxied: source === 'proxied' || source === 'direct' ? source : '',
		tags: listValues(read('tags'))
	};
}

/**
 * The API query for a page of the action stream (`GET /admin/audit/actions`,
 * `GET /firm/audit/actions`) — filters plus the keyset page.
 *
 * Every empty filter is omitted. Two mappings are here rather than at the call sites precisely
 * because they are what drifts (see this module's header): `until` becomes the END of the typed day
 * because the API's bound is exclusive, and `proxied` maps its three states onto the API's bool as
 * true / false / ABSENT.
 *
 * `tenant` comes from the filters, which is right for the cross-firm surface. The surfaces that FIX
 * their scope (the admin per-firm slice, the platform client record) carry it outside the filter
 * object and `set()` it on the result — a fixed scope is not a filter a reader can clear.
 */
export function actionLogQuery(
	filters: ActionLogFilters,
	extra: ActionLogPage = {}
): URLSearchParams {
	const query = new URLSearchParams();
	// Page first, so the query string reads the way the hand-rolled proxies built it.
	if (extra.limit !== undefined) query.set('limit', String(extra.limit));
	if (extra.before !== undefined && extra.before !== null)
		setIf(query, 'before', String(extra.before));
	if (extra.after !== undefined && extra.after !== null) setIf(query, 'after', String(extra.after));

	setIf(query, 'tenant', listParam(filters.tenant));
	// The event-type PREFIXES as one comma-separated param, ORed at the API.
	setIf(query, 'event_type', listParam(filters.eventType));
	// The free-text search — a DIFFERENT filter from the event types, ANDed with them at the API.
	setIf(query, 'q', filters.search ?? '');
	setIf(query, 'actor', listParam(filters.actor));
	setIf(query, 'method', listParam(filters.method));
	setIf(query, 'since', filters.since);
	// `until` is EXCLUSIVE at the API — send the end of the day so the typed day is included.
	setIf(query, 'until', actionLogUntilBound(filters.until));
	// The tri-state → the API's bool. '' must leave the param ABSENT: `proxied=false` would hide
	// every action Graphene support took on the firm's behalf, which is the opposite of unfiltered.
	if (filters.proxied === 'proxied') query.set('proxied', 'true');
	else if (filters.proxied === 'direct') query.set('proxied', 'false');
	// The entity-kind tags, ONE comma-separated param — the fixed wire format. Set LAST so a query
	// with no tag selected is byte-identical to the pre-tags one (the pinned proxy output).
	const tags = listParam(filters.tags);
	if (tags) query.set('tags', tags);
	// The SURFACE. Only `events` is sent: `actions` is the api's default, so the unfiltered query
	// stays byte-identical to the pre-surface one (the pinned proxy output).
	return query;
}

/**
 * The same page request in the BROWSER's vocabulary — for the hop from a page to its own
 * same-origin `…/more` proxy, which is a different hop from the proxy's call to the API.
 *
 * WHY TWO BUILDERS, and why this is not duplication. The proxy re-reads whatever the browser sends
 * with `actionLogFiltersFromSearchParams` and then builds the API query with `actionLogQuery`. That
 * reader accepts only 'proxied' / 'direct' for the source filter and treats anything else as "no
 * filter" — so sending the API's own `proxied=true` over this hop arrives as UNFILTERED and the
 * Source filter is silently dropped. Nothing errors; the page just shows every source under a
 * control that says otherwise.
 *
 * Each builder therefore names the hop it serves: this one speaks the toolbar's words because the
 * next reader is the toolbar's reader, and `actionLogQuery` speaks the API's because the next reader
 * is the API. Four page mounts each restated `proxied` by hand to work around the mismatch; one
 * named function is the same fix in one place, and a unit test can execute it rather than a source
 * scan asserting the line is still there.
 *
 * `until` is left as the plain typed date on purpose: the proxy applies `actionLogUntilBound` itself,
 * and that expansion is idempotent, so either spelling survives — but the plain date is what a URL of
 * this vocabulary should carry.
 */
export function actionLogBrowserQuery(
	filters: ActionLogFilters,
	extra: ActionLogPage = {}
): URLSearchParams {
	const query = actionLogQuery(filters, extra);
	// Restate the two params whose spelling differs between the hops.
	query.delete('proxied');
	setIf(query, 'proxied', filters.proxied);
	if (filters.until.trim()) query.set('until', filters.until.trim());
	return query;
}
