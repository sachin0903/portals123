<script lang="ts">
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import BookOpenIcon from '@lucide/svelte/icons/book-open';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import SearchIcon from '@lucide/svelte/icons/search';
	import BrowseBar from './browse-bar.svelte';
	import NewsJournal from './news-journal.svelte';
	import NewsMagazine from './news-magazine.svelte';
	import { KIND_ORDER, KIND_PLURAL, kindOf, stampOf } from './insights.js';
	import type { NewsItem } from './types.js';

	/**
	 * The firm's content library as a page — the `news` block's `library` variant, which the
	 * default Insights tab is. This component owns what every look shares: the kind switch (once
	 * there are two kinds to switch between), the topic tabs and the search button (once the
	 * library outgrows a glance — filtered-listing's eight-row rule, because a search over four
	 * articles reads as a tool for a library the firm has not written), and the two empty states.
	 *
	 * The BODY is the look's. `card` is the reading list: the newest item as the lead (its hero
	 * when it has one, the words alone when it does not), everything else one short row.
	 * `magazine` is picture-led — a cover story and a grid of cover cards, the firm's palette
	 * standing in where it has no photograph (`InsightCover`). `journal` is type-led and
	 * chrome-less — the ledger finish's signature. This page is where a firm's identity has the
	 * most room, so it is the one block whose looks differ this much.
	 */
	let {
		items,
		look = 'card',
		interactive = true
	}: {
		items: NewsItem[];
		/** `card` | `magazine` | `journal` — resolved by `looks.ts`; anything else renders `card`. */
		look?: string;
		interactive?: boolean;
	} = $props();

	const ALL = 'all';
	const SEARCH_MIN_ROWS = 8;

	let kind = $state(ALL);
	let topic = $state(ALL);

	const kinds = $derived.by(() => {
		const present = new Set(items.map(kindOf));
		return KIND_ORDER.filter((k) => present.has(k));
	});
	const kindOptions = $derived([
		{ value: ALL, label: 'All' },
		...kinds.map((k) => ({ value: k, label: KIND_PLURAL[k] }))
	]);

	const topics = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const i of items) counts.set(i.source, (counts.get(i.source) ?? 0) + 1);
		return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([value]) => value);
	});

	const large = $derived(items.length > SEARCH_MIN_ROWS);
	const showKinds = $derived(kinds.length > 1);
	const showTopics = $derived(large && topics.length > 1);
	const showSearch = $derived(large);

	// Topics are the tabs once there are topics to browse; until then the kinds take the tab row.
	const tabsAreTopics = $derived(showTopics);
	const showTabs = $derived(showTopics || showKinds);
	const tabs = $derived(
		tabsAreTopics
			? [{ value: ALL, label: 'All' }, ...topics.map((t) => ({ value: t, label: t }))]
			: kindOptions
	);
	const tabValue = $derived(tabsAreTopics ? topic : kind);
	function pickTab(value: string) {
		if (tabsAreTopics) topic = value;
		else kind = value;
	}
	const narrowed = $derived(kind !== ALL || topic !== ALL);

	const matched = $derived(
		items.filter(
			(i) => (kind === ALL || kindOf(i) === kind) && (topic === ALL || i.source === topic)
		)
	);
	const lead = $derived(matched[0] ?? null);
	const rest = $derived(matched.slice(1));

	// A hero that fails to load (an asset purged from the store, a dead CDN) falls back to the
	// text-only lead rather than a broken-image glyph over a quarter of the page.
	let brokenHero = $state<string | null>(null);
	const showHero = $derived(lead !== null && !!lead.heroUrl && brokenHero !== lead.id);

	function reset() {
		kind = ALL;
		topic = ALL;
	}

	const stamp = stampOf;
</script>

