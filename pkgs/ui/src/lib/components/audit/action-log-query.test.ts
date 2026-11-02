// The action-log filter↔query URL mapping: `until` as an exclusive end-of-day bound, `proxied` as
// an absent-when-unfiltered tri-state, and every multi-valued filter as one comma-separated param.
import { describe, expect, it } from 'vitest';
import {
	actionLogBrowserQuery,
	actionLogFiltersFromSearchParams,
	actionLogQuery,
	actionLogUntilBound,
	type ActionLogFilters
} from './action-log-query.js';

/** The unfiltered baseline — the same shape as `EMPTY_ACTION_LOG_FILTERS`, restated here rather than
 *  imported from `event-stream.svelte` so these tests need no component compilation. (Once those
 *  declarations move to a plain `.ts` module, import the real constant instead.) */
const EMPTY: ActionLogFilters = {
	tenant: [],
	eventType: [],
	search: '',
	actor: [],
	method: [],
	since: '',
	until: '',
	proxied: '',
	tags: []
};

const FULL: ActionLogFilters = {
	tenant: ['401d0a11'],
	eventType: ['action.firm-'],
	search: 'margaret',
	actor: ['ada@firm.test'],
	method: ['POST'],
	since: '2026-08-01',
	until: '2026-08-05',
	proxied: 'proxied',
	tags: ['document', 'contact']
};

describe('actionLogFiltersFromSearchParams', () => {
	it('reads every wire name the loaders and proxies use', () => {
		const params = new URLSearchParams({
			tenant: '401d0a11',
			event_type: 'action.firm-',
			actor: 'ada@firm.test',
			method: 'POST',
			since: '2026-08-01',
			until: '2026-08-05',
			proxied: 'proxied',
			tags: 'document,contact',
			q: 'margaret'
		});
		expect(actionLogFiltersFromSearchParams(params)).toEqual(FULL);
	});

	it('splits the ONE tags param on commas, dropping blanks', () => {
		const read = (tags: string) =>
			actionLogFiltersFromSearchParams(new URLSearchParams({ tags })).tags;
		expect(read('document,contact')).toEqual(['document', 'contact']);
		expect(read(' document , contact ')).toEqual(['document', 'contact']);
		// A blank element must never become a kind: the API answers `AND FALSE` for a kind it cannot
		// honour, so `tags=document,` would empty the page rather than filter by documents.
		expect(read('document,,')).toEqual(['document']);
		expect(read('')).toEqual([]);
		expect(read(' , ')).toEqual([]);
		expect(actionLogFiltersFromSearchParams(new URLSearchParams()).tags).toEqual([]);
	});

	it('treats an absent param as unfiltered', () => {
		expect(actionLogFiltersFromSearchParams(new URLSearchParams())).toEqual(EMPTY);
	});

	it('trims, so a link carrying whitespace filters by nothing', () => {
		const params = new URLSearchParams({ actor: '  ada@firm.test  ', event_type: ' \t ' });
		const filters = actionLogFiltersFromSearchParams(params);
		expect(filters.actor).toEqual(['ada@firm.test']);
		expect(filters.eventType).toEqual([]);
	});

	it('accepts only the two real source values — anything else means "all", never an error', () => {
		expect(
			actionLogFiltersFromSearchParams(new URLSearchParams({ proxied: 'direct' })).proxied
		).toBe('direct');
		expect(actionLogFiltersFromSearchParams(new URLSearchParams({ proxied: 'true' })).proxied).toBe(
			''
		);
		expect(actionLogFiltersFromSearchParams(new URLSearchParams({ proxied: '' })).proxied).toBe('');
		expect(
			actionLogFiltersFromSearchParams(new URLSearchParams({ proxied: 'PROXIED' })).proxied
		).toBe('');
	});
});

