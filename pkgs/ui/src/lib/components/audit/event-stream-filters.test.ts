/**
 * The Events toolbars' filter derivations — the OPTION LISTS the dropdowns offer and the MATCHERS the
 * no-fetcher fallback narrows with.
 *
 * Pins: each list offers the WHOLE vocabulary for the scope (the endpoint's facets, or the closed
 * entity-kind set, UNIONed with whatever the loaded rows carry) as a `FilterSelect` `{value,label}`
 * with FilterSelect's own "all" entry left to it; the event-type list is humanised through ONE path
 * and grouped in a shape FilterSelect can section; and each matcher answers the same question its
 * server-side counterpart does, including the two that fail silently when they drift (an `until` that
 * drops the whole typed day, and a source tri-state whose fallback hides every proxied action).
 *
 * REPOINTED, not deleted: the "filtering narrows the ROWS, never the CHOICES" suite. It used to prove
 * that with the filters applied client-side the option lists could not shrink; the filters are applied
 * by the API again, so the same guarantee now comes from the facets being filter-independent — the
 * suite asserts that instead.
 */
import { describe, expect, it } from 'vitest';
import { EMPTY_ACTION_LOG_FILTERS, type ActionLogFilters } from './event-stream.svelte';
import {
	ENTITY_TAG_KINDS,
	actorName,
	actorSuggestions,
	entityTagSuggestions,
	eventCategoryLabel,
	eventTypeFilterOptions,
	eventTypeSuggestions,
	matchesActor,
	matchesAllFilters,
	matchesDateRange,
	matchesEventType,
	matchesEventTypePrefix,
	matchesMethod,
	matchesSource,
	matchesTags,
	matchesTenant
} from './event-stream-filters.js';

/** One filter object with only the fields a case is about set. */
const filters = (over: Partial<ActionLogFilters> = {}): ActionLogFilters => ({
	...EMPTY_ACTION_LOG_FILTERS,
	...over
});

describe('eventTypeSuggestions', () => {
	it('lists distinct event types with humanised labels, sorted by label, raw type as value', () => {
		const opts = eventTypeSuggestions([
			{ event_type: 'client.created' },
			{ event_type: 'authentication.succeeded' },
			{ event_type: 'client.created' }, // dup collapses
			{ event_type: '' }, // skipped
			{ event_type: null } // skipped
		]);
		// Sorted by LABEL: "Created a client" (C) before "Signed in" (S).
		expect(opts).toEqual([
			{ value: 'client.created', label: 'Created a client' },
			{ value: 'authentication.succeeded', label: 'Signed in' }
		]);
	});

	it('never labels a type with its raw id — one humanising path', () => {
		const opts = eventTypeSuggestions([{ event_type: 'document.sent' }]);
		expect(opts).toEqual([{ value: 'document.sent', label: 'Sent a document' }]);
	});

	it('stays FLAT — the compact feed’s picker has no sections', () => {
		// The EventStream's picker is grouped (`eventTypeFilterOptions`); this one serves the EventFeed,
		// a handful of types over one already-complete list.
		expect(
			eventTypeSuggestions([{ event_type: 'authentication.succeeded' }])[0]
		).not.toHaveProperty('group');
	});
});

