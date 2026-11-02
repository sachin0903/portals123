// EventStream's server-applied filtering: fetch-and-swap, not client-side narrowing; the wiring
// below is a source scan because this package's vitest has no DOM to drive component effects from.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import EventStream, {
	EMPTY_ACTION_LOG_FILTERS,
	appendPage,
	createFilterApplier,
	listEvents,
	prependNew,
	seedList,
	type ActionLogFilters,
	type StreamPage
} from './event-stream.svelte';
import type { ActionEvent } from './describe.js';

const event = (over: Partial<ActionEvent> = {}): ActionEvent => ({
	id: 1,
	event_type: 'client.created',
	created_at: '2026-08-03T09:15:00Z',
	method: 'POST',
	route: '/firm/contacts',
	status: 200,
	path_params: {},
	metadata: { correlation_id: 'corr_1' },
	actor_id: 'u-1',
	actor_sub: 'wos_alice',
	actor_email: 'alice@firm.test',
	actor_name: 'Alice Feed',
	tenant_id: 1,
	tenant_uid: 'ten_firm',
	tenant_name: 'Feed Firm',
	target_tenant_id: null,
	target_tenant_uid: null,
	target_tenant_name: null,
	...over
});

const page = (events: ActionEvent[], hasMore = false): StreamPage => ({ events, hasMore });
const filters = (over: Partial<ActionLogFilters> = {}): ActionLogFilters => ({
	...EMPTY_ACTION_LOG_FILTERS,
	...over
});

describe('the in-place list (seed / append)', () => {
	it('seeds from the first page and carries its hasMore', () => {
		const list = seedList([event({ id: 3 }), event({ id: 2 })], true);
		expect(listEvents(list).map((e) => e.id)).toEqual([3, 2]);
		expect(list.more).toBe(true);
	});

	it('"Load more" appends after page one and takes its hasMore from the page just fetched', () => {
		const list = appendPage(
			appendPage(seedList([event({ id: 9 })], true), page([event({ id: 8 })], true)),
			page([event({ id: 7 })], false)
		);
		expect(listEvents(list).map((e) => e.id)).toEqual([9, 8, 7]);
		expect(list.more).toBe(false);
	});

	it('appending never disturbs page one — the two writers stay separable', () => {
		// The whole reason the list is held as {page, appended}: a fresh first page (a navigation, a
		// refresh, an APPLIED FILTER) replaces one part without having to work out which rows were paged
		// in.
		const first = seedList([event({ id: 9 })], true);
		const grown = appendPage(first, page([event({ id: 8 })], true));
		expect(grown.page).toEqual(first.page);
		expect(grown.appended.map((e) => e.id)).toEqual([8]);
	});

	it('an applied page RESETS the accumulation — a new first page invalidates every keyset page', () => {
		// The filters changed, so the pages fetched after the old page one answer a question nobody is
		// asking. `seedList` is what the apply commits with, which is why it is one function and not two.
		const grown = appendPage(seedList([event({ id: 9 })], true), page([event({ id: 8 })], true));
		const reapplied = seedList([event({ id: 4 })], false);
		expect(listEvents(grown).map((e) => e.id)).toEqual([9, 8]);
		expect(listEvents(reapplied).map((e) => e.id)).toEqual([4]);
		expect(reapplied.appended).toEqual([]);
		expect(reapplied.more).toBe(false);
	});
});

/**
 * The apply itself — executed, not scanned. Three answers: commit, keep-and-retry, and discard.
 */