describe('actionLogQuery', () => {
	it('maps each filter to its wire name', () => {
		const q = actionLogQuery(FULL);
		expect(q.get('tenant')).toBe('401d0a11');
		expect(q.get('event_type')).toBe('action.firm-');
		expect(q.get('actor')).toBe('ada@firm.test');
		expect(q.get('method')).toBe('POST');
		expect(q.get('since')).toBe('2026-08-01');
	});

	it('sends the END of the typed day for `until`, because the API bound is exclusive', () => {
		expect(actionLogQuery({ ...EMPTY, until: '2026-08-05' }).get('until')).toBe(
			'2026-08-05T23:59:59.999Z'
		);
		// `since` is inclusive at the API and must stay a bare date.
		expect(actionLogQuery({ ...EMPTY, since: '2026-08-01' }).get('since')).toBe('2026-08-01');
	});

	it('passes an `until` that is already an instant through untouched', () => {
		// Appending a time would build `…T00:00:00.000ZT23:59:59.999Z`, which the API can only 422.
		const instant = '2026-08-06T00:00:00.000Z';
		expect(actionLogUntilBound(instant)).toBe(instant);
		expect(actionLogQuery({ ...EMPTY, until: instant }).get('until')).toBe(instant);
	});

	it('emits the tags as ONE comma-separated param, omitted entirely when none are selected', () => {
		// The fixed wire format: `tags=document,contact`, in the order they were selected.
		expect(actionLogQuery({ ...EMPTY, tags: ['document', 'contact'] }).get('tags')).toBe(
			'document,contact'
		);
		expect(actionLogQuery({ ...EMPTY, tags: ['document'] }).get('tags')).toBe('document');
		// Empty ⇒ ABSENT, never `tags=`: the API cannot honour a blank kind and answers with no rows.
		expect(actionLogQuery({ ...EMPTY, tags: [] }).has('tags')).toBe(false);
		expect(actionLogQuery({ ...EMPTY, tags: ['  ', ''] }).has('tags')).toBe(false);
		expect(actionLogQuery({ ...EMPTY, tags: [' document ', ''] }).get('tags')).toBe('document');
	});

	it('maps the source tri-state onto the API bool, leaving it ABSENT when unfiltered', () => {
		expect(actionLogQuery({ ...EMPTY, proxied: 'proxied' }).get('proxied')).toBe('true');
		expect(actionLogQuery({ ...EMPTY, proxied: 'direct' }).get('proxied')).toBe('false');
		// The one that matters: unfiltered must not send `proxied=false`.
		expect(actionLogQuery({ ...EMPTY, proxied: '' }).has('proxied')).toBe(false);
	});

	it('omits every empty filter — never `event_type=`', () => {
		expect(actionLogQuery(EMPTY).toString()).toBe('');
		// `eventType` is multi-valued (ORed at the API), so a blank one is a blank ENTRY.
		const whitespaced = actionLogQuery({ ...EMPTY, actor: ['   '], eventType: ['  '] });
		expect(whitespaced.toString()).toBe('');
	});

	it('carries only the page when the filters are empty', () => {
		expect(actionLogQuery(EMPTY, { limit: 50 }).toString()).toBe('limit=50');
		expect(actionLogQuery(EMPTY, { limit: 50, before: 900 }).toString()).toBe(
			'limit=50&before=900'
		);
		// A cursor read off a query param arrives as a string; both spellings must work.
		expect(actionLogQuery(EMPTY, { before: '900' }).get('before')).toBe('900');
	});

	it('omits an absent or blank cursor rather than sending `before=`', () => {
		expect(actionLogQuery(EMPTY, {}).has('before')).toBe(false);
		expect(actionLogQuery(EMPTY, { before: null }).has('before')).toBe(false);
		expect(actionLogQuery(EMPTY, { before: '' }).has('before')).toBe(false);
	});

	it('builds the query the hand-rolled proxies built, in the same order', () => {
		// Byte-for-byte the admin `/events/more` proxy's output (the reference copy) PLUS the two params
		// added since — `q` (the free-text search, in filter order) and `tags` (appended LAST, which is
		// why a query with no tag selected is otherwise that exact string). The point of the pin is that
		// no EXISTING param moves or changes shape when one is added.
		expect(actionLogQuery({ ...FULL, tags: [] }, { limit: 50, before: 1234 }).toString()).toBe(
			'limit=50&before=1234&tenant=401d0a11&event_type=action.firm-&q=margaret&actor=ada%40firm.test&method=POST&since=2026-08-01&until=2026-08-05T23%3A59%3A59.999Z&proxied=true'
		);
		// With tags, they ride on the end of the same query (the comma is percent-encoded by
		// URLSearchParams, which is the same value on the wire).
		expect(actionLogQuery(FULL, { limit: 50, before: 1234 }).toString()).toBe(
			'limit=50&before=1234&tenant=401d0a11&event_type=action.firm-&q=margaret&actor=ada%40firm.test&method=POST&since=2026-08-01&until=2026-08-05T23%3A59%3A59.999Z&proxied=true&tags=document%2Ccontact'
		);
	});
});