describe('eventTypeFilterOptions — the EventStream’s whole vocabulary, grouped', () => {
	it('offers the FACET types, not just the loaded ones, as their raw ids', () => {
		// THE POINT OF THE FACETS. The filters are applied by the API, so a list derived from the loaded
		// rows can only offer what the current answer contains — "not just the 50 loaded ones" is the
		// product requirement this satisfies. A facet-only entry must read as a sentence like the rest;
		// a second humanising path is how one of them renders as `aml.screen_ran` instead.
		const opts = eventTypeFilterOptions(
			[{ event_type: 'client.created' }],
			['aml.screen_ran', 'document.sent']
		);
		expect(opts.map((o) => o.value)).toContain('aml.screen_ran');
		// THE RAW `event_type` IS THE LABEL, and the ONLY thing an option carries. A filter picks a value
		// out of a closed machine vocabulary — the id is what the detail panel shows under "Type" and
		// what distinguishes two operations a sentence cannot. No humanised second column: it took the
		// width from the identifier being picked and read as two competing labels for one option.
		// The VALUE keeps the `action.` namespace (it is what the API filters on, and what a reader may
		// paste out of the detail panel — MultiSelect searches the value too); the LABEL drops it,
		// because every option carries it and it therefore distinguishes nothing.
		const screen = opts.find((o) => o.value === 'aml.screen_ran');
		expect(screen?.label).toBe('aml.screen_ran');
		expect(screen?.hint).toBeUndefined();
		const sent = opts.find((o) => o.value === 'document.sent');
		expect(sent?.label).toBe('document.sent');
		expect(sent?.hint).toBeUndefined();
		// …and the loaded row's own type is still there: a type paged in from behind the facet window is
		// real, so the two sources are UNIONed rather than one replacing the other.
		expect(opts.map((o) => o.value)).toContain('client.created');
	});

	it('groups by the row-glyph category and sorts by section, then label', () => {
		// Sorting by the heading is not cosmetic: the picker opens a new section whenever the group
		// changes as it walks the list, so two non-adjacent entries of one category would render as two
		// sections with the same heading.
		const opts = eventTypeFilterOptions(
			[],
			[
				'client.created',
				'document.sent',
				'authentication.succeeded',
				'aml.screen_ran',
				'role.permissions_updated'
			]
		);
		expect(opts.map((o) => o.group)).toEqual([
			'Compliance',
			'Documents & forms',
			'People',
			'Roles & permissions',
			'Security'
		]);
		// Contiguous: every option of a section sits with its section.
		const groups = opts.map((o) => o.group);
		expect(groups).toEqual([...groups].sort((a, b) => (a ?? '').localeCompare(b ?? '')));
	});

	it('sorts by the raw id inside a section, which clusters an operation family together', () => {
		// By VALUE, not by sentence: `documents-classify-…` sits beside `documents-create-…` where
		// sorting by prose would scatter one family across the section by whichever verb it starts with.
		const opts = eventTypeFilterOptions([], ['client.updated', 'client.created']);
		expect(opts.map((o) => o.label)).toEqual(['client.created', 'client.updated']);
		expect(opts.every((o) => o.group === 'People')).toBe(true);
	});

	it('collapses a type the facets and the rows both carry', () => {
		const opts = eventTypeFilterOptions(
			[{ event_type: 'authentication.succeeded' }],
			['authentication.succeeded']
		);
		expect(opts).toEqual([
			{ value: 'authentication.succeeded', label: 'authentication.succeeded', group: 'Security' }
		]);
	});

	it('with no facets it is the loaded types alone, sectioned', () => {
		// The degradation an unwired mount (or an API that predates facets) gets.
		expect(eventTypeFilterOptions([{ event_type: 'document.sent' }])).toEqual([
			{
				value: 'document.sent',
				label: 'document.sent',
				group: 'Documents & forms'
			}
		]);
		expect(eventTypeFilterOptions([], [])).toEqual([]);
	});
});

describe('eventCategoryLabel', () => {
	it('says the row-glyph category in the product’s own words', () => {
		expect(eventCategoryLabel('client.created')).toBe('People');
		expect(eventCategoryLabel('aml.screen_ran')).toBe('Compliance');
		// `flows` reads as Journeys — the word the portals use for a flow.
		expect(eventCategoryLabel('action.flows-publish-flow')).toBe('Journeys');
		// Total: an unrecognised type lands in Settings rather than an empty heading.
		expect(eventCategoryLabel('action.something-nobody-mapped')).toBe('Settings');
	});
});

