<script module lang="ts">
	import type { FilterDef } from '../book/listing/filter-panel.svelte';
	import { humanise } from '../book/listing/filters.js';
	import { categoryMeta } from './describe.js';

	/** The full question `onquery` hands the app; every key always present, so cleared != unchanged. */
	export type NotificationQuery = {
		q: string;
		category: string[];
		priority: string[];
		state: string[];
		since: string;
		until: string;
	};

	/** A pure function so an SSR test can reach it — a shut popover renders nothing server-side. */
	export function notificationFilterDefs({
		facets = {}
	}: { facets?: Record<string, string[]> } = {}): FilterDef[] {
		return [
			{
				key: 'state',
				label: 'State',
				options: [
					{ value: 'unread', label: 'Unread' },
					{ value: 'read', label: 'Read' },
					{ value: 'dismissed', label: 'Dismissed' }
				]
			},
			{
				key: 'category',
				label: 'Category',
				options: (facets.category ?? []).map((value) => ({
					value,
					label:
						categoryMeta(value).label === 'Update' ? humanise(value) : categoryMeta(value).label
				}))
			},
			{
				key: 'priority',
				label: 'Priority',
				options: (facets.priority ?? []).map((value) => ({ value, label: humanise(value) }))
			},
			{
				// No min/max: `NotificationPage` carries facets but no date bounds.
				key: 'received',
				label: 'Received',
				options: [],
				kind: 'daterange'
			}
		];
	}
</script>

<script lang="ts">
	// The full-page notification feed (the `/notifications` route in each portal mounts this). Same
	// rows as the bell, with a mark-all control and keyset "load more". Loading + the initial page
	// stay app-side; this takes typed props and injected callbacks, the book-surface idiom.
	import { untrack } from 'svelte';
	import BellIcon from '@lucide/svelte/icons/bell';
	import CheckCheckIcon from '@lucide/svelte/icons/check-check';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import { Button } from '../ui/button/index.js';
	import { PAGE_SIZE, manyFilters, oneFilter } from '../book/listing/query.js';
	import { ListingToolbar } from '../book/listing/index.js';
	import NotificationItemRow from './notification-item.svelte';
	import { groupNotificationsByDay } from './day-groups.js';
	import { settleWrite, type WriteOutcome } from './write-outcome.js';
	import type { NotificationItem } from './describe.js';

	let {
		items = [],
		hasMore = false,
		page = 1,
		previousHref,
		nextHref,
		backHref,
		onMarkRead,
		onMarkAllRead,
		query = '',
		filters = {},
		facets = {},
		onquery
	}: {
		items?: NotificationItem[];
		hasMore?: boolean;
		page?: number;
		previousHref?: string;
		nextHref?: string;
		/** Where "Back" goes. Given ⇒ a link to that route; omitted ⇒ `history.back()` (the feed is
		 *  a standalone top-level route reached from the bell, so it has no sidebar to return from). */
		backHref?: string;
		onMarkRead?: (uid: string) => WriteOutcome;
		onMarkAllRead?: () => WriteOutcome;
		/** The search term in force, read from the URL by the app's loader. */
		query?: string;
		/** `category`/`priority`/`state` take a string or list; `since`/`until` are the received range's two bounds. */
		filters?: Record<string, string | string[]>;
		facets?: Record<string, string[]>;
		/**
		 * Present ⇒ the toolbar is shown and the app owns the navigation. Absent ⇒ no toolbar at
		 * all, rather than one that filters a single page and calls it the archive.
		 */
		onquery?: (next: NotificationQuery) => void;
	} = $props();

	let list = $state<NotificationItem[]>(untrack(() => items));
	$effect(() => {
		list = items;
	});

	const anyUnread = $derived(list.some((n) => n.read_at === null));

	const stateValues = $derived(manyFilters(filters.state));
	const categoryValues = $derived(manyFilters(filters.category));
	const priorityValues = $derived(manyFilters(filters.priority));
	const since = $derived(oneFilter(filters.since) ?? '');
	const until = $derived(oneFilter(filters.until) ?? '');

	/** One writer, so changing a filter cannot silently drop the search term beside it. */
	function change(patch: Partial<NotificationQuery>) {
		onquery?.({
			q: query,
			category: categoryValues,
			priority: priorityValues,
			state: stateValues,
			since,
			until,
			...patch
		});
	}

	const filterDefs = $derived(notificationFilterDefs({ facets }));

	// A key is absent entirely rather than present-and-empty, or the badge would show with no chip under it.
	const barValue = $derived<Record<string, string[]>>({
		...(stateValues.length ? { state: stateValues } : {}),
		...(categoryValues.length ? { category: categoryValues } : {}),
		...(priorityValues.length ? { priority: priorityValues } : {}),
		// Both slots travel even when one is empty — dropping a blank `since` would read as "from until", not "up to until".
		...(since || until ? { received: [since, until] } : {})
	});

	function applyFilters(next: Record<string, string[]>): void {
		const range = next.received ?? [];
		change({
			state: next.state ?? [],
			category: next.category ?? [],
			priority: next.priority ?? [],
			since: range[0] ?? '',
			until: range[1] ?? ''
		});
	}

	function clearAll(): void {
		change({ q: '', category: [], priority: [], state: [], since: '', until: '' });
	}

	const dirty = $derived(Object.keys(barValue).length > 0);
	const first = $derived((page - 1) * PAGE_SIZE + 1);
	const last = $derived(first + list.length - 1);
	// One page's worth of rows only (see the `$effect` above — a new page REPLACES `list`, it never
	// appends), so this is always computed fresh for whatever is currently on screen. A day that
	// straddles the offset boundary just gets its heading again at the top of the next page.
	const dayGroups = $derived(groupNotificationsByDay(list));

	// Optimistic, and REVERTED if the app reports the write did not land (`write-outcome.ts`): the
	// archive has no refetch seam of its own, so a failed write puts the row(s) back as they were
	// rather than leaving the page claiming a read the server never recorded.
	function markRead(uid: string) {
		const before = list.find((n) => n.uid === uid);
		list = list.map((n) => (n.uid === uid ? { ...n, read_at: new Date().toISOString() } : n));
		void settleWrite(onMarkRead?.(uid), () => {
			if (before) list = list.map((n) => (n.uid === uid ? before : n));
		});
	}
	function markAll() {
		const before = list;
		const stamp = new Date().toISOString();
		list = list.map((n) => (n.read_at ? n : { ...n, read_at: stamp }));
		void settleWrite(onMarkAllRead?.(), () => {
			list = before;
		});
	}