<div class="@container space-y-5" data-news-library data-look={look}>
	{#if showTabs || showSearch}
		<!-- The portal's one browse bar (browse-bar.svelte): the TOPICS are the tabs, the KIND a
		     pull-down beside them, SEARCH a button that opens the portal's palette on insights —
		     the same search as the header's, already scoped. A library with no topics yet tabs by
		     kind instead, and drops the pull-down. -->
		<BrowseBar
			{tabs}
			tab={tabValue}
			ontab={pickTab}
			tabsLabel={tabsAreTopics ? 'Filter by topic' : 'Filter by type'}
			menu={tabsAreTopics && showKinds
				? {
						heading: 'Show',
						options: kindOptions.map((o) => ({
							value: o.value,
							label: o.value === ALL ? 'All types' : o.label
						})),
						value: kind,
						onchange: (v) => (kind = v || ALL),
						quiet: kind === ALL
					}
				: null}
			search={showSearch ? { label: 'Search insights', scope: 'insight' } : null}
			{interactive}
		/>
	{/if}

	{#if items.length === 0}
		<div class="glass-panel rounded-xl px-6 py-12 text-center">
			<BookOpenIcon class="text-muted-foreground mx-auto size-8" />
			<p class="mt-3 font-medium">Nothing to read yet</p>
			<p class="text-muted-foreground mt-1 text-sm">
				News, guides and articles from your firm will appear here.
			</p>
		</div>
	{:else if lead === null}
		<!-- One narrowing or three, the reader must be able to tell a miss from an empty library:
		     "nothing matches" with the way back, never "your firm has published nothing". -->
		<div class="glass-panel rounded-xl px-6 py-12 text-center">
			<SearchIcon class="text-muted-foreground mx-auto size-8" />
			<p class="mt-3 font-medium">Nothing matches</p>
			<p class="text-muted-foreground mt-1 text-sm">Try a different kind or topic.</p>
			{#if narrowed}
				<button
					type="button"
					class="text-primary mt-3 text-sm font-medium underline-offset-4 hover:underline"
					onclick={reset}
					disabled={!interactive}
				>
					Show everything
				</button>
			{/if}
		</div>
	{:else if look === 'magazine'}
		<NewsMagazine items={matched} />
	{:else if look === 'journal'}
		<NewsJournal items={matched} />
	{:else}
		<a
			href={lead.href}
			class="glass-panel group @lg:grid-cols-[3fr_2fr] grid overflow-hidden rounded-xl transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-md"
			data-news-lead
		>
			{#if showHero}
				<img
					src={lead.heroUrl}
					alt=""
					class="@lg:order-last @lg:aspect-auto @lg:h-full aspect-[16/8] w-full object-cover"
					loading="eager"
					onerror={() => (brokenHero = lead.id)}
				/>
			{/if}
			<div class="flex flex-col justify-between gap-4 p-5 @lg:p-6">
				<div>
					<p class="text-muted-foreground text-xs font-medium">{stamp(lead)}</p>
					<h2 class="mt-2 text-xl leading-snug font-semibold tracking-tight @lg:text-2xl">
						{lead.headline}
					</h2>
					{#if lead.summary}
						<p class="text-muted-foreground mt-2 line-clamp-3 text-sm leading-relaxed">
							{lead.summary}
						</p>
					{/if}
				</div>
				<span class="text-primary inline-flex items-center gap-1 text-sm font-medium">
					Read
					<ArrowRightIcon class="size-4 transition-transform group-hover:translate-x-0.5" />
				</span>
			</div>
		</a>

		{#if rest.length > 0}
			<div class="glass-panel divide-y rounded-xl">
				{#each rest as item (item.id)}
					<a
						href={item.href}
						class="hover:bg-accent/40 flex items-center gap-3 px-4 py-3.5 transition-colors first:rounded-t-xl last:rounded-b-xl"
					>
						<div class="min-w-0 flex-1">
							<p class="text-muted-foreground text-xs">{stamp(item)}</p>
							<p class="mt-0.5 line-clamp-2 font-medium leading-snug">{item.headline}</p>
						</div>
						<ChevronRightIcon class="text-muted-foreground size-4 shrink-0" />
					</a>
				{/each}
			</div>
		{/if}
	{/if}
</div>