describe('actorName + the exact matchers the EventFeed uses', () => {
	it('names an actor by name, then email, then sub, else empty', () => {
		expect(actorName({ actor_name: 'Ada', actor_email: 'a@f.test', actor_sub: 'wos_a' })).toBe(
			'Ada'
		);
		expect(actorName({ actor_email: 'a@f.test', actor_sub: 'wos_a' })).toBe('a@f.test');
		expect(actorName({ actor_sub: 'wos_a' })).toBe('wos_a');
		expect(actorName({})).toBe('');
	});

	it('matchesEventType: exact type, empty string is no filter', () => {
		const ev = { event_type: 'document.sent' };
		expect(matchesEventType(ev, '')).toBe(true); // no filter
		expect(matchesEventType(ev, 'document.sent')).toBe(true);
		expect(matchesEventType(ev, 'document.viewed')).toBe(false);
		// EXACT, not a prefix: the EventFeed's picker offers whole loaded types and has no text box.
		expect(matchesEventType(ev, 'action.documents-')).toBe(false);
	});

	it('matchesActor: on the SAME display name actorSuggestions offers, empty is no filter', () => {
		const named = { actor_name: 'Ada Adviser', actor_email: 'ada@firm.test' };
		expect(matchesActor(named, [])).toBe(true); // no filter
		expect(matchesActor(named, ['Ada Adviser'])).toBe(true);
		expect(matchesActor(named, ['ada@firm.test'])).toBe(false); // name wins, so email is not the key
		// A row named only by email is matched by that email — the same value the option carried.
		expect(matchesActor({ actor_email: 'bo@firm.test' }, ['bo@firm.test'])).toBe(true);
	});
});

describe('actorSuggestions', () => {
	const rows = [
		{ actor_name: 'Bo Adviser', actor_email: 'bo@firm.test', actor_sub: 'wos_bo' },
		{ actor_name: 'Ada Adviser', actor_email: 'ada@firm.test', actor_sub: 'wos_ada' },
		// A repeat of Ada — collapses to one option.
		{ actor_name: 'Ada Adviser', actor_email: 'ada@firm.test', actor_sub: 'wos_ada' }
	];

	it('lists distinct actors sorted by name, with no "all" sentinel (FilterSelect adds it)', () => {
		const opts = actorSuggestions(rows);
		expect(opts).toEqual([
			{ value: 'Ada Adviser', label: 'Ada Adviser' },
			{ value: 'Bo Adviser', label: 'Bo Adviser' }
		]);
	});

	it('offers the FACET actors too, deduped and sorted with the loaded ones', () => {
		// Same argument as the event types: with the filter applied by the API, a list derived from the
		// answer withdraws every actor but the chosen one. The facet values are built by the endpoint
		// with `actorName`'s own precedence in SQL, so a facet-only actor matches the rows it names.
		const opts = actorSuggestions(rows, ['Ada Adviser', 'Cy Compliance']);
		expect(opts.map((o) => o.value)).toEqual(['Ada Adviser', 'Bo Adviser', 'Cy Compliance']);
	});

	it('with no facets it is the loaded actors alone (the EventFeed / admin audit-log posture)', () => {
		expect(actorSuggestions(rows).map((o) => o.value)).toEqual(['Ada Adviser', 'Bo Adviser']);
	});

	it('falls back to email then sub for a nameless actor, and skips a fully anonymous row', () => {
		const opts = actorSuggestions([
			{ actor_email: 'only@firm.test' },
			{ actor_sub: 'wos_sub_only' },
			{ actor_name: null, actor_email: null, actor_sub: null }
		]);
		const values = opts.map((o) => o.value);
		expect(values).toContain('only@firm.test');
		expect(values).toContain('wos_sub_only');
		// The two named-by-fallback actors only — the anonymous row contributes nothing.
		expect(opts).toHaveLength(2);
	});
});