describe('the live poll’s list transition (prependNew)', () => {
	it('puts new rows on top and KEEPS every loaded page', () => {
		// The whole reason it is a prepend and not a re-seed: a reader four "Load more" presses deep must
		// not lose those pages because something happened elsewhere in the firm.
		const grown = appendPage(seedList([event({ id: 9 })], true), page([event({ id: 8 })], true));
		const live = prependNew(grown, [event({ id: 11 }), event({ id: 10 })]);
		expect(listEvents(live).map((e) => e.id)).toEqual([11, 10, 9, 8]);
		// …and the keyset cursor (the OLDEST id) is untouched, so the next page is still the right one.
		expect(live.more).toBe(true);
	});

	it('drops rows already held, wherever they are held', () => {
		// A poll in flight when a "Load more" or an apply lands can answer with rows the new page already
		// contains. A duplicate id renders twice AND breaks the keyed each — so first occurrence wins and
		// a row already on screen keeps its position rather than jumping to the top.
		const grown = appendPage(seedList([event({ id: 9 })], true), page([event({ id: 8 })], true));
		const live = prependNew(grown, [event({ id: 10 }), event({ id: 9 }), event({ id: 8 })]);
		expect(listEvents(live).map((e) => e.id)).toEqual([10, 9, 8]);
	});

	it('is the SAME list object when there is nothing new', () => {
		// Identity matters: this runs on a timer, and returning a fresh object every tick would
		// invalidate `$derived`s (and re-render the table) twenty times a minute for no change at all.
		const grown = appendPage(seedList([event({ id: 9 })], true), page([event({ id: 8 })], true));
		expect(prependNew(grown, [])).toBe(grown);
		expect(prependNew(grown, [event({ id: 9 })])).toBe(grown);
	});
});

describe('createFilterApplier', () => {
	it('commits the page a successful fetch answered', async () => {
		const apply = createFilterApplier(async () => page([event({ id: 5 })], true));
		const result = await apply(filters({ actor: ['Ada Adviser'] }));
		expect(result.status).toBe('applied');
		if (result.status !== 'applied') return;
		expect(result.page.events.map((e) => e.id)).toEqual([5]);
		expect(result.page.hasMore).toBe(true);
	});

	it('hands the filters straight to the fetcher, tags included', async () => {
		const fetcher = vi.fn(async () => page([]));
		const apply = createFilterApplier(fetcher);
		const asked = filters({ actor: ['Ada Adviser'], tags: ['document', 'contact'] });
		await apply(asked);
		expect(fetcher).toHaveBeenCalledWith(asked);
	});

	it('reports a refusal as `failed` — the caller keeps the rows and offers a retry', async () => {
		const apply = createFilterApplier(async () => null);
		expect((await apply(filters())).status).toBe('failed');
	});

	it('a THROWING fetcher is the same answer as a refused one, never an unhandled rejection', async () => {
		// A rejection escaping here would strand the busy flag with no way back to the surface.
		const apply = createFilterApplier(async () => {
			throw new Error('network');
		});
		await expect(apply(filters())).resolves.toEqual({ status: 'failed' });
	});

	it('discards an answer a NEWER apply has overtaken — the stale-answer guard', async () => {
		// THE ONE THAT IS SILENT WHEN IT BREAKS. Two changes in quick succession can answer out of order;
		// the older answer landing last would leave the table showing rows nobody asked for beside a
		// toolbar that says otherwise. The guard is a generation count taken before the fetch.
		let release: ((value: StreamPage | null) => void) | undefined;
		const slow = new Promise<StreamPage | null>((resolve) => (release = resolve));
		let call = 0;
		const apply = createFilterApplier(async () => {
			call += 1;
			return call === 1 ? slow : page([event({ id: 2 })]);
		});

		const first = apply(filters({ actor: ['Ada Adviser'] })); // starts, will answer LAST
		const second = await apply(filters({ actor: ['Bo Adviser'] })); // starts, answers FIRST
		release?.(page([event({ id: 1 })]));

		expect(second.status).toBe('applied');
		expect(await first).toEqual({ status: 'stale' });
	});

	it('one applier per surface: a FRESH one would reset the count and let the stale answer win', async () => {
		// Stated as a pin because it is the whole reason the component creates it once, untracked.
		const fresh = createFilterApplier(async () => page([event({ id: 1 })]));
		expect((await fresh(filters({ actor: ['Ada Adviser'] }))).status).toBe('applied');
		expect((await fresh(filters({ actor: ['Bo Adviser'] }))).status).toBe('applied');
	});
});

