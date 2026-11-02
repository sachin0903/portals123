<script lang="ts" module>
	import type { ActionEvent } from './describe.js';
	import type { FilterDef } from '../book/listing/filter-panel.svelte';
	import type { MultiSelectOption } from '../ui/multi-select/types.js';

	/** A tenant option for the firm filter — fed by the endpoint's own lookup, so the surface works
	 *  for an operator holding `audit:read` without `tenants:read`. */
	export interface ActionLogTenant {
		uid: string;
		name: string;
	}

	/**
	 * The complete filter VOCABULARY for the caller's scope, as the endpoint states it (`?facets=true`)
	 * — and deliberately INDEPENDENT of the filters in force, which is the whole of its value. With the
	 * filters applied by the API, a dropdown derived from the answer offers only the values that answer
	 * contains: pick one actor and every other actor withdraws from the list, so the only way back is
	 * to clear a filter whose effect you can no longer see.
	 *
	 * The facets describe the most recent window of the stream, not all history, so the component
	 * UNIONs them with whatever the loaded rows carry (see `event-stream-filters.ts`). Absent ⇒ the
	 * dropdowns fall back to the loaded rows alone, which is what an unwired mount gets.
	 */
	export interface ActionFacets {
		event_types: string[];
		actors: string[];
	}

	/**
	 * The toolbar's filters — the question the API is asked, not a narrowing of what came back.
	 *
	 * Every field is a wire name away from `action-log-query.ts`, which maps this object onto
	 * `GET /admin/audit/actions` / `GET /firm/audit/actions` for the loader's first page, for every
	 * in-place apply and for every "Load more". Empty string / empty list = unfiltered, and an
	 * unfiltered field is OMITTED from the query rather than sent blank.
	 */
	export interface ActionLogFilters {
		/** Tenant uids, ORed — matches the acting OR the proxied-target side. Comma-separated on the wire. */
		tenant: string[];
		/** Event-type PREFIXES, ORed — an event matches if it starts with any of them. An exact type is
		 *  its own prefix, so a resource (`document.`) narrows to a family. On the wire as ONE
		 *  comma-separated param: `event_type=document.sent,task.created`.
		 *
		 *  This is the PICKER's filter only. The toolbar's box is `search` — a different question. */
		eventType: string[];
		/** The toolbar's free-text search (`q`), over what a reader recognises a row by: the actor, the
		 *  CLIENT's name, the document's name, the event type and the route.
		 *
		 *  It used to be a second event-TYPE control — the box filtered on the type prefix, which is what
		 *  the picker beside it does properly now. So the two had to be mutually exclusive, and the
		 *  question people actually bring to a trail ("what happened to Margaret Cole") had no control at
		 *  all: a contact id is not something anybody knows. They are independent filters now and compose
		 *  as AND, like every other pair. */
		search: string;
		/** Actor display names (name, else email, else sub — `actorName`'s precedence), ORed, comma-separated on the wire. */
		actor: string[];
		/** HTTP methods, ORed, comma-separated on the wire. */
		method: string[];
		/** ISO dates (yyyy-mm-dd); since inclusive, until exclusive at the API. */
		since: string;
		until: string;
		/** Source of the action: '' = all, 'proxied' = only actions taken on this firm's behalf
		 *  (target_tenant_id present), 'direct' = only direct actions (no target). A normal
		 *  three-way filter, mapped to the API's tri-state `proxied` bool (true/false/absent). */
		proxied: '' | 'proxied' | 'direct';
		/** Entity KINDS the event must touch — ANDed, every selected kind must be present (see
		 *  `matchesTags`). On the wire as ONE comma-separated param: `tags=document,contact`. */
		tags: string[];
	}

	export const EMPTY_ACTION_LOG_FILTERS: ActionLogFilters = {
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

	/** One page of the stream as a fetcher ANSWERS it: the rows, plus whether more remain behind them.
	 *  The counterpart of `action-log-query.ts`'s `ActionLogPage`, which is the page a caller ASKS for
	 *  (`limit`/`before`) — same word, opposite direction, so the two are named apart. */
	export interface StreamPage {
		events: ActionEvent[];
		hasMore: boolean;
	}

	/**
	 * The list ON SCREEN, held as two named parts rather than one array — because two different things
	 * write it and only one of them appends: `page` is page ONE (the loader's prop, or the page an
	 * applied filter fetched) and `appended` the keyset pages "Load more" has added since.
	 *
	 * It lives in the module block, as plain values with pure transitions, so those writes can be
	 * unit-tested AT ALL: this package's vitest runs in a plain node environment (no jsdom anywhere in
	 * the tree — see ui/theme-toggle/scheme.test.ts, which makes the same argument), so a component's
	 * reactive behaviour cannot be driven from a test. Keeping the decisions out of the effects is what
	 * leaves anything testable behind.
	 */
	export interface StreamList {
		page: ActionEvent[];
		appended: ActionEvent[];
		more: boolean;
	}

	/** The list as it starts: page one from the prop, nothing appended. Also what a REAL navigation, a
	 *  refresh, or an APPLIED FILTER resets it to — a new first page invalidates every keyset page
	 *  fetched after it, whichever of the three landed it. */
	export function seedList(events: ActionEvent[], hasMore: boolean): StreamList {
		return { page: events, appended: [], more: hasMore };
	}

	/** "Load more" APPENDS in place — page one is untouched, `more` follows the page just fetched. */
	export function appendPage(list: StreamList, page: StreamPage): StreamList {
		return { page: list.page, appended: [...list.appended, ...page.events], more: page.hasMore };
	}

	/** The rows to render: page one, then everything appended after it (the stream is id-descending,
	 *  so this stays newest-first). */
	export function listEvents(list: StreamList): ActionEvent[] {
		return [...list.page, ...list.appended];
	}

	/**
	 * The live poll's transition: rows that arrived AFTER page one, put at the top.
	 *
	 * Prepending rather than re-seeding is the whole point. A reader who has pressed "Load more" four
	 * times holds four keyset pages, and replacing page one throws all of them away — so the cheap
	 * implementation of "keep the feed fresh" costs the reader their place every time anything happens.
	 * The stream is id-descending and every polled row is newer than everything held, so the top is
	 * where they belong and the `before` cursor (the OLDEST id) is untouched.
	 *
	 * Deduplicated by id, and that is not defensive noise: a poll in flight when a "Load more" or an
	 * apply lands can answer with rows the new page already contains, and a duplicate id would render
	 * twice AND break the keyed `{#each}`. The FIRST occurrence wins, so a row already on screen keeps
	 * its position instead of jumping to the top.
	 */
	export function prependNew(list: StreamList, rows: ActionEvent[]): StreamList {
		if (rows.length === 0) return list;
		const held = new Set(listEvents(list).map((e) => e.id));
		const fresh = rows.filter((e) => !held.has(e.id));
		if (fresh.length === 0) return list;
		return { page: [...fresh, ...list.page], appended: list.appended, more: list.more };
	}

	/** What an in-place apply answered, and therefore what the component must do: swap the rows for
	 *  `page`, keep them and offer a retry, or do NOTHING AT ALL because a newer apply is in flight. */
	export type ApplyResult =
		{ status: 'applied'; page: StreamPage } | { status: 'failed' } | { status: 'stale' };

	/**
	 * The in-place filter apply, as a pure state machine over a fetcher — so the three answers can be
	 * unit-tested at all (this package's vitest runs in a plain node environment, see `StreamList`).
	 *
	 * IT OWNS THE STALE-ANSWER GUARD, which is the part that is silent when it goes wrong: a
	 * GENERATION COUNT is taken before the fetch and compared after it, and an answer that is no longer
	 * the newest reports `stale` and is discarded. Two changes in quick succession can answer out of
	 * order, and the older answer landing last would leave the table showing rows nobody asked for
	 * beside a toolbar that says otherwise — a wrong answer that looks like data.
	 *
	 * A THROWING fetcher is the same answer as a refused one (`failed`): the caller's contract is
	 * "null means it failed", and a rejection escaping here would strand the busy flag and surface as
	 * an unhandled rejection instead of a retry.
	 *
	 * Create it ONCE per surface — a fresh applier resets the count, which is exactly how a stale
	 * answer gets through.
	 */
	export function createFilterApplier(
		fetchPage: (filters: ActionLogFilters) => Promise<StreamPage | null>
	): (filters: ActionLogFilters) => Promise<ApplyResult> {
		let generation = 0;
		return async (filters) => {
			const mine = ++generation;
			let page: StreamPage | null = null;
			try {
				page = await fetchPage(filters);
			} catch {
				page = null;
			}
			if (mine !== generation) return { status: 'stale' };
			return page ? { status: 'applied', page } : { status: 'failed' };
		};
	}

	/** A pure, exported function so an SSR test can read the shape without rendering the popover. */
	export function eventStreamFilterDefs(input: {
		showTenantFilter: boolean;
		tenantOptions: MultiSelectOption[];
		eventTypeOptions: MultiSelectOption[];
		tagOptions: MultiSelectOption[];
		actorOptions: MultiSelectOption[];
		proxiedOptions: MultiSelectOption[];
		methodOptions: MultiSelectOption[];
	}): FilterDef[] {
		const {
			methodOptions,
			showTenantFilter,
			tenantOptions,
			eventTypeOptions,
			tagOptions,
			actorOptions,
			proxiedOptions
		} = input;
		return [
			...(showTenantFilter
				? [
						{
							key: 'tenant',
							label: 'Firm',
							options: tenantOptions,
							searchPlaceholder: 'Filter firms…'
						}
					]
				: []),
			...(eventTypeOptions.length
				? [
						{
							key: 'event_type',
							label: 'Event type',
							options: eventTypeOptions,
							searchPlaceholder: 'Filter event types…',
							// The LABEL here is the raw `event_type` — the value the detail panel shows under
							// "Type" — and the human sentence rides as the hint.
							monoLabel: true
						}
					]
				: []),
			{ key: 'method', label: 'Change', options: methodOptions },
			{ key: 'tags', label: 'Tag', options: tagOptions, searchPlaceholder: 'Filter tags…' },
			{
				key: 'actor',
				label: 'Actor',
				options: actorOptions,
				searchPlaceholder: 'Filter actors…'
			},
			{
				key: 'proxied',
				label: 'Source',
				options: proxiedOptions,
				single: true,
				allLabel: 'All sources'
			},
			{ key: 'date', label: 'Date', options: [], kind: 'daterange' }
		];
	}
</script>

<script lang="ts">
	// The action-event stream, read for humans (audit.actions / GET /admin/audit/actions): a
	// full-width scanning TABLE of events (who · action · when) where a row EXPANDS IN PLACE to show
	// the whole event under the line that raised the question. Expansion is CLIENT-SIDE (no URL
	// param, no history churn); the newest row is open on arrival, so the surface never lands on a
	// table that says nothing.
	//
	// IT WAS MASTER-DETAIL — a 58% table beside a fixed detail pane — and the pane cost the trail
	// nearly half its width permanently, to answer a question the reader has about one row at a time.
	//
	// TWO DECISIONS A READER WILL QUESTION, both deliberate and both the product owner's:
	//
	//   * EVERY FILTER IS APPLIED BY THE API, fetched in place through `onFilterApply`. A filter
	//     therefore covers the WHOLE trail rather than the fifty rows that happen to be loaded — on a
	//     compliance record "nobody did that" and "nobody did that on this page" are different answers
	//     and only one of them is true. THE PRICE IS A ROUND TRIP per change (debounced below), and it
	//     is accepted. It is also why the dropdowns are fed by the endpoint's filter-independent
	//     `facets` vocabulary: a server-applied filter otherwise eats its own options.
	//   * THE FILTERS ARE NOT IN THE URL. No `goto`, no `replaceState`, no announcement prop: they live
	//     in this component's state and nowhere else, so a filtered view is neither shareable nor
	//     bookmarkable and A RELOAD RESETS THEM. Reading the URL without writing it is the worst of the
	//     three — you could arrive on a filtered link, move a dropdown, and a reload would jump back to
	//     the stale filters still in the address bar.
	//
	// A mount that passes NO `onFilterApply` still filters: the controls narrow the loaded rows through
	// the same pure matchers (`matchesAllFilters`), which is the honest degradation of the same
	// toolbar rather than a dead bar.
	//
	// Split from the former single-file ActionLog: the detail pane
	// `event-detail.svelte`, the pure read logic `event-detail.ts` and the filter derivations
	// `event-stream-filters.ts` (all unit-tested). The toolbar is the shared listing frame.
	import { untrack, type Snippet } from 'svelte';
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import BanknoteIcon from '@lucide/svelte/icons/banknote';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import ShieldAlertIcon from '@lucide/svelte/icons/shield-alert';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import UserIcon from '@lucide/svelte/icons/user';
	import WorkflowIcon from '@lucide/svelte/icons/workflow';
	import { whenVisible } from '../../hooks/when-visible.js';
	import { Button } from '../ui/button/index.js';
	import { Card, CardContent } from '../ui/card/index.js';
	import { DataTable, type DataTableColumn, type DataTableSort } from '../ui/data-table/index.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import ListingToolbar from '../book/listing/listing-toolbar.svelte';
	import { ALL } from '../book/listing/filters.js';
	import PersonAvatar from '../data/person-avatar.svelte';
	import type { ActionCategory, ActionEntity } from './describe.js';
	import { eventCategory, methodLabel, timestampParts } from './describe.js';
	import {
		actorSuggestions,
		entityTagSuggestions,
		eventTypeFilterOptions,
		filterList,
		matchesAllFilters
	} from './event-stream-filters.js';
	// The bell's poll timer, reused: one visibility-aware state machine, already unit-tested for
	// the missed-resume failure that would leave this feed stale while looking live.
	import { createPollController } from '../notifications/poll-controller.js';
	import EventDetail from './event-detail.svelte';
	import { headline, initialsOf } from './event-detail.js';

	let {
		events,
		tenants = [],
		facets,
		filters = EMPTY_ACTION_LOG_FILTERS,
		hasMore = false,
		loadError = false,
		showTenantFilter = true,
		attribution = 'operations',
		links,
		entityHref,
		onFilterApply,
		onLoadMore,
		onRefresh,
		onPollNewer,
		actions
	}: {
		events: ActionEvent[];
		tenants?: ActionLogTenant[];
		/** The endpoint's complete filter vocabulary for this scope (see `ActionFacets`). Absent ⇒ the
		 *  dropdowns offer only what the loaded rows carry. */
		facets?: ActionFacets;
		/** The ENTRY filters — a starting point for the toolbar's own state, not a live channel: the
		 *  component seeds its controls from it once and is the source of truth from then on (the URL
		 *  carries no filters, so nothing moves this prop mid-session). A mount that opens on a
		 *  pre-narrowed view still works by passing it, and page one is assumed to have been fetched
		 *  AT these filters — which is what "Load more" then pages at. */
		filters?: ActionLogFilters;
		/** True when the FIRST page (the loader's) has more stream behind it. */
		hasMore?: boolean;
		/** The loader's first-page read FAILED (the page shows its own retry banner above). Passed in
		 *  so the empty state reads as an error, not a definitive "no events" — a failed read must
		 *  never look like an empty trail. */
		loadError?: boolean;
		/** False on single-firm surfaces (the platform portal reads its OWN trail — a firm dropdown
		 *  there would be a control with one meaningless option). */
		showTenantFilter?: boolean;
		/** Who is reading the trail — it changes how a PROXIED row is attributed (see the rows below) and
		 *  whether the detail pane shows the raw JSON (see event-detail). */
		attribution?: 'operations' | 'firm';
		/** Turns a target id chip (a path param) into a link into the host app's own routes. */
		links?: (key: string, value: unknown) => string | null;
		/** The entity-row counterpart of `links`: turns a RESOLVED entity into a link. */
		entityHref?: (event: ActionEvent, entity: ActionEntity) => string | null;
		/** Fetch page ONE at these filters — the whole trail narrowed by the API, which is the point
		 *  (see the header). The component SWAPS its rows for the answer and resets the "Load more"
		 *  accumulation with it; `null` means the fetch failed, in which case the rows on screen stay
		 *  and a retry is offered.
		 *
		 *  ABSENT ⇒ the toolbar narrows the loaded rows client-side instead. Nothing is fetched, so an
		 *  unwired mount filters what it has rather than nothing at all. */
		onFilterApply?: (filters: ActionLogFilters) => Promise<StreamPage | null>;
		/** Fetch the page of events immediately BEFORE `before` (keyset, id-descending). Returns them
		 *  plus whether more remain, or null on failure. The component APPENDS them in place — no
		 *  navigation, no URL change, no re-fetch of what is already on screen.
		 *
		 *  The filters it is handed are the APPLIED ones — the filters the rows on screen answer, not
		 *  whatever the controls have moved to since. Paging at anything else would append rows from a
		 *  different question and still answer 200. */
		onLoadMore?: (before: number, filters: ActionLogFilters) => Promise<StreamPage | null>;
		/** Re-pull the first page (events that arrived since load) by RE-RUNNING THE PAGE LOAD — usually
		 *  `invalidateAll`, which replaces `events` with a fresh array. Absent ⇒ no refresh affordance.
		 *
		 *  It lands the UNFILTERED first page (a loader reads no filters from the URL, because they are
		 *  not there), so the toolbar re-applies its own filters from this component's state — which is
		 *  why the reset effect below deliberately leaves the controls alone. */
		onRefresh?: () => void | Promise<void>;
		/** THE LIVE POLL. Asked every `POLL_INTERVAL_MS` while the tab is visible: "what has happened
		 *  since event `after`, at these filters?" — the app fetches with the same query builder it uses
		 *  for everything else, so the answer is scoped and filtered exactly like the rows on screen.
		 *
		 *  Absent ⇒ NO POLLING AT ALL, which is how the embedded rails (a client's record, a document's
		 *  events tab) stay exactly as they are: they are read in passing, not watched, and a pill
		 *  offering new rows on a tab nobody is looking at is noise.
		 *
		 *  Returning null (a refused or failed fetch) is a no-op — the next tick recovers. */
		onPollNewer?: (after: number, filters: ActionLogFilters) => Promise<ActionEvent[] | null>;
		/** Page acts (a `ListingOverflow`), on the right of the toolbar. */
		actions?: Snippet;
	} = $props();

	// WHICH ROW IS OPEN — client-side, no URL param, no history churn.
	//
	// SINGLE-OPEN, i.e. an accordion. A trail is SCANNED, and every open row pushes the rest of the
	// list down by the height of a whole detail: three open events and the next row you want is two
	// screens away, so the order — the only thing a trail has — stops being legible.
	//
	// NOTHING is open on arrival. The master-detail pane opened the newest row because a pane that
	// shows nothing is a hole in the layout; an expanded row is not, and a surface that opens one
	// for you has answered a question nobody asked while pushing the rest of the list down a screen.
	let expandedId = $state<number | null>(null);

	/** Click a row: open it, or close it if it is the one already open. */
	function toggleRow(e: ActionEvent) {
		expandedId = expandedId === e.id ? null : e.id;
		// Opening or closing changes the list's HEIGHT, which can slide the sentinel into the
		// prefetch band with nobody having scrolled. See `armed`.
		armed = false;
	}

	// The list is INTERNAL STATE, not a projection of the `events` prop, because three things write it:
	// the prop seeds it (SSR / first entry / a real navigation or refresh), an APPLIED FILTER replaces
	// it, and "Load more" APPENDS. The transitions are the pure module-block functions (seedList /
	// appendPage), so what they do is unit-tested. Seeded ONCE from the props (untrack, this file's
	// idiom) — which is also what makes SSR correct, since the reset effect below does not run
	// server-side.
	let list = $state<StreamList>(untrack(() => seedList(events, hasMore)));
	let loadingMore = $state(false);
	// Re-seed when the FIRST PAGE PROP changes: a real navigation, or the app's onRefresh landing a
	// fresh page. The reference change is the reliable signal (a value compare could miss two pages
	// sharing a newest row); "Load more" and an applied filter both leave the prop untouched, so this
	// never wipes either. `seenPage` is a plain, non-reactive marker, so ONLY `events` drives the
	// effect — `hasMore` is read untracked inside. The toolbar's own state is deliberately NOT
	// re-seeded: the filters are the operator's, and a refresh re-applies them.
	let seenPage = untrack(() => events);
	$effect(() => {
		if (events !== seenPage) {
			seenPage = events;
			untrack(() => {
				list = seedList(events, hasMore);
				pending = [];
				// A loader's page is the UNFILTERED question — the filters are not in the URL, so nothing
				// else could have asked it — and that is now what the rows answer. Saying so is what makes
				// the apply effect below notice the standing filters again and re-fetch them: without it a
				// refresh would leave unfiltered rows sitting under a toolbar that says otherwise, which is
				// a wrong answer that looks like data.
				applied = EMPTY_ACTION_LOG_FILTERS;
			});
		}
	});
	const allEvents = $derived(listEvents(list));
	const more = $derived(list.more);

	// Cap the in-place growth so a runaway "Load more" cannot pull the whole stream into one page.
	const MAX_EVENTS = 1000;
	const atCap = $derived(allEvents.length >= MAX_EVENTS);

	async function loadMore() {
		const before = allEvents[allEvents.length - 1]?.id;
		if (before == null || loadingMore) return;
		loadingMore = true;
		try {
			// At the APPLIED filters (see the prop): the next page must answer the same question the rows
			// already in the table answer.
			const page = await onLoadMore?.(before, applied);
			if (page) list = appendPage(list, page);
			// A page that did not arrive STOPS the automatic paging below, or the sentinel is still on
			// screen, `more` is still true, and the effect asks again the instant `loadingMore` clears —
			// a failing endpoint hammered in a tight loop from every reader's browser at once.
			autoPaused = !page;
		} finally {
			// A failed page keeps the rows on screen, leaves `more` alone and re-enables the button — the
			// failure IDIOM of this surface: retry is asking for the same thing again.
			loadingMore = false;
		}
	}

	// ---- reaching the bottom loads the next page -------------------------------------------------
	// A trail is READ BY SCROLLING, so a button at the bottom is a stop sign in the middle of the one
	// gesture the surface is used with. The affordance stays — as the no-observer fallback, as the way
	// back from a failed page, and as a keyboard target — but reaching it is enough.
	//
	// Driven by an EFFECT over visibility rather than by the observer's callback, which is what makes
	// a short page work: an IntersectionObserver fires on a CHANGE of intersection, so a page whose
	// rows do not push the sentinel off screen would never fire again and the list would stall.
	let sentinelVisible = $state(false);
	let autoPaused = $state(false);

	// Armed by SCROLLING, disarmed by a row opening. Visibility alone is the wrong trigger:
	// expanding a row moves everything below it, so the sentinel can enter the band with the reader
	// sitting still — and the page it then fetched was 50 more rows and a full re-render, which is
	// the lag spike that follows a click. Left armed after a page lands, so a page too short to push
	// the sentinel off screen still chains.
	let armed = $state(true);

	function sentinelChanged(visible: boolean) {
		sentinelVisible = visible;
		// Scrolling AWAY and coming back is the reader asking again — the cheapest possible retry for
		// a page that failed, and it costs one request rather than a loop of them.
		if (!visible) {
			autoPaused = false;
			armed = true;
		}
	}

	// Capture phase: a scroll event does not bubble, and this surface scrolls its own box from `md`
	// up and the PAGE below that, so there is no one element to listen on.
	$effect(() => {
		if (typeof window === 'undefined') return;
		const rearm = () => (armed = true);
		window.addEventListener('scroll', rearm, { capture: true, passive: true });
		return () => window.removeEventListener('scroll', rearm, { capture: true });
	});

	$effect(() => {
		if (sentinelVisible && armed && more && !atCap && !loadingMore && !autoPaused) void loadMore();
	});

	// TWO CONTROLS, ONE PARAM. The search box and the event-type dropdown both drive `event_type`, a
	// PREFIX match: the box narrows to a FAMILY (`action.firm-`), the picker names ONE EXACT type. They
	// must never both carry a value, because one param can only answer to one of them and the other
	// would be set-but-silently-ignored — the worst of the three states. So:
	//
	//   * THE PICK WINS (see `snapshot`), and
	//   * whichever control you touch CLEARS the other — picking a type empties the box, typing in the
	//     box drops the picker back to "All events".
	//
	// The pick is NEVER round-tripped through the visible input. That was the reported defect: the
	// dropdown ran in controlled mode setting `query`, so choosing "Sent a message" dumped
	// `message.sent` into the search box for the operator to look at.
	//
	// Which of the two the ENTRY filter lands on is decided the same way: a whole type the vocabulary
	// knows is a PICK, anything else (`action.firm-`, a hand-built fragment) is a search term.
	// Each control seeds from its OWN filter now. There is no longer a rule to apply here: the box is
	// the free-text search and the picker is the event-type list, so an entry filter carrying both is a
	// legitimate state rather than a contradiction to resolve.
	//
	// `filterList`, not `.map`: see its own note — a caller still on the single-string `eventType` shape
	// must not be able to throw here, because a thrown component is a page whose every filter is dead.
	const entry = untrack(() => ({
		picks: filterList(filters.eventType),
		query: filters.search ?? ''
	}));

	// Local control state, seeded from the entry filters once (untrack: props are reactive, and
	// re-seeding on every prop change would fight the operator mid-edit).
	let eventTypePicks = $state<string[]>(entry.picks);
	let query = $state(entry.query);
	let actor = $state<string[]>(untrack(() => filterList(filters.actor)));
	let tenant = $state<string[]>(untrack(() => filterList(filters.tenant)));
	let method = $state<string[]>(untrack(() => filterList(filters.method)));
	let since = $state(untrack(() => filters.since));
	let until = $state(untrack(() => filters.until));
	let proxied = $state<string>(untrack(() => filters.proxied || ALL));
	let selectedTags = $state<string[]>(untrack(() => filterList(filters.tags)));
	// The withheld count. Held separately from `list` because the api computes it on the FIRST page
	// only — a keyset continuation answers 0, and folding that in would make the offer flicker away
	// on "Load more".

	// NOTHING clears anything here any more. The box and the picker were mutually exclusive only
	// because both wrote the same `event_type` param — two controls for one filter, where touching
	// either silently emptied the other. They are separate filters now and compose as AND, so a reader
	// can search a client's name AND narrow to one operation, which is the obvious thing to want.

	// Source is a normal three-way dropdown (like Firm/Actor), not a lone toggle: All, or one side
	// of the proxy. The labels read for whoever is looking — a firm sees "Graphene support" vs its
	// own staff; ops sees the technical proxied/direct split.
	const proxiedOptions = $derived(
		attribution === 'firm'
			? [
					{ value: 'proxied', label: 'Graphene support' },
					{ value: 'direct', label: 'Your firm' }
				]
			: [
					{ value: 'proxied', label: 'Proxied (on a firm’s behalf)' },
					{ value: 'direct', label: 'Direct' }
				]
	);

	const tenantOptions = $derived(tenants.map((t) => ({ value: t.uid, label: t.name })));
	// VALUES stay the HTTP verbs the filter sends; labels are the words the rows use.
	const methodOptions = [
		{ value: 'POST', label: methodLabel('POST') },
		{ value: 'PUT', label: methodLabel('PUT') },
		{ value: 'PATCH', label: methodLabel('PATCH') },
		{ value: 'DELETE', label: methodLabel('DELETE') }
	];

	// THE DROPDOWN VOCABULARIES — the whole vocabulary for the scope, never "the values on screen".
	// Event types and actors come from the endpoint's filter-independent `facets`, UNIONed with what the
	// loaded rows carry (the facets describe a recent window, so a type paged in from behind it is real
	// and must still be pickable); the tag kinds are a closed set known here, so a reader can ask for
	// documents before a document event happens to be loaded. Deriving any of these from the FILTERED
	// answer is the option-shrinking bug: choose one actor and every other actor withdraws from the
	// list, leaving no way back but to clear a filter whose effect you can no longer see.
	const eventTypeOptions = $derived(eventTypeFilterOptions(allEvents, facets?.event_types ?? []));
	const actorOptions = $derived(actorSuggestions(allEvents, facets?.actors ?? []));
	const tagOptions = $derived(entityTagSuggestions(allEvents));

	/** The controls as one filter object — `ALL` mapped back to empty for the one still-single
	 *  control (Source); the event-type PICK takes precedence over the typed family. */
	function snapshot(): ActionLogFilters {
		return {
			tenant: [...tenant],
			eventType: [...eventTypePicks],
			search: query.trim(),
			actor: [...actor],
			method: [...method],
			since,
			until,
			proxied: proxied === ALL ? '' : (proxied as '' | 'proxied' | 'direct'),
			tags: [...selectedTags]
		};
	}

	/** Same SET of values, whatever order they were clicked in: the API ORs firm/actor/event-type/method and ANDs tags. */
	function sameSet(a: readonly string[], b: readonly string[]): boolean {
		if (a.length !== b.length) return false;
		const left = [...a].sort();
		const right = [...b].sort();
		return left.every((tag, i) => tag === right[i]);
	}

	function same(a: ActionLogFilters, b: ActionLogFilters): boolean {
		return (
			sameSet(a.tenant, b.tenant) &&
			sameSet(a.eventType, b.eventType) &&
			a.search === b.search &&
			sameSet(a.actor, b.actor) &&
			sameSet(a.method, b.method) &&
			a.since === b.since &&
			a.until === b.until &&
			a.proxied === b.proxied &&
			sameSet(a.tags, b.tags)
		);
	}

	const filterDefs = $derived(
		eventStreamFilterDefs({
			showTenantFilter,
			tenantOptions,
			eventTypeOptions,
			tagOptions,
			actorOptions,
			proxiedOptions,
			methodOptions
		})
	);

	const barValue = $derived<Record<string, string[]>>({
		...(tenant.length ? { tenant } : {}),
		...(eventTypePicks.length ? { event_type: eventTypePicks } : {}),
		...(selectedTags.length ? { tags: selectedTags } : {}),
		...(actor.length ? { actor } : {}),
		...(proxied !== ALL ? { proxied: [proxied] } : {}),
		...(method.length ? { method } : {}),
		...(since || until ? { date: [since, until] } : {})
	});

	/** One writer, because the bar can change several at once (Reset clears the lot). */
	function setBar(next: Record<string, string[]>): void {
		tenant = next.tenant ?? [];
		eventTypePicks = next.event_type ?? [];
		selectedTags = next.tags ?? [];
		actor = next.actor ?? [];
		proxied = next.proxied?.[0] ?? ALL;
		method = next.method ?? [];
		since = next.date?.[0] ?? '';
		until = next.date?.[1] ?? '';
	}

	const activeFilters = $derived(snapshot());
	// Clear appears whenever anything is off its default. Both event-type controls are named
	// explicitly: `snapshot` folds only the winning one into `eventType`, so in the instant between a
	// pick and the box being cleared the losing control is invisible to `same`.
	const dirty = $derived(
		!same(activeFilters, EMPTY_ACTION_LOG_FILTERS) ||
			eventTypePicks.length > 0 ||
			query.trim() !== ''
	);

	// THE APPLIED FILTERS: the question the rows on screen answer. Page one arrived at the entry
	// filters, so that is where this starts; it moves only when a fetch SUCCEEDS. A failed apply
	// deliberately leaves it alone — the rows are still the old answer, so "Load more" must keep
	// paging the old question.
	let applied = $state<ActionLogFilters>(untrack(() => snapshot()));
	let applying = $state(false);
	let applyError = $state(false);
	// Created ONCE (untrack), so its generation count survives every apply — a fresh applier per change
	// would reset the count and let a stale answer through. The closure reads the CURRENT prop, so
	// nothing is captured that could go out of date.
	const applyFilters = untrack(() =>
		createFilterApplier(async (next) => (await onFilterApply?.(next)) ?? null)
	);
	// One debounce for EVERY control, held here rather than in the search box: a dropdown change and a
	// keystroke then queue the same way, and moving three filters in one breath costs one request.
	const FILTER_DEBOUNCE_MS = 300;
	let applyTimer: ReturnType<typeof setTimeout> | undefined;

	async function applyNow(next: ActionLogFilters) {
		if (!onFilterApply) return;
		applying = true;
		const result = await applyFilters(next);
		// A stale answer changes NOTHING — not the rows, not the error, not even the busy flag: a newer
		// apply is in flight and owns all three.
		if (result.status === 'stale') return;
		if (result.status === 'applied') {
			applied = next;
			list = seedList(result.page.events, result.page.hasMore);
			// Held rows answered the PREVIOUS question — see the poll's note. The fresh page is the new
			// answer in full, so there is nothing left to offer.
			pending = [];
			applyError = false;
		} else {
			// The rows stay: they still answer the last APPLIED filters, and `applied` is left alone with
			// them so "Load more" keeps paging the question on screen.
			applyError = true;
		}
		applying = false;
	}

	// Fetch when the controls settle. `applied` is read untracked, so only a real control change can
	// schedule a fetch; the cleanup cancels a pending one, which is the debounce.
	$effect(() => {
		const next = activeFilters;
		if (!onFilterApply) return;
		if (untrack(() => same(next, applied))) return;
		applyTimer = setTimeout(() => void applyNow(next), FILTER_DEBOUNCE_MS);
		return () => clearTimeout(applyTimer);
	});

	/** Retry a failed apply: the same request, now, with no debounce — the operator has already
	 *  waited. The rows on screen stayed put, so this is the load-more idiom (ask again) rather than a
	 *  second failure vocabulary. */
	function retryApply() {
		clearTimeout(applyTimer);
		void applyNow(activeFilters);
	}

	/* ── The live poll ───────────────────────────────────────────────────────────────────────────
	   The feed had no freshness at all: it was whatever the loader read, until someone pressed Refresh.
	   On a page whose entire job is "what is happening", that is the wrong default.

	   IT NEVER MOVES THE ROWS ON ITS OWN. New events are held and OFFERED — "3 new events · Show" —
	   because this is a reader, not a ticker: auto-prepending re-ranks the table under the eye that is
	   scanning it, and with a row expanded it pushes the detail somebody is mid-way through reading
	   down the page by however many events happened to land. Held rows are shown only when the reader
	   asks, and `prependNew` then keeps every page they have already loaded.

	   The held rows are DISCARDED whenever the question changes — an applied filter, a re-seed, a
	   refresh — because they were fetched to answer the old one. Keeping them would mix rows from two
	   different filter sets into one table, which is a wrong answer that looks like data.

	   The timer is the shared `createPollController` (the notification bell's, unit-tested for the
	   failure that matters here too: a missed resume leaves the feed permanently stale while looking
	   live). Hidden tab ⇒ paused; visible again ⇒ an immediate tick, so coming back is instant. */
	const POLL_INTERVAL_MS = 20000;
	let pending = $state<ActionEvent[]>([]);
	const pendingCount = $derived(pending.length);

	/** The high-water mark the poll asks after: the NEWEST id anywhere in play — held rows included, so
	 *  a reader who ignores the pill for ten minutes accumulates each new event once rather than being
	 *  re-served the same ones every tick. Null on an empty feed, which is why the poll waits for a
	 *  first row rather than inventing a cursor (`after=0` would ask for the entire stream). */
	const pollAfter = $derived(Math.max(pending[0]?.id ?? 0, allEvents[0]?.id ?? 0) || null);

	async function pollTick() {
		if (!onPollNewer) return;
		const after = untrack(() => pollAfter);
		if (after == null) return;
		// At the APPLIED filters, exactly like `loadMore` — the count offered must be a count of rows
		// that will actually appear.
		const asked = untrack(() => applied);
		let rows: ActionEvent[] | null = null;
		try {
			rows = await onPollNewer(after, asked);
		} catch {
			// A failed poll is silent by design: the reader did not ask for it, so it must not produce an
			// error banner. The next tick recovers.
			return;
		}
		if (!rows?.length) return;
		// The question must not have changed WHILE we asked. Without this the answer to the previous
		// filters lands in the pill under the new ones.
		if (untrack(() => !same(applied, asked))) return;
		// Newest first, and never anything already on screen or already held: `after` should make both
		// impossible, but a poll racing a "Load more" can overlap and a duplicate id breaks the keyed
		// `{#each}` as well as the count.
		const known = new Set([...allEvents, ...pending].map((e) => e.id));
		const fresh = rows.filter((e) => !known.has(e.id));
		if (fresh.length > 0) pending = [...fresh, ...pending];
	}

	/** Show what the poll has held: the rows go to the top, every loaded page is kept, and the pill
	 *  clears. The reader asked, so this is the one path allowed to move the table. */
	function showPending() {
		if (pending.length === 0) return;
		list = prependNew(list, pending);
		pending = [];
	}

	$effect(() => {
		if (!onPollNewer || typeof document === 'undefined') return;
		const poller = createPollController(POLL_INTERVAL_MS, () => void pollTick());
		const onVisibility = () => (document.hidden ? poller.hidden() : poller.visible());
		const onFocus = () => poller.visible();
		if (!document.hidden) poller.start();
		document.addEventListener('visibilitychange', onVisibility);
		window.addEventListener('focus', onFocus);
		return () => {
			poller.stop();
			document.removeEventListener('visibilitychange', onVisibility);
			window.removeEventListener('focus', onFocus);
		};
	});

	// THE ROWS. With a fetcher, the API has already applied the filters and the rows ARE the answer —
	// re-running the matchers over them would narrow a second time on slightly different rules (see
	// `matchesTags`, which reads resolved entities where the API tests captured keys) and could hide
	// rows the API rightly returned. Without one, the same controls narrow the loaded rows, which is
	// what an unwired mount gets: you filter what has loaded, and "Load more" is what widens it.
	const visibleEvents = $derived(
		onFilterApply ? allEvents : allEvents.filter((e) => matchesAllFilters(e, activeFilters))
	);

	// One clock per render keeps the relative hints on a page mutually consistent.
	const now = new Date();

	// No "which row is open" derivation is needed beyond the id: `DataTable` asks `isExpanded`
	// against the rows it is actually rendering, so an id left over from a previous question opens
	// nothing rather than opening the wrong row.

	// Row-level derivations the former `event-row.svelte` owned, now plain functions of an event: the
	// table's `cell` snippet and a couple of `sortValue`s both need them, and neither is a component
	// with its own `$derived` any more.
	const CATEGORY_ICONS: Record<ActionCategory, typeof UserIcon> = {
		people: UserIcon,
		roles: ShieldIcon,
		documents: FileTextIcon,
		money: BanknoteIcon,
		tasks: ListChecksIcon,
		flows: WorkflowIcon,
		screening: ShieldAlertIcon,
		security: KeyRoundIcon,
		settings: SettingsIcon
	};
	const isProxiedEvent = (e: ActionEvent) => e.target_tenant_id !== null;
	const rowFirmName = (e: ActionEvent) =>
		isProxiedEvent(e) ? e.target_tenant_name : (e.tenant_name ?? null);
	const rowFirmUid = (e: ActionEvent) => (isProxiedEvent(e) ? e.target_tenant_uid : null);
	const rowFirmIcon = (e: ActionEvent) =>
		isProxiedEvent(e) ? e.target_tenant_icon_url : (e.tenant_icon_url ?? null);
	// The action is the sentence minus the actor (WHO) — a leading space (verb-led) or " · " (the
	// de-slugged fallback) is trimmed so the column reads cleanly.
	const actionText = (e: ActionEvent) => headline(e).rest.replace(/^\s*·?\s*/, '');
	const methodClass = (e: ActionEvent) =>
		e.method === 'DELETE'
			? 'text-destructive'
			: e.method === 'POST'
				? 'text-success-strong'
				: 'text-muted-foreground';

	// Every heading but the leading glyph is clickable. Who and Action are DERIVED — the row holds
	// the raw event, not the split sentence or the humanised text — so both need a `sortValue`
	// rather than a bare field read; When sorts on the event's own `created_at`, which is already
	// the raw ISO value the cell only reformats for display.
	//
	// THE SHARES ARE PERCENTAGES NOW, because the table has the whole card rather than 58% of it. A
	// fixed `12rem` under `table-fixed` stayed 192px at every width, so every pixel the pane gained
	// went to Action alone — the column that truncates most gracefully and needed it least. The glyph
	// column keeps a PIXEL width: it holds one 24px disc, and a percentage would collapse it on a
	// narrow pane.
	//
	// NO OUTCOME BADGE. The middleware writes a row only after a 2xx, so "Succeeded" was the answer
	// on 99.7% of rows and the column said the same word 5,352 times. The only exceptions are
	// `authentication.failed`, whose event type already says it failed. A column that is constant
	// is not a column.
	const eventColumns: DataTableColumn<ActionEvent>[] = [
		{ key: 'category', header: '', headerClass: 'bg-muted/40 h-9 w-10', cellClass: 'relative' },
		{
			key: 'who',
			header: 'Who',
			sortable: true,
			headerClass: 'bg-muted/40 h-9 w-[15rem] text-[11px] tracking-wide',
			sortValue: (e) => headline(e).actor
		},
		{
			key: 'action',
			header: 'Action',
			sortable: true,
			headerClass: 'bg-muted/40 h-9 text-[11px] tracking-wide',
			sortValue: (e) => actionText(e)
		},
		{
			key: 'when',
			header: 'When',
			sortable: true,
			defaultDir: 'desc',
			// Default sort column: a log reads newest-first, not by identity.
			sortDefault: true,
			headerClass: 'bg-muted/40 h-9 w-[13rem] text-[11px] tracking-wide',
			sortValue: (e) => e.created_at
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in the
	// template is recreated on every update, stamping the reader's chosen column back to the default.
	let eventSort = $state<DataTableSort | null>(null);

	let refreshing = $state(false);
	/**
	 * Refresh: ask the SAME question again, at the filters that are on screen.
	 *
	 * THE BUG THIS FIXES. It used to call `onRefresh` alone — `invalidateAll()`, which re-runs the
	 * loader, and the loader deliberately asks the UNFILTERED first page. So the controls kept their
	 * values while the rows underneath them stopped answering to them: a filtered view silently became
	 * an unfiltered one under a toolbar that still said otherwise. Nothing looked wrong, which is what
	 * made it bad — the reader has no way to tell a filter that matches everything from one that is no
	 * longer applied.
	 *
	 * So with filters set, Refresh is an APPLY (one round trip, fresh rows at those filters, and the
	 * dropdown vocabulary is left alone because it is filter-independent by design). With nothing
	 * filtered it is the loader re-run, which is the same rows PLUS a fresh vocabulary.
	 */
	async function refresh() {
		if (refreshing) return;
		refreshing = true;
		try {
			const next = activeFilters;
			if (onFilterApply && !same(next, EMPTY_ACTION_LOG_FILTERS)) {
				// Cancel any debounced apply first, then ask now. `applyNow` has no "already applied"
				// short-circuit of its own (that check lives in the effect), so re-asking an identical
				// question really does re-ask it — which is the whole point of a refresh.
				clearTimeout(applyTimer);
				await applyNow(next);
			} else {
				await onRefresh?.();
			}
		} finally {
			refreshing = false;
		}
	}

	function clearAll() {
		query = '';
		eventTypePicks = [];
		actor = [];
		tenant = [];
		method = [];
		since = '';
		until = '';
		proxied = ALL;
		selectedTags = [];
	}
</script>

{#snippet retryRead()}
	<!-- The copy said "retry above" and pointed at a control the reader has to go and find, on the
	     one state where nothing else on screen is worth reading. Same refresh the toolbar runs —
	     and gated on `onRefresh` like its sibling in `message-stream`, because `refresh()` falls
	     through to `await onRefresh?.()` and a mount that passes none would get a button that spins
	     and changes nothing. Every current mount passes one; the gate is what keeps that true. -->
	<Button variant="outline" size="sm" disabled={refreshing} onclick={refresh}>
		{refreshing ? 'Retrying…' : 'Try again'}
	</Button>
{/snippet}

{#snippet clearEventFilters()}
	<Button variant="outline" size="sm" onclick={clearAll}>Clear filters</Button>
{/snippet}

{#snippet firmIcon(src: string | null | undefined, name: string | null | undefined)}
	<!-- The firm's branding icon; the name always renders beside it, so the image is decorative and
	     keeps the firm name as its hover title. Absent icon ⇒ nothing (the name-only chip fallback,
	     pinned in the SSR tests). -->
	{#if src}
		<img
			{src}
			alt=""
			title={name ?? undefined}
			class="size-3.5 shrink-0 rounded-[3px] object-contain"
			loading="lazy"
		/>
	{/if}
{/snippet}

<Card glass class="flex h-full min-h-0 min-w-0 flex-col">
	<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
		<div class="shrink-0">
			<!-- Refresh re-asks at the filters on screen (see `refresh`). -->
			<ListingToolbar
				bind:query
				placeholder="Search a client, a person, a document…"
				{dirty}
				onclear={clearAll}
				onrefresh={onRefresh ? refresh : undefined}
				{refreshing}
				{filterDefs}
				filterValue={barValue}
				onfilters={setBar}
				{actions}
			/>

			{#if applyError}
				<!-- A failed apply keeps the ROWS on screen (they still answer the last applied filters) and
				     offers the same request again — the load-more idiom, not a second failure vocabulary. -->
				<div
					class="text-muted-foreground mt-2 flex items-center gap-2 text-xs"
					role="status"
					aria-live="polite"
				>
					<TriangleAlertIcon class="text-warning size-3.5" />
					<span>Couldn’t apply these filters — showing the previous results.</span>
					<Button
						variant="secondary"
						size="sm"
						class="h-7"
						disabled={applying}
						onclick={retryApply}
					>
						Retry
					</Button>
				</div>
			{:else if applying}
				<p class="text-muted-foreground mt-2 text-xs" role="status" aria-live="polite">
					Applying filters…
				</p>
			{:else if pendingCount > 0}
				<!-- What the poll has found, OFFERED rather than applied. A count and a button: prepending on
				     its own would re-rank the table under the reader's eye and push an open row's detail down
				     the page mid-read. `aria-live="polite"` announces the count without interrupting, which is
				     the whole posture of this control. -->
				<div class="mt-2 flex items-center gap-2 text-xs" role="status" aria-live="polite">
					<span class="bg-primary size-1.5 shrink-0 animate-pulse rounded-full"></span>
					<span class="text-muted-foreground">
						{pendingCount === 1 ? '1 new event' : `${pendingCount} new events`}
					</span>
					<Button variant="secondary" size="sm" class="h-7" onclick={showPending}>Show</Button>
				</div>
			{/if}
		</div>

		{#if allEvents.length === 0}
			<div class="flex-1">
				<!-- Nothing to show. A failed read wins: it must never read as a definitive empty trail (the
				     page's own retry banner sits above). With filters set this is now a statement about the
				     WHOLE trail, not about a loaded page — so clearing them really is the next step. -->
				<EmptyState
					compact
					tone={loadError ? 'warning' : 'neutral'}
					icon={loadError ? TriangleAlertIcon : ActivityIcon}
					title={loadError
						? 'Couldn’t load events'
						: dirty
							? 'No events match these filters'
							: 'No actions recorded yet'}
					description={loadError
						? 'The event log couldn’t be loaded.'
						: dirty
							? 'Try a wider date range, or clear the filters to see the whole trail.'
							: 'Every successful portal action lands here as it happens.'}
					action={loadError
						? onRefresh
							? retryRead
							: undefined
						: dirty
							? clearEventFilters
							: undefined}
				/>
			</div>
		{:else}
			<!-- ONE COLUMN, FULL WIDTH: the table fills the card and the open event unfolds underneath its
			     own row. It fills the remaining height inside the card and only this pane scrolls — the
			     page itself never does. -->
			<div class="flex min-w-0 flex-col md:min-h-0 md:flex-1 md:overflow-hidden">
				<div class="overflow-x-auto md:min-h-0 md:flex-1 md:overflow-y-auto">
					<!-- `fixed`, so the Action cell is bounded and its text can truncate instead of
					     forcing the table wider than the pane; the header classes carry each column's
					     share. The row is the disclosure (`onRowClick` toggles, `isExpanded` says which
					     one is open) — only reachable on the client-side fallback (a fetched answer with
					     no rows empties `allEvents` and shows the empty state instead): the filters hid
					     every loaded row, so keep the table and its Load-more affordance, which is the
					     only thing that widens what a client-side filter can find. -->
					<DataTable
						columns={eventColumns}
						rows={visibleEvents}
						bind:sort={eventSort}
						getKey={(e) => e.id}
						label="Events"
						fixed
						onRowClick={toggleRow}
						isExpanded={(e) => expandedId === e.id}
						rowClass={() => 'group aria-expanded:bg-muted/60'}
						empty="No loaded events match these filters."
					>
						{#snippet cell(e, column)}
							{#if column.key === 'category'}
								<span
									class="bg-primary absolute inset-y-0 left-0 w-0.5 opacity-0 group-aria-expanded:opacity-100"
									aria-hidden="true"
								></span>
								{@const CategoryIcon = CATEGORY_ICONS[eventCategory(e.event_type)]}
								<span
									class="bg-muted text-muted-foreground flex size-6 items-center justify-center rounded-full"
									data-category={eventCategory(e.event_type)}
									aria-hidden="true"
								>
									<CategoryIcon class="size-3.5" />
								</span>
							{:else if column.key === 'who'}
								{@const led = headline(e)}
								<span class="flex items-center gap-2 overflow-hidden">
									<!-- The actor's identity colour, from the SAME rule as every other avatar in
									     the product: a hue rotated off the firm's brand and keyed on the name, so
									     one colleague is one colour wherever you meet them. `PersonAvatar`, not
									     `ClientAvatar` — an actor is staff, and the muted client disc would make every
									     row of this feed the same grey face. -->
									<PersonAvatar
										name={led.actor}
										initials={initialsOf(led.actor)}
										class="size-6 text-[10px] font-medium"
									/>
									<span class="min-w-0 truncate text-sm font-medium" title={led.actor}>
										{led.actor}
									</span>
									<!-- The request method sits with WHO acted (read vs write vs delete at a
									     glance), as a small toned pill — the meaning is in the text, the colour is
									     only a hint. -->
									<!-- The verb in English. `POST` beside a person's name told the reader which HTTP
									     method carried the change, which is not a fact about the firm's book; the raw
									     method stays in the title for anyone who wants it. -->
									<span
										class="bg-muted {methodClass(
											e
										)} shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium"
										title="HTTP {e.method}">{methodLabel(e.method)}</span
									>
								</span>
							{:else if column.key === 'action'}
								<!-- The action text truncates (min-w-0 + truncate) so a long sentence never
								     widens the table; the type + firm chips are shrink-0, and any excess is
								     clipped, never scrolled. -->
								<div class="flex items-center gap-2 overflow-hidden">
									<span class="min-w-0 truncate" title={actionText(e)}>{actionText(e)}</span>
									<code
										class="bg-muted text-muted-foreground max-w-[11rem] shrink-0 truncate rounded px-1.5 py-0.5 font-mono text-[11px]"
										title={e.event_type}>{e.event_type}</code
									>
									{#if isProxiedEvent(e)}
										<!-- THE CHIP ANSWERS "WHO", NOT "ON WHAT" — see event-row's own note on this
										     wording, kept verbatim here. -->
										<span class="shrink-0">
											<StatusBadge tone="warning">
												<TriangleAlertIcon class="size-3" />
												{@render firmIcon(e.tenant_icon_url, e.tenant_name)}
												Graphene support
											</StatusBadge>
										</span>
									{/if}
									{#if attribution === 'operations' && (rowFirmName(e) || rowFirmUid(e))}
										<!-- WHICH FIRM the row is about: the proxied TARGET where there is one, else
										     the acting tenant — never the operations tenant the operator signed in
										     to, which would be the same value on every proxied row. -->
										<span
											class="border-border bg-muted inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px]"
										>
											{@render firmIcon(rowFirmIcon(e), rowFirmName(e))}
											{rowFirmName(e) ?? rowFirmUid(e)}
										</span>
									{/if}
									{#if e.metadata?.break_glass}
										<span class="shrink-0"
											><StatusBadge tone="danger">Break-glass</StatusBadge></span
										>
									{/if}
								</div>
							{:else if column.key === 'when'}
								{@const stamp = timestampParts(e.created_at, now)}
								<!-- Left-aligned so the time sits directly under its "When" heading (the relative
								     hint + full ISO live in the detail's Metadata / the title). -->
								<span class="flex items-center gap-2">
									<span class="text-muted-foreground text-sm tabular-nums" title={e.created_at}>
										{stamp.absolute}
									</span>
									<!-- THE DISCLOSURE AFFORDANCE, at the trailing edge of the row where the eye
									     finishes it. Without it a row that expands looks exactly like a row that does
									     not, and the only way to find out is to click — which on a compliance trail is
									     a thing readers avoid doing. It is `aria-hidden`: the row itself carries
									     `aria-expanded`/`aria-controls` (DataTable), so a screen reader is already
									     told, and announcing a second control here would invent one that does not
									     exist. -->
									<ChevronDownIcon
										class="text-muted-foreground ml-auto size-4 shrink-0 transition-transform group-aria-expanded:rotate-180"
										aria-hidden="true"
									/>
								</span>
							{/if}
						{/snippet}

						{#snippet detail(e)}
							<!-- The whole event, under its own row. No `{#key}`: the block only exists while that
							     row is open, so opening another destroys this one and builds the other — the
							     remount the side pane needed a key for is structural here. -->
							<EventDetail event={e} {links} {entityHref} {now} variant="inline" />
						{/snippet}
					</DataTable>
					<!-- Load-more / end-marker live INSIDE the scroll, right after the last row — the
					     place the next event would be — so they are reached by scrolling to the bottom,
					     not pinned as a footer over the list. "Load more" APPENDS the next keyset page in
					     place (no navigation, no URL change); the URL never carries a growing limit. -->
					{#if more && !atCap}
						<!-- The SENTINEL: reaching this loads the next page (`whenVisible` + the effect above),
						     so the reader never has to stop scrolling to press anything. The button stays and
						     is not decoration — it is the fallback where there is no IntersectionObserver, the
						     way back from a page that failed, and the only thing a keyboard can reach. -->
						<div
							class="flex justify-center border-t px-4 py-3"
							use:whenVisible={{ onchange: sentinelChanged }}
						>
							<Button variant="secondary" size="sm" disabled={loadingMore} onclick={loadMore}>
								{loadingMore ? 'Loading…' : 'Load more'}
								<ChevronDownIcon class="size-4" />
							</Button>
						</div>
					{:else if more && atCap}
						<!-- Hit the in-place cap but more remains. Narrowing IS the way further back now: a
						     filter is a query, so it reaches the whole trail rather than these rows. -->
						<div class="text-muted-foreground border-t px-4 py-3 text-center text-xs">
							Showing the most recent {MAX_EVENTS.toLocaleString()} events — narrow the filters to reach
							further back.
						</div>
					{:else}
						<!-- Nothing more to load: the whole trail is on screen. A quiet end-marker so the
						     list has a definite bottom rather than just stopping. -->
						<div
							class="text-muted-foreground flex items-center justify-center gap-3 border-t px-4 py-3 text-xs"
						>
							<span class="bg-border h-px w-8" aria-hidden="true"></span>
							End of events
							<span class="bg-border h-px w-8" aria-hidden="true"></span>
						</div>
					{/if}
				</div>
			</div>
		{/if}
	</CardContent>
</Card>