describe('entityTagSuggestions', () => {
	// REPOINTED (was: "offers no tag a loaded event does not carry"). The tag filter is applied by the
	// API now, so offering only the kinds the loaded rows happen to carry means "you may not ask for
	// documents unless a document event is already on screen" — the option-shrinking defect the facets
	// exist to close, in another costume. The entity-kind vocabulary is CLOSED and known here, so it
	// needs no facet: it is offered in full.
	it('offers the whole closed vocabulary, humanised as the detail-pane chips say it, sorted', () => {
		const opts = entityTagSuggestions([]);
		expect(opts).toHaveLength(ENTITY_TAG_KINDS.length);
		expect(opts.map((o) => o.label)).toEqual([...opts.map((o) => o.label)].sort());
		// The product's own words, one vocabulary with the chips (`contact` → Client, `tenant` → Firm).
		expect(opts).toContainEqual({ value: 'contact', label: 'Client' });
		expect(opts).toContainEqual({ value: 'tenant', label: 'Firm' });
		expect(opts).toContainEqual({ value: 'document', label: 'Document' });
	});

	it('offers a kind the loaded rows carry even if the closed list has not grown to it yet', () => {
		// The read side can mint a kind before this list learns it (its chips and the API's tag map move
		// together), so the loaded rows are UNIONed in rather than validated against the list.
		const opts = entityTagSuggestions([
			{ entities: [{ kind: 'illustration', id: 'i1', label: 'Quote' }] }
		]);
		expect(opts).toContainEqual({ value: 'illustration', label: 'Illustration' });
		expect(opts).toHaveLength(ENTITY_TAG_KINDS.length + 1);
	});

	it('mints one option per kind, whatever the rows repeat', () => {
		const opts = entityTagSuggestions([
			{ entities: [{ kind: 'contact', id: '4', label: 'Max' }] },
			{ entities: [{ kind: 'contact', id: '8', label: 'Bo' }] },
			{ entities: null }
		]);
		expect(opts.filter((o) => o.value === 'contact')).toHaveLength(1);
		expect(opts).toHaveLength(ENTITY_TAG_KINDS.length);
	});
});

describe('matchesTags', () => {
	const ev = {
		entities: [
			{ kind: 'document', id: 'd', label: 'X' },
			{ kind: 'contact', id: '4', label: 'Max' }
		]
	};

	it('an empty selection is no filter', () => {
		expect(matchesTags(ev, [])).toBe(true);
		expect(matchesTags({ entities: [] }, [])).toBe(true);
	});
	it('matches a single selected kind the event carries', () => {
		expect(matchesTags(ev, ['document'])).toBe(true);
		expect(matchesTags(ev, ['contact'])).toBe(true);
	});

	it('requires EVERY selected kind — AND, not OR', () => {
		// The fixture carries a document AND a contact, so both together match…
		expect(matchesTags(ev, ['document', 'contact'])).toBe(true);
		// …but adding a kind it does not carry must NARROW to nothing. Under the old OR this returned
		// true, which meant a second tag made the result set GROW — the control read as broken.
		expect(matchesTags(ev, ['document', 'account'])).toBe(false);
		expect(matchesTags(ev, ['account', 'contact'])).toBe(false);
	});

	it('does not match when the event carries none of the selected kinds', () => {
		expect(matchesTags(ev, ['account'])).toBe(false);
		expect(matchesTags({ entities: [] }, ['document'])).toBe(false);
		expect(matchesTags({ entities: null }, ['document'])).toBe(false);
	});
});

describe('matchesEventTypePrefix — the EventStream’s event-type filter', () => {
	const ev = { event_type: 'client.created' };

	it('an empty (or blank) filter matches everything', () => {
		// An ARRAY since the filter became multi-valued (ORed at the API): "no filter" is the empty
		// array, and a blank entry is one that says nothing. This file was half-migrated — some
		// calls below already pass arrays — which is what a package with no type-checking on its
		// tests lets through.
		expect(matchesEventTypePrefix(ev, [])).toBe(true);
		expect(matchesEventTypePrefix(ev, ['   '])).toBe(true);
	});

	it('narrows a FAMILY, which is what the search box promises and what the server did', () => {
		// `a.event_type LIKE 'client.%'` at the API. Were this the exact match the feed uses, typing
		// "client." in the box would empty the table — a search box that silently finds nothing. A
		// family is now the RESOURCE, which is the half of a declared name before the dot.
		expect(matchesEventTypePrefix(ev, ['client.'])).toBe(true);
		expect(matchesEventTypePrefix(ev, ['action.documents-'])).toBe(false);
	});

	it('an exact type is its own prefix, so the dropdown works through the same matcher', () => {
		expect(matchesEventTypePrefix(ev, ['client.created'])).toBe(true);
		expect(matchesEventTypePrefix(ev, ['client.createds'])).toBe(false);
	});

	it('a typeless row matches nothing once a prefix is set', () => {
		expect(matchesEventTypePrefix({ event_type: null }, ['action.'])).toBe(false);
		expect(matchesEventTypePrefix({}, ['action.'])).toBe(false);
	});
});