</script>

<div class="w-full">
	<div class="mb-3 flex items-center gap-3">
		{#if backHref}
			<Button variant="ghost" size="sm" href={backHref} class="-ml-2 gap-1.5">
				<ArrowLeftIcon class="size-4" />
				Back
			</Button>
		{:else}
			<Button variant="ghost" size="sm" onclick={() => history.back()} class="-ml-2 gap-1.5">
				<ArrowLeftIcon class="size-4" />
				Back
			</Button>
		{/if}
		<h1 class="text-lg font-semibold">Notifications</h1>
		{#if anyUnread}
			<Button variant="ghost" size="sm" onclick={markAll} class="ml-auto gap-1.5">
				<CheckCheckIcon class="size-4" />
				Mark all read
			</Button>
		{/if}
	</div>

	{#if onquery}
		<!-- Server-driven, all of it. The archive keeps every notification a login has ever had and
		     shows 25 at a time, so a control that narrowed the page would answer for the page. -->
		<ListingToolbar
			class="mb-3"
			{query}
			placeholder="Search notifications"
			{dirty}
			onclear={clearAll}
			onsearch={(term) => change({ q: term })}
			{filterDefs}
			filterValue={barValue}
			onfilters={applyFilters}
		/>
	{/if}

	{#if list.length === 0}
		<div
			class="text-muted-foreground flex flex-col items-center gap-3 rounded-lg border border-dashed px-6 py-16 text-center"
		>
			<BellIcon class="size-8 opacity-40" />
			<div>
				<p class="text-foreground text-sm font-medium">
					{query || dirty ? 'Nothing matches' : 'Nothing here yet'}
				</p>
				<p class="mt-1 text-sm">
					{query || dirty
						? 'Try a different search, or widen the filters.'
						: 'Notifications about your work will appear here.'}
				</p>
			</div>
		</div>
	{:else}
		<!-- A muted GROUND with the rows as cards on it, the way Slack separates its activity
		     items — not one card wrapped round a continuous list. Boxing the rows is what makes two
		     notifications read as two things; flush against each other they ran together into a
		     column of text that had to be parsed rather than counted. The ground is also what the
		     sticky day pills sit against as rows scroll under them.

		     NO inner scroller. This list used to cap itself at a viewport-derived height and scroll
		     inside that, which on a standalone page put a second scrollbar down the right beside
		     the window's own — two tracks, and neither one obviously the page's. The page scrolls;
		     the pills stick to the viewport instead, clearing the topbar with `--topbar-h` (the
		     shell declares it and this renders inside the shell, so it resolves here). A caller
		     that ever needs this list boxed into a fixed panel should say so with a prop rather
		     than have every page pay for it. -->
		<div class="bg-muted/40 rounded-lg border">
			<div class="space-y-2 p-3">
				<!-- Keyed by index, not the heading text: grouping is consecutive-run only (see
				     `groupNotificationsByDay`), so a genuinely out-of-order page repeats a heading rather
				     than merging it, and a repeated heading must not collide as a Svelte key. -->
				{#each dayGroups as group, gi (gi)}
					<!-- Slack's divider: a centred pill on a rule, and STICKY, so the day you are
					     reading stays named as you scroll past a long one. `z-10` and an opaque pill
					     because rows pass underneath it, and `top-0` works because the scroll
					     container immediately above is the positioning context. -->
					<div
						class="sticky top-[var(--topbar-h,4rem)] z-10 -mx-3 flex items-center gap-3 px-3 py-2"
					>
						<span class="border-border/70 h-px flex-1 border-t"></span>
						<span
							class="border-border/70 bg-card text-muted-foreground rounded-full border px-2.5 py-0.5 text-xs font-medium shadow-sm"
						>
							{group.heading}
						</span>
						<span class="border-border/70 h-px flex-1 border-t"></span>
					</div>
					{#each group.items as item (item.uid)}
						<!-- No onDismiss here: this feed is the archive now — it shows read AND dismissed
						     rows, nothing hides a row from it — so a × that only unhooked the row from the
						     BELL while leaving it right here would look like it had done nothing. Dismiss
						     stays a bell-only action (see the layouts' own NotificationBell wiring). -->
						<NotificationItemRow {item} onMarkRead={markRead} dayGrouped boxed />
					{/each}
				{/each}
			</div>
		</div>
		<div class="mt-3 flex items-center justify-between gap-3">
			<p class="text-muted-foreground text-xs tabular-nums" aria-live="polite">
				{first}–{last}
			</p>
			<div class="flex items-center gap-2">
				<Button variant="outline" size="sm" href={previousHref} disabled={!previousHref}>
					Previous
				</Button>
				<Button variant="outline" size="sm" href={nextHref} disabled={!hasMore || !nextHref}>
					Next
				</Button>
			</div>
		</div>
	{/if}
</div>