describe('round trip', () => {
	it('URL → filters → query preserves every filter', () => {
		const url = new URLSearchParams({
			tenant: '401d0a11',
			event_type: 'action.firm-',
			actor: 'ada@firm.test',
			method: 'POST',
			since: '2026-08-01',
			until: '2026-08-05',
			proxied: 'direct',
			tags: 'document,contact'
		});
		const q = actionLogQuery(actionLogFiltersFromSearchParams(url));
		expect(q.get('tenant')).toBe('401d0a11');
		expect(q.get('event_type')).toBe('action.firm-');
		expect(q.get('actor')).toBe('ada@firm.test');
		expect(q.get('method')).toBe('POST');
		expect(q.get('since')).toBe('2026-08-01');
		expect(q.get('until')).toBe('2026-08-05T23:59:59.999Z');
		expect(q.get('proxied')).toBe('false');
		// The tag round trip is the one that has to be symmetric: the proxy re-reads its own query with
		// `actionLogFiltersFromSearchParams` before rebuilding the API's, so a format that did not
		// survive the hop would drop the filter and answer 200 with an unfiltered page.
		expect(q.get('tags')).toBe('document,contact');
	});

	it('reads the ONE event_type param back as its list of prefixes', () => {
		const read = (event_type: string) =>
			actionLogFiltersFromSearchParams(new URLSearchParams({ event_type })).eventType;
		expect(read('client.created,task.created')).toEqual(['client.created', 'task.created']);
		// Same blank-dropping rule as the tags, for the opposite reason: the API prefix-matches, so an
		// empty element would become `LIKE '%'` and quietly WIDEN the filter to the whole stream.
		expect(read('action.firm-,,')).toEqual(['action.firm-']);
		expect(read(' , ')).toEqual([]);
		expect(read('')).toEqual([]);
	});

	it('sends the free-text search as `q`, absent when empty', () => {
		// A DIFFERENT filter from the event types — the box searches the actor, the client's name, the
		// document's name, the type and the route, and ANDs with the picker rather than fighting it.
		expect(actionLogQuery({ ...EMPTY, search: 'margaret' }).get('q')).toBe('margaret');
		expect(actionLogQuery({ ...EMPTY, search: '   ' }).has('q')).toBe(false);
		expect(actionLogQuery(EMPTY).has('q')).toBe(false);
	});

	it('sends several event types as ONE comma-separated param, ORed at the API', () => {
		const q = actionLogQuery({ ...EMPTY, eventType: ['action.a', 'action.b'] });
		expect(q.get('event_type')).toBe('action.a,action.b');
		// Absent when nothing is picked — never `event_type=`, which is a filter for the empty prefix.
		expect(actionLogQuery(EMPTY).has('event_type')).toBe(false);
	});

	it('query → filters is the identity on everything but the two mapped params', () => {
		// A page's own query, read back the way a proxy reads it. `until` and `proxied` are
		// deliberately NOT symmetric: the wire values the API takes are not the ones a reader picked.
		const filters = actionLogFiltersFromSearchParams(
			new URLSearchParams(actionLogQuery(FULL).toString())
		);
		expect(filters).toEqual({
			...FULL,
			until: '2026-08-05T23:59:59.999Z',
			proxied: '' // 'true' is not one of the two reader-facing values
		});
	});
});

describe('actionLogBrowserQuery', () => {
	// The page → its own `…/more` proxy hop. The proxy re-reads this query with
	// `actionLogFiltersFromSearchParams`, which accepts only 'proxied' / 'direct' — so the API's own
	// `proxied=true` arrives as UNFILTERED and the Source filter is silently dropped. These execute
	// the round trip end to end rather than asserting a line still exists in four page components.
	const overTheHop = (filters: ActionLogFilters, extra = {}) =>
		actionLogQuery(
			actionLogFiltersFromSearchParams(new URLSearchParams(actionLogBrowserQuery(filters, extra))),
			extra
		);

	it('speaks the toolbar vocabulary, not the API bool', () => {
		expect(actionLogBrowserQuery({ ...EMPTY, proxied: 'proxied' }).get('proxied')).toBe('proxied');
		expect(actionLogBrowserQuery({ ...EMPTY, proxied: 'direct' }).get('proxied')).toBe('direct');
	});

	it('omits the source param when unfiltered — never sends the hiding value', () => {
		expect(actionLogBrowserQuery(EMPTY).has('proxied')).toBe(false);
	});

	it('survives the hop: each source state reaches the API as its own bool', () => {
		expect(overTheHop({ ...EMPTY, proxied: 'proxied' }).get('proxied')).toBe('true');
		expect(overTheHop({ ...EMPTY, proxied: 'direct' }).get('proxied')).toBe('false');
		// Absent, NOT false: `proxied=false` hides every action Graphene support took for the firm.
		expect(overTheHop(EMPTY).has('proxied')).toBe(false);
	});

	it('carries the typed date, and the bound still lands at the API', () => {
		// The browser hop carries the plain day (what a URL of this vocabulary should hold); the proxy
		// applies the exclusive-bound rule, so the typed day is still included in the answer.
		expect(actionLogBrowserQuery(FULL).get('until')).toBe('2026-08-05');
		expect(overTheHop(FULL).get('until')).toBe('2026-08-05T23:59:59.999Z');
	});

	it('carries every other filter and the cursor unchanged over the hop', () => {
		const query = overTheHop(FULL, { before: 42 });
		expect(query.get('before')).toBe('42');
		expect(query.get('tenant')).toBe('401d0a11');
		expect(query.get('event_type')).toBe('action.firm-');
		expect(query.get('actor')).toBe('ada@firm.test');
		expect(query.get('method')).toBe('POST');
		expect(query.get('since')).toBe('2026-08-01');
		expect(query.get('tags')).toBe('document,contact');
	});
});