describe('matchesMethod', () => {
	it('empty is no filter; otherwise the verb matches exactly', () => {
		expect(matchesMethod({ method: 'PATCH' }, [])).toBe(true);
		expect(matchesMethod({ method: 'PATCH' }, ['PATCH'])).toBe(true);
		expect(matchesMethod({ method: 'PATCH' }, ['POST'])).toBe(false);
	});

	it('is exact, not fuzzy — a lower-case value is not the segment’s upper-case verb', () => {
		// The toolbar's segments are the four upper-case verbs the stream records, which is the shape the
		// server compared after upper-casing at its boundary.
		expect(matchesMethod({ method: 'PATCH' }, ['patch'])).toBe(false);
		expect(matchesMethod({ method: null }, ['POST'])).toBe(false);
	});
});

describe('matchesSource — the tri-state, all three cases', () => {
	const proxiedRow = { target_tenant_id: 9 };
	const directRow = { target_tenant_id: null };

	it("'' is ALL — both sides survive", () => {
		expect(matchesSource(proxiedRow, '')).toBe(true);
		expect(matchesSource(directRow, '')).toBe(true);
	});

	it("'proxied' keeps only rows with a target firm", () => {
		expect(matchesSource(proxiedRow, 'proxied')).toBe(true);
		expect(matchesSource(directRow, 'proxied')).toBe(false);
		expect(matchesSource({}, 'proxied')).toBe(false); // absent = no target
	});

	it("'direct' keeps only rows without one", () => {
		expect(matchesSource(directRow, 'direct')).toBe(true);
		expect(matchesSource({}, 'direct')).toBe(true);
		expect(matchesSource(proxiedRow, 'direct')).toBe(false);
	});

	it('an unknown value means ALL, never "direct"', () => {
		// The fallback that matters: reading an unrecognised value as 'direct' would hide every action
		// Graphene support took on a firm's behalf — the opposite of unfiltered, on the one surface that
		// exists to disclose it.
		expect(matchesSource(proxiedRow, 'nonsense')).toBe(true);
		expect(matchesSource(directRow, 'nonsense')).toBe(true);
	});
});

describe('matchesDateRange — since inclusive, until inclusive of the whole typed day', () => {
	const at = (created_at: string) => ({ created_at });

	it('both bounds blank is no filter', () => {
		expect(matchesDateRange(at('2026-08-05T09:00:00Z'), '', '')).toBe(true);
	});

	it('since is INCLUSIVE from the first instant of the typed day', () => {
		expect(matchesDateRange(at('2026-08-05T00:00:00Z'), '2026-08-05', '')).toBe(true);
		expect(matchesDateRange(at('2026-08-04T23:59:59Z'), '2026-08-05', '')).toBe(false);
	});

	it('until keeps the WHOLE typed day — the boundary that fails silently', () => {
		// An `until` read as midnight drops the entire day it names, and a missing day reads as "the firm
		// did nothing that day", never as a bug. The rule is the query builder's own `actionLogUntilBound`,
		// reused rather than spelled twice.
		expect(matchesDateRange(at('2026-08-05T00:00:00Z'), '', '2026-08-05')).toBe(true);
		expect(matchesDateRange(at('2026-08-05T23:59:59.000Z'), '', '2026-08-05')).toBe(true);
		expect(matchesDateRange(at('2026-08-06T00:00:00Z'), '', '2026-08-05')).toBe(false);
	});

	it('a range keeps both end days and excludes either side', () => {
		const within = (d: string) => matchesDateRange(at(d), '2026-08-03', '2026-08-05');
		expect(within('2026-08-03T00:00:00Z')).toBe(true);
		expect(within('2026-08-04T12:00:00Z')).toBe(true);
		expect(within('2026-08-05T22:00:00Z')).toBe(true);
		expect(within('2026-08-02T23:59:59Z')).toBe(false);
		expect(within('2026-08-06T00:00:00Z')).toBe(false);
	});

	it('an unreadable BOUND filters nothing — a filter bar is not a place to fail', () => {
		expect(matchesDateRange(at('2026-08-05T09:00:00Z'), 'yesterday', '')).toBe(true);
		expect(matchesDateRange(at('2026-08-05T09:00:00Z'), '', 'soon')).toBe(true);
	});

	it('an unreadable ROW date matches nothing once a bound is set', () => {
		// It cannot be shown to be inside the range, and the tag filter sets the precedent (an event with
		// no entities matches no tag). With no bounds it is untouched.
		expect(matchesDateRange({ created_at: null }, '2026-08-05', '')).toBe(false);
		expect(matchesDateRange({}, '', '2026-08-05')).toBe(false);
		expect(matchesDateRange({ created_at: null }, '', '')).toBe(true);
	});
});