describe('EventStream (SSR) with the filters applied by the API', () => {
	it('paints the loader’s page and its cursor button — no callback props required', () => {
		// A mount can still hand over `events` alone: with no fetcher the toolbar narrows what is loaded.
		const body = render(EventStream, {
			props: { events: [event({ id: 32 })], hasMore: true }
		}).body;
		expect(body).toContain('Alice Feed');
		expect(body).toContain('Load more');
	});

	it('offers Refresh only when the mount can reload, and pages either way', () => {
		const events = [event({ id: 30 })];
		const bare = render(EventStream, { props: { events, hasMore: true } }).body;
		const wired = render(EventStream, {
			props: {
				events,
				hasMore: true,
				onFilterApply: async () => page([event({ id: 29 })]),
				onLoadMore: async () => page([event({ id: 29 })]),
				onRefresh: () => {}
			}
		}).body;
		expect(bare).toContain('Load more');
		expect(wired).toContain('Load more');
		expect(bare).not.toContain('aria-label="Refresh"');
		expect(wired).toContain('aria-label="Refresh"');
	});

	it('shows no apply failure or busy note until one happens', () => {
		// Both are state the effects reach; first paint carries neither, with or without a fetcher.
		for (const props of [
			{ events: [event({ id: 33 })] },
			{ events: [event({ id: 33 })], onFilterApply: async () => page([]) }
		]) {
			const body = render(EventStream, { props }).body;
			expect(body).not.toContain('Couldn’t apply these filters');
			expect(body).not.toContain('Applying filters…');
		}
	});

	it('seeds an ENTRY filter onto the control it belongs on: a whole type is the PICK', () => {
		// The trigger shows the picked type as its chip, which is also proof the value is a real option —
		// and the search box must NOT be carrying it (see the two-controls rule).
		const body = render(EventStream, {
			props: {
				events: [event({ id: 34, event_type: 'document.sent' })],
				filters: {
					...EMPTY_ACTION_LOG_FILTERS,
					eventType: ['document.sent']
				}
			}
		}).body;
		expect(body).toContain('document.sent');
		// The picker's chip, not the search input: the raw id must never be written into the box.
		expect(body).not.toContain('value="document.sent"');
	});

	it('seeds SEVERAL picked types, because the filter is a list now', () => {
		const body = render(EventStream, {
			props: {
				events: [
					event({ id: 34, event_type: 'document.sent' }),
					event({ id: 35, event_type: 'task.created' })
				],
				filters: {
					...EMPTY_ACTION_LOG_FILTERS,
					eventType: ['document.sent', 'task.created']
				}
			}
		}).body;
		expect(body).toContain('document.sent');
		expect(body).toContain('task.created');
	});

	it('tolerates the OLD single-string shape instead of throwing', () => {
		// `eventType` was one string before it was a list of prefixes, and this type crosses a package
		// boundary: four mounts and eight loaders build it. A component that meets the old shape and
		// throws is a page with NO working filters (Svelte stops hydrating), which is strictly worse than
		// the filter it was asked for. So the shape is normalised where it is read.
		const body = render(EventStream, {
			props: {
				events: [event({ id: 34, event_type: 'document.sent' })],
				filters: {
					...EMPTY_ACTION_LOG_FILTERS,
					eventType: 'document.sent'
				} as unknown as typeof EMPTY_ACTION_LOG_FILTERS
			}
		}).body;
		expect(body).toContain('document.sent');
	});

	it('seeds the search box from `search`, and the picker from `eventType`', () => {
		// EACH CONTROL HAS ITS OWN FILTER now. The box was a second event-TYPE control — it filtered on
		// the type prefix, which is what the picker does properly — so the two had to be mutually
		// exclusive and the question people actually bring to a trail ("what happened to Margaret Cole")
		// had no control at all. They are independent and compose as AND.
		const body = render(EventStream, {
			props: {
				events: [event({ id: 36, event_type: 'client.created' })],
				filters: {
					...EMPTY_ACTION_LOG_FILTERS,
					eventType: ['client.created'],
					search: 'margaret'
				}
			}
		}).body;
		expect(body).toContain('value="margaret"'); // the box holds the search…
		expect(body).toContain('client.created'); // …and the picker holds its chip, at the same time
		expect(body).not.toContain('All events'); // the picker is NOT reset by a search term
	});

	it('offers the event-type picker from the FACETS, even with no rows loaded at all', () => {
		// The requirement in one assertion: the vocabulary is the endpoint's, not the loaded page's. An
		// answer with no rows must still offer every type in the scope, or a filter that matches nothing
		// can never be changed into one that matches something.
		//
		// Only the TRIGGER is server-rendered (bits' Select keeps its panel closed, so the items are not
		// in the HTML) — the lists themselves are pinned in event-stream-filters.test.ts.
		const withFacets = render(EventStream, {
			props: {
				events: [],
				facets: { event_types: ['document.sent'], actors: ['Zoe Compliance'] }
			}
		}).body;
		// The filters live in the BAR, which is shut until something is applied — so an unfiltered
		// arrival offers the funnel. Applied, the picker itself renders, and the vocabulary behind it
		// is the FACETS' (the endpoint's), not the loaded page's.
		expect(withFacets).toContain('aria-label="Show filters"');
		const picked = render(EventStream, {
			props: {
				events: [],
				facets: { event_types: ['document.sent'], actors: ['Zoe Compliance'] },
				filters: { ...EMPTY_ACTION_LOG_FILTERS, eventType: ['document.sent'] }
			}
		}).body;
		expect(picked).toContain('aria-label="Hide filters (1 applied)"');

		// No facets and no rows ⇒ no vocabulary, so the picker hides rather than offering an empty list.
		const bare = render(EventStream, { props: { events: [] } }).body;
		expect(bare).not.toContain('All events');
	});

	it('a FACET-ONLY type is a real option — the entry pick lands on it with no such row loaded', () => {
		// Proof the facet vocabulary reaches the control rather than only the loaded rows: the trigger
		// renders that type's humanised label, which it can only do for a value the option list carries.
		const body = render(EventStream, {
			props: {
				events: [event({ id: 37 })], // an `client.created` row, nothing else
				facets: { event_types: ['document.sent'], actors: [] },
				filters: { ...EMPTY_ACTION_LOG_FILTERS, eventType: ['document.sent'] }
			}
		}).body;
		// The type as the picker's CHIP — it can only render one for a value the option list carries, and
		// that list came from the facets alone here (the loaded row is a different type). The chip shows
		// the LABEL, which drops the `action.` namespace every option shares; the value keeps it.
		expect(body).toContain('document.sent');
		expect(body).not.toContain('value="document.sent"'); // and NOT in the search box
	});

	it('always offers the TAG picker — its vocabulary is closed, so it waits on nothing', () => {
		// Unconditional in `filterDefs`, unlike the event-type picker which waits on a vocabulary.
		// Applied, the bar is open and the tag is named on it.
		const body = render(EventStream, {
			props: { events: [], filters: { ...EMPTY_ACTION_LOG_FILTERS, tags: ['document'] } }
		}).body;
		expect(body).toContain('aria-label="Hide filters (1 applied)"');
	});
});