describe('matchesTenant', () => {
	const proxiedRow = { tenant_uid: 'graphene', target_tenant_uid: 'ten_firm' };
	const ownRow = { tenant_uid: 'ten_firm', target_tenant_uid: null };

	it('empty is no filter', () => {
		expect(matchesTenant(proxiedRow, [])).toBe(true);
	});

	it('matches EITHER side, like the server’s scope predicate', () => {
		// An action Graphene took ON a firm belongs in that firm's answer as much as one its own staff
		// took (`t.uid = :tenant OR tt.uid = :tenant`).
		expect(matchesTenant(ownRow, ['ten_firm'])).toBe(true);
		expect(matchesTenant(proxiedRow, ['ten_firm'])).toBe(true);
		expect(matchesTenant(proxiedRow, ['graphene'])).toBe(true);
		expect(matchesTenant(ownRow, ['other_firm'])).toBe(false);
	});
});

describe('matchesAllFilters — several filters at once, which is how the toolbar is used', () => {
	const row = {
		event_type: 'client.updated',
		method: 'PATCH',
		created_at: '2026-08-05T09:15:00Z',
		actor_name: 'Ada Adviser',
		tenant_uid: 'graphene',
		target_tenant_uid: 'ten_firm',
		target_tenant_id: 9,
		entities: [{ kind: 'contact', id: '4', label: 'Max' }]
	};

	it('no filters at all matches every row', () => {
		expect(matchesAllFilters(row, filters())).toBe(true);
	});

	it('every filter must hold — they compose as AND, tags included', () => {
		// The tags travel IN the filter object now (they have a wire name: `tags=document,contact`), so
		// there is one channel for a filter rather than an object plus a side argument.
		expect(
			matchesAllFilters(
				row,
				filters({
					tenant: ['ten_firm'],
					eventType: ['client.'],
					actor: ['Ada Adviser'],
					method: ['PATCH'],
					since: '2026-08-05',
					until: '2026-08-05',
					proxied: 'proxied',
					tags: ['contact']
				})
			)
		).toBe(true);
	});

	it('ONE failing filter rejects the row, whichever it is', () => {
		const base = {
			tenant: ['ten_firm'],
			eventType: ['client.'],
			actor: ['Ada Adviser'],
			method: ['PATCH'],
			since: '2026-08-05',
			until: '2026-08-05',
			proxied: 'proxied' as const
		};
		expect(matchesAllFilters(row, filters({ ...base, tenant: ['other_firm'] }))).toBe(false);
		expect(matchesAllFilters(row, filters({ ...base, eventType: ['action.documents-'] }))).toBe(
			false
		);
		expect(matchesAllFilters(row, filters({ ...base, actor: ['Bo Adviser'] }))).toBe(false);
		expect(matchesAllFilters(row, filters({ ...base, method: ['POST'] }))).toBe(false);
		expect(matchesAllFilters(row, filters({ ...base, since: '2026-08-06' }))).toBe(false);
		expect(matchesAllFilters(row, filters({ ...base, until: '2026-08-04' }))).toBe(false);
		expect(matchesAllFilters(row, filters({ ...base, proxied: 'direct' }))).toBe(false);
		expect(matchesAllFilters(row, filters({ ...base, tags: ['document'] }))).toBe(false);
	});

	it('an absent `tags` (an older filter object) filters by no tag rather than throwing', () => {
		// The fallback path runs over whatever a mount hands in; a missing list must read as "no tag
		// filter", never as an empty answer.
		const legacy = { ...EMPTY_ACTION_LOG_FILTERS } as ActionLogFilters;
		delete (legacy as { tags?: string[] }).tags;
		expect(matchesAllFilters(row, legacy)).toBe(true);
	});
});

describe('filtering narrows the ROWS, never the CHOICES', () => {
	// THE ORIGINAL BUG. The filters are applied by the API, so the answer to a filtered question
	// contains only the values that satisfy it: choosing one actor returns only their rows, and a
	// dropdown re-derived from those rows withdraws every other actor — leaving no way back but to clear
	// a filter whose effect you can no longer see.
	//
	// What holds it shut is that the vocabulary does not come from the answer: the facets are computed
	// from the caller's SCOPE and are filter-independent by contract (`audit_stream.action_facets`), and
	// the tag kinds are a closed list. Pinned here as the property that matters — the option lists take
	// no filter argument, so a filtered answer CANNOT be what they read — plus the source scan in
	// event-stream.filtering.ssr.test.ts, which pins that the component feeds them the facets.
	const loaded = [
		{
			id: 1,
			event_type: 'client.created',
			method: 'POST',
			created_at: '2026-08-05T09:00:00Z',
			actor_name: 'Ada Adviser',
			entities: [{ kind: 'contact', id: '1', label: 'Max' }]
		},
		{
			id: 2,
			event_type: 'document.sent',
			method: 'POST',
			created_at: '2026-08-04T09:00:00Z',
			actor_name: 'Bo Adviser',
			entities: [{ kind: 'document', id: 'd1', label: 'Report' }]
		},
		{
			id: 3,
			event_type: 'authentication.succeeded',
			method: 'POST',
			created_at: '2026-08-03T09:00:00Z',
			actor_name: 'Zoe Compliance',
			entities: []
		}
	];

	/** What the API answers once one actor is picked: only that actor's rows. */
	const filteredAnswer = loaded.filter((e) => e.actor_name === 'Ada Adviser');
	/** The scope's whole vocabulary, as the endpoint states it — unmoved by the filter. */
	const facets = {
		event_types: loaded.map((e) => e.event_type),
		actors: loaded.map((e) => e.actor_name)
	};

	it('a filtered ANSWER still offers the whole vocabulary, because the facets are its source', () => {
		expect(filteredAnswer.map((e) => e.id)).toEqual([1]);

		// Bo and Zoe are still selectable, which is the only way back from a filter you cannot see.
		expect(actorSuggestions(filteredAnswer, facets.actors).map((o) => o.value)).toEqual([
			'Ada Adviser',
			'Bo Adviser',
			'Zoe Compliance'
		]);
		expect(
			eventTypeFilterOptions(filteredAnswer, facets.event_types)
				.map((o) => o.value)
				.sort()
		).toEqual(['authentication.succeeded', 'document.sent', 'client.created'].sort());
		// And the tag kinds never depended on the rows at all.
		expect(entityTagSuggestions(filteredAnswer).map((o) => o.value)).toContain('account');
	});

	it('an answer with NO rows still offers the whole vocabulary', () => {
		// The state that used to be unrecoverable: the filters match nothing, so a row-derived dropdown
		// is empty and there is nothing left to click.
		expect(actorSuggestions([], facets.actors)).toHaveLength(3);
		expect(eventTypeFilterOptions([], facets.event_types)).toHaveLength(3);
		expect(entityTagSuggestions([]).length).toBeGreaterThan(0);
	});

	it('the same facets give the same options whatever the rows are — no filter reaches these lists', () => {
		// The property, stated directly: an option list is a function of the SCOPE's vocabulary, so the
		// answer it is rendered beside cannot narrow it. Neither list takes a filter to give one to.
		expect(actorSuggestions(filteredAnswer, facets.actors)).toEqual(
			actorSuggestions([], facets.actors)
		);
		expect(eventTypeFilterOptions(filteredAnswer, facets.event_types)).toEqual(
			eventTypeFilterOptions([], facets.event_types)
		);
	});

	it('the fallback (no fetcher) narrows the loaded rows and leaves the vocabulary alone', () => {
		const shown = loaded.filter((e) => matchesAllFilters(e, filters({ actor: ['Ada Adviser'] })));
		expect(shown.map((e) => e.id)).toEqual([1]);
		expect(actorSuggestions(loaded)).toHaveLength(3);
		expect(eventTypeFilterOptions(loaded)).toHaveLength(3);
	});
});