/**
 * The wiring — a source scan, because no headless run can execute it here (see the header). Each of
 * these is a regression that would be silent: the rows would still render and every pure test above
 * would still pass, while the surface quietly went back to narrowing a loaded page, or lost the
 * ability to un-filter itself, or started writing the filters into the URL.
 */
describe('the wiring: the filters are fetched, and the vocabulary is the endpoint’s', () => {
	const stripComments = (code: string) =>
		code
			.replace(/<!--[\s\S]*?-->/g, '')
			.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/(?<!:)\/\/[^\n]*/g, '');
	// Whitespace-flattened, so prettier's line breaking is not part of what is being asserted. Comments
	// are stripped FIRST, which is what lets the absence assertions below coexist with the header
	// comment that names each mechanism on purpose.
	const source = stripComments(
		readFileSync(join(import.meta.dirname, 'event-stream.svelte'), 'utf8')
	).replace(/\s+/g, ' ');

	it('fetches page one when the controls settle, through the injected fetcher', () => {
		expect(source).toMatch(
			/applyTimer = setTimeout\(\(\) => void applyNow\(next\), FILTER_DEBOUNCE_MS\)/
		);
		expect(source).toMatch(/const result = await applyFilters\(next\)/);
		expect(source).toMatch(
			/createFilterApplier\(async \(next\) => \(await onFilterApply\?\.\(next\)\) \?\? null\)/
		);
	});

	it('SWAPS the rows on success and resets the accumulation with them', () => {
		expect(source).toMatch(/list = seedList\(result\.page\.events, result\.page\.hasMore\)/);
		expect(source).toMatch(/applied = next/);
	});

	it('a stale answer changes NOTHING, and a failed one keeps the rows', () => {
		expect(source).toMatch(/if \(result\.status === 'stale'\) return/);
		// `applied` is only ever written on the success branch, so a failure leaves the rows and the
		// question they answer in step.
		expect(source).toMatch(/\} else \{ applyError = true; \}/);
	});

	it('the debounce cancels a pending fetch rather than queueing a second', () => {
		expect(source).toMatch(/return \(\) => clearTimeout\(applyTimer\)/);
	});

	it('derives the dropdowns from the FACETS unioned with the loaded rows', () => {
		// The original bug, one argument wide: without the facets these lists can only offer what the
		// filtered answer contains, so the value you want to switch to vanishes as soon as you filter.
		expect(source).toMatch(
			/const eventTypeOptions = \$derived\(eventTypeFilterOptions\(allEvents, facets\?\.event_types \?\? \[\]\)\)/
		);
		expect(source).toMatch(
			/const actorOptions = \$derived\(actorSuggestions\(allEvents, facets\?\.actors \?\? \[\]\)\)/
		);
		expect(source).toMatch(/const tagOptions = \$derived\(entityTagSuggestions\(allEvents\)\)/);
		expect(source).not.toMatch(/Suggestions\(visibleEvents\)/);
		expect(source).not.toMatch(/FilterOptions\(visibleEvents/);
	});

	it('the event-type picker is the shared MultiSelect: searchable, grouped, height-capped', () => {
		// One option per operation in the estate — scores of them — so three properties matter, and the
		// single-select this replaced had none of them: a search box, a panel that SCROLLS (a Select
		// panel grows to its content and ran off the viewport), and a trigger that stays one chip wide.
		// A filter bar uses FilterSelect for one value and MultiSelect for many, never a Combobox.
		expect(source).toMatch(/key: 'event_type'[\s\S]*?searchPlaceholder: 'Filter event types…'/);
		expect(source).not.toContain('Combobox');
		// The grouping comes from the option builder, so the control needs no knowledge of categories.
		expect(source).toMatch(/eventTypeFilterOptions\(allEvents/);
		// `monoLabel`: the LABEL is the raw `event_type` (the value the detail panel shows under "Type"),
		// with the humanised sentence as the hint beside it.
		expect(source).toMatch(/monoLabel/);
	});

	it('the PICK never round-trips through the search box (the reported defect)', () => {
		// The dropdown used to run controlled on `query`, so choosing an event dumped its raw
		// `action.*` id into the visible search input. The bar writes `eventTypePicks` and nothing
		// else — `query` is the toolbar's own bound state and the two never meet.
		expect(source).toMatch(/eventTypePicks = next\.event_type \?\? \[\]/);
		expect(source).not.toMatch(/value=\{query \|\| ALL\}/);
		expect(source).not.toMatch(/\(query = v === ALL \? '' : v\)/);
	});

	it('the box and the picker are INDEPENDENT filters that compose as AND', () => {
		// They were mutually exclusive only because both wrote the same `event_type` param — two
		// controls for one filter, where touching either silently emptied the other. The box is `q` now
		// (actor / client name / document / type / route) and the picker is `event_type`.
		expect(source).toMatch(/eventType: \[\.\.\.eventTypePicks\], search: query\.trim\(\)/);
		// …and NOTHING clears the other control any more.
		expect(source).not.toMatch(/untrack\(\(\) => \(eventTypePicks = \[\]\)\)/);
		expect(source).not.toContain('seenQuery');
	});

	it('the tags travel as a filter, not as a side channel', () => {
		expect(source).toMatch(/tags: \[\.\.\.selectedTags\]/);
		expect(source).toMatch(/sameSet\(a\.tags, b\.tags\)/);
		expect(source).toMatch(/matchesAllFilters\(e, activeFilters\)/);
	});

	it('Clear resets the search, the picker and the tags', () => {
		expect(source).toMatch(/function clearAll\(\) \{ query = ''; eventTypePicks = \[\];/);
		expect(source).toMatch(/selectedTags = \[\]; \}/);
		expect(source).toMatch(/eventTypePicks\.length > 0 \|\| query\.trim\(\) !== ''/); // `dirty` sees them
	});

	it('keeps NO URL sync — the second decision the owner made', () => {
		// Filters live in this component's state and nowhere else, so a reload resets them. Reading the
		// URL without writing it would be the worst of the three: arrive on a filtered link, move a
		// dropdown, and a reload jumps back to the stale filters still in the address bar.
		expect(source).not.toContain('replaceState');
		expect(source).not.toContain('pushState');
		expect(source).not.toContain('onFiltersChange');
		expect(source).not.toContain('goto');
	});

	it('the rows are internal state, seeded from the events prop and re-seeded by reference', () => {
		// A real navigation and the app's onRefresh both land a new array; the reference compare is how
		// that is told apart from a "Load more" or an apply, neither of which touches the prop.
		expect(source).toMatch(/const allEvents = \$derived\(listEvents\(list\)\)/);
		expect(source).toMatch(/if \(events !== seenPage\)/);
		expect(source).toMatch(/list = seedList\(events, hasMore\)/);
	});

	it('a new first page does NOT reset the operator’s filters, and records what the rows answer', () => {
		// A new `events` prop is the UNFILTERED first page (a loader reads no filters — they are not in
		// the URL). Wiping the controls would throw away the only thing the operator chose, so instead
		// the component records that the rows now answer the unfiltered question. That write keeps
		// `applied` TRUTHFUL, which is what "Load more" pages at.
		expect(source).toMatch(
			/if \(events !== seenPage\) \{ seenPage = events; untrack\(\(\) => \{ list = seedList\(events, hasMore\); pending = \[\]; applied = EMPTY_ACTION_LOG_FILTERS; \}\); \}/
		);
		// `hiddenCount` rides along because the api counts on the FIRST PAGE ONLY: a fresh first page
		// carries a fresh number, and a keyset continuation carries none. Reading it anywhere else
		// would make the offer flicker away on "Load more".
		// …and the controls themselves are untouched by it.
		expect(source).not.toMatch(/seenPage = events;[^}]*clearAll\(\)/);
	});

	it('the poll OFFERS rows and never moves the table itself', () => {
		// THE RULE THIS SURFACE TURNS ON. This is a master-detail reader: the table is ranked newest-first
		// and, with nothing selected, the detail pane tracks the newest row. A background prepend would
		// therefore re-rank the rows under the cursor and swap the document being read. So the poll writes
		// only `pending`, and `prependNew` — the one call that moves rows — is reached from `showPending`
		// alone, which is a click.
		expect(source).toMatch(/function showPending\(\)[^}]*list = prependNew\(list, pending\)/s);
		// `pollTick` assigns `pending` and nothing else — in particular never `list`.
		const tick = source.slice(
			source.indexOf('async function pollTick'),
			source.indexOf('function showPending')
		);
		expect(tick).toContain('pending = [...fresh, ...pending]');
		expect(tick).not.toMatch(/\blist = /);
	});

	it('the poll asks at the APPLIED filters, and discards an answer that arrived late', () => {
		// Same rule as "Load more": the count offered must be a count of rows that will actually appear.
		// And the filters can change WHILE the poll is in flight — without the generation check the answer
		// to the previous question lands in the pill under the new one.
		const tick = source.slice(
			source.indexOf('async function pollTick'),
			source.indexOf('function showPending')
		);
		expect(tick).toMatch(/const asked = untrack\(\(\) => applied\)/);
		expect(tick).toMatch(/onPollNewer\(after, asked\)/);
		expect(tick).toMatch(/if \(untrack\(\(\) => !same\(applied, asked\)\)\) return/);
	});

	it('held rows are discarded whenever the question changes', () => {
		// They answered the PREVIOUS filters. Showing them after an apply would mix two filter sets into
		// one table — a wrong answer that looks like data, which is this component's standing hazard.
		// Both re-seed sites must clear them: the applied filter, and a loader page landing.
		const applySeed = source.indexOf('list = seedList(result.page.events, result.page.hasMore)');
		const propSeed = source.indexOf('list = seedList(events, hasMore)');
		expect(applySeed).toBeGreaterThan(-1);
		expect(propSeed).toBeGreaterThan(-1);
		expect(source.slice(applySeed, applySeed + 400)).toContain('pending = []');
		expect(source.slice(propSeed, propSeed + 200)).toContain('pending = []');
	});

	it('no onPollNewer ⇒ no timer, no listeners, no pill', () => {
		// How the embedded rails (a client's record, a document's events tab) stay exactly as they are:
		// they are read in passing, not watched.
		expect(source).toMatch(/if \(!onPollNewer \|\| typeof document === 'undefined'\) return;/);
		expect(source).toMatch(/if \(!onPollNewer\) return;/);
	});

	it('uses the shared poll controller rather than a second timer', () => {
		// The bell's, already unit-tested for the failure that matters here too: a missed resume leaves the
		// feed permanently stale while looking live. A bespoke setInterval would also have to re-derive the
		// pause/resume rules, which is how two timers come to behave differently.
		expect(source).toContain("from '../notifications/poll-controller.js'");
		expect(source).toMatch(/createPollController\(POLL_INTERVAL_MS/);
		expect(source).not.toMatch(/setInterval/);
		// Paused while hidden, immediate on return — the reason a returning reader is not left waiting.
		expect(source).toMatch(/document\.hidden \? poller\.hidden\(\) : poller\.visible\(\)/);
	});

	it('Refresh RE-ASKS at the filters on screen, not the loader’s unfiltered page', () => {
		// THE DEFECT, and why it was invisible. Refresh used to be `onRefresh()` alone — a loader re-run,
		// which asks the UNFILTERED page. The controls kept their values while the rows underneath
		// stopped answering to them, and nothing on screen distinguishes a filter that matches
		// everything from one that is no longer applied.
		//
		// A re-apply was MEANT to follow it: the new-page branch above sets `applied` to the unfiltered
		// filters, which should make the apply effect notice `activeFilters !== applied` and fetch again.
		// It cannot. That effect reads `applied` inside `untrack` — deliberately, so only a real control
		// change schedules a fetch — so writing `applied` notifies nothing, and with the controls not
		// having moved, `activeFilters` is unchanged and the effect never re-runs. A mechanism that
		// cannot fire is worse than none, because the comment beside it says the case is handled.
		//
		// So Refresh applies DIRECTLY when anything is filtered, and is the loader re-run only when
		// nothing is (which also refreshes the dropdown vocabulary — the one thing an apply leaves alone,
		// by design, since the vocabulary is filter-independent).
		expect(source).toMatch(
			/if \(onFilterApply && !same\(next, EMPTY_ACTION_LOG_FILTERS\)\) \{ .*?await applyNow\(next\); \} else \{ await onRefresh\?\.\(\); \}/
		);
		// The debounced apply is cancelled first, so the two cannot both fire for one click.
		expect(source).toMatch(/clearTimeout\(applyTimer\); await applyNow\(next\)/);
		expect(source).toMatch(/onrefresh=\{onRefresh \? refresh : undefined\}/);
	});

	it('pages at the APPLIED filters — never the controls, never unfiltered', () => {
		// The controls may have moved since; the rows on screen answer `applied`, and a page fetched at
		// anything else would append rows from a different question and still answer 200.
		expect(source).toMatch(/onLoadMore\?\.\(before, applied\)/);
		expect(source).not.toMatch(/onLoadMore\?\.\(before, EMPTY_ACTION_LOG_FILTERS\)/);
		expect(source).not.toMatch(/onLoadMore\?\.\(before, activeFilters\)/);
	});

	it('narrows client-side ONLY when there is no fetcher', () => {
		// With a fetcher the rows ARE the answer; re-running the matchers over them narrows a second time
		// on slightly different rules (`matchesTags` reads resolved entities where the API tests captured
		// keys) and can hide rows the API rightly returned.
		expect(source).toMatch(
			/const visibleEvents = \$derived\( onFilterApply \? allEvents : allEvents\.filter\(\(e\) => matchesAllFilters\(e, activeFilters\)\) \)/
		);
	});

	it('refreshes through the app’s own load when nothing is filtered', () => {
		// The unfiltered case is still the loader re-run: same rows, plus a fresh vocabulary.
		expect(source).toMatch(/await onRefresh\?\.\(\)/);
		expect(source).toMatch(/onrefresh=\{onRefresh \? refresh : undefined\}/);
	});
});
