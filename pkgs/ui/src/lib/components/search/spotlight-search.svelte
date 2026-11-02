<script lang="ts">
	import type { Component } from 'svelte';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { Button } from '../ui/button/index.js';
	import { cn } from '../../utils.js';
	import XIcon from '@lucide/svelte/icons/x';
	import * as Command from '../ui/command/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import { LoadErrorBanner } from '../ui/load-error-banner/index.js';
	import {
		relevanceOrderedSearchHits,
		routableSearchGroups,
		scheduleSearch,
		searchCategoriesForGroups,
		searchHrefFor,
		searchHighlightParts,
		SEARCH_MIN_QUERY_LENGTH,
		SEARCH_OPEN_EVENT,
		SPOTLIGHT_CHIP_CLASS,
		SPOTLIGHT_OVERLAY_CLASS,
		SPOTLIGHT_PANEL_CLASS,
		SPOTLIGHT_PHONE_CLOSE_CLASS,
		SPOTLIGHT_PANEL_STYLE,
		SearchClient,
		settleSearch,
		type SearchCategory,
		type SearchHit,
		type SearchOpenDetail,
		type SearchResults,
		type SearchRoutes
	} from '../../search.js';

	/**
	 * The portal's search palette — the platform's Spotlight-style bar, simplified for a reader
	 * rather than an operator. One glass panel: a field, the category row — every category this
	 * portal searches, always on show with "All" first, so a client sees what the search covers
	 * before typing and can narrow with one tap, the way the App Store and Spotlight offer their
	 * kinds up front — one relevance-ordered result stream with the matched text marked, and the
	 * three honest states (searching, nothing, failed with a retry). No quick actions, no `/` or
	 * `>` prefixes, no first-run guide, no keycap hints: a client opens it from a button, types,
	 * and taps a result. On a phone it is a full-screen search view (SPOTLIGHT_PANEL_CLASS), with
	 * a Cancel beside the field in place of the dialog's close.
	 *
	 * The app owns the POLICY — which categories exist, where a hit routes (`routes`), what the
	 * URL is (`client`) and how to navigate (`onnavigate`) — and this component owns the state
	 * machine and the surface, so the client and platform portals can share it. It also answers the
	 * `openSearchPalette()` window event (search.ts), which is how a page control several components
	 * down (the browse bar's search button) opens it on a category without any shared state.
	 */
	let {
		open = $bindable(false),
		scope = $bindable<string | null>(null),
		scopes,
		icons,
		typeLabels,
		routes,
		client,
		onnavigate,
		title = 'Search',
		description = 'Search your records',
		placeholder = 'Search…',
		scopedPlaceholder = (label: string) => `Search ${label.toLowerCase()}…`,
		ariaLabel = 'Search',
		emptyMessage = (term: string) => `No matches for “${term}”.`,
		failedMessage = 'We couldn’t search. Please try again.'
	}: {
		open?: boolean;
		/** The pinned category (an API group type), or null for everything. */
		scope?: string | null;
		/** Every category this portal searches, in display order — no "All" entry. */
		scopes: readonly SearchCategory[];
		/** The mark a hit of each type wears; types without one get the magnifier. */
		icons: Record<string, Component>;
		/** The type chip's label per hit type ("Document", "Action item"). */
		typeLabels: Record<string, string>;
		/** Where a hit opens — the app's routing policy; unroutable types are dropped. */
		routes: SearchRoutes;
		/** The sequence-guarded caller; its URL builder is the app's (contact scoping etc.). */
		client: SearchClient;
		onnavigate: (href: string) => void;
		title?: string;
		description?: string;
		placeholder?: string;
		scopedPlaceholder?: (scopeLabel: string) => string;
		ariaLabel?: string;
		emptyMessage?: (term: string) => string;
		failedMessage?: string;
	} = $props();

	let query = $state('');
	let results = $state<SearchResults | null>(null);
	let loading = $state(false);
	let failed = $state(false);
	let inputEl = $state<HTMLInputElement | null>(null);
	let stripEl = $state<HTMLDivElement | null>(null);

	const term = $derived(query.trim());
	const ready = $derived(term.length >= SEARCH_MIN_QUERY_LENGTH);
	const groups = $derived(routableSearchGroups(results, routes));
	// A pinned scope narrows what is SHOWN. The API already answers over everything the reader may
	// see, a handful per group, so the cut is made here rather than with a second round trip.
	const shown = $derived(scope === null ? groups : groups.filter((g) => g.type === scope));
	const hits = $derived(relevanceOrderedSearchHits(shown));
	// Categories the current result actually contains — the others stay offered but read quiet,
	// so a reader can see at a glance where the matches are without losing the way to look
	// elsewhere.
	const present = $derived(new Set(searchCategoriesForGroups(groups, scopes).map((c) => c.type)));
	const activeScope = $derived(scopes.find((s) => s.type === scope) ?? null);
	// Resting, the palette is just the bar: nothing is listed until there is something to list.
	const listOpen = $derived(failed || ready);

	async function run(q: string): Promise<void> {
		loading = true;
		failed = false;
		const next = settleSearch(await client.run(q));
		if (!next) return; // superseded by a newer query — leave the palette alone
		loading = false;
		results = next.results;
		failed = next.failed;
	}

	function idle(): void {
		client.cancel();
		results = null;
		failed = false;
		loading = false;
	}

	$effect(() => scheduleSearch(term, { query: run, idle }));

	function reset(): void {
		client.cancel();
		query = '';
		scope = null;
		results = null;
		failed = false;
		loading = false;
	}

	function select(hit: SearchHit): void {
		const href = searchHrefFor(hit, routes);
		if (!href) return;
		open = false;
		reset();
		onnavigate(href);
	}

	function pin(next: string | null): void {
		scope = next;
		inputEl?.focus();
	}

	/** Backspace or Escape on an empty field lifts the pinned category before anything closes. */
	function onInputKeydown(e: KeyboardEvent): void {
		if ((e.key === 'Backspace' || e.key === 'Escape') && !query && scope !== null) {
			e.preventDefault();
			e.stopPropagation();
			scope = null;
		}
	}

	// The window-event bridge (search.ts::openSearchPalette). A custom event name with a colon is
	// not an attribute `<svelte:window>` can carry, so this is a listener with its own cleanup.
	$effect(() => {
		const onOpenRequest = (e: Event) => {
			const detail = (e as CustomEvent<SearchOpenDetail>).detail;
			scope = detail?.scope ?? null;
			open = true;
		};
		window.addEventListener(SEARCH_OPEN_EVENT, onOpenRequest);
		return () => window.removeEventListener(SEARCH_OPEN_EVENT, onOpenRequest);
	});

	// The category strip scrolls on a phone; a palette opened already narrowed (the browse bar's
	// search button) must show the chip in force, not leave it off the right edge.
	$effect(() => {
		void scope;
		const strip = stripEl;
		if (!strip) return;
		// After the open animation's first frame: the panel scales in, and a rect measured mid-scale
		// lands the chip short of the edge.
		const frame = requestAnimationFrame(() => {
			strip
				.querySelector<HTMLElement>('[aria-pressed="true"]')
				?.scrollIntoView?.({ inline: 'center', block: 'nearest' });
		});
		return () => cancelAnimationFrame(frame);
	});
</script>

{#snippet highlighted(value: string)}
	{#each searchHighlightParts(value, term) as part, i (i)}
		{#if part.matched}
			<mark class="bg-brand-subtle rounded-sm font-semibold text-inherit">{part.text}</mark>
		{:else}
			{part.text}
		{/if}
	{/each}
{/snippet}

<!-- The panel's material, geometry and phone behaviour are the shared SPOTLIGHT_* constants in
     search.ts, so this palette and the platform's cannot drift (docs/search.md, "The panel"). -->
<Command.Dialog
	bind:open
	{title}
	{description}
	shouldFilter={false}
	showCloseButton
	overlayClass={SPOTLIGHT_OVERLAY_CLASS}
	contentStyle={SPOTLIGHT_PANEL_STYLE}
	class={SPOTLIGHT_PANEL_CLASS}
	onOpenChange={(next: boolean) => {
		if (!next) reset();
	}}
	data-spotlight-search
>
	<Command.Input
		bind:ref={inputEl}
		bind:value={query}
		onkeydown={onInputKeydown}
		placeholder={activeScope ? scopedPlaceholder(activeScope.label) : placeholder}
		aria-label={ariaLabel}
		class="placeholder:text-muted-foreground/70 text-xl font-light md:text-xl"
	/>
	<Dialog.Close
		data-slot="spotlight-cancel"
		aria-label="Close search"
		class={SPOTLIGHT_PHONE_CLOSE_CLASS}
	>
		<XIcon />
	</Dialog.Close>
	<!-- The bar's own row, outside the list so it stays put whether or not anything is listed:
	     every category, the one in force pressed; tapping it again lifts it. "All" is the floating
	     bar's explicit way back — a phone does without it, as Monzo does, because the toggle is
	     the way back there. Categories the current result contains read in full; the rest read
	     quiet but stay a tap away. -->
	<div
		class="border-border/40 flex min-h-11 items-center gap-2 border-t px-3 py-2 max-sm:border-t-0 max-sm:pt-0 max-sm:pb-3"
	>
		<div
			bind:this={stripEl}
			role="group"
			aria-label="Filter search results by category"
			class="no-scrollbar -mx-3 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto px-3"
		>
			{#each [{ type: null, label: 'All' }, ...scopes] as category (category.type ?? 'all')}
				{@const on = scope === category.type}
				{@const quiet =
					ready && results !== null && category.type !== null && !present.has(category.type)}
				<Button
					type="button"
					variant={on ? 'secondary' : 'ghost'}
					size="sm"
					onclick={() => pin(on ? null : category.type)}
					aria-pressed={on}
					class={cn(
						SPOTLIGHT_CHIP_CLASS,
						category.type === null && 'max-sm:hidden',
						on
							? 'bg-brand-subtle text-brand-strong hover:bg-brand-subtle/80 max-sm:border-transparent'
							: 'text-muted-foreground hover:text-foreground max-sm:text-foreground',
						quiet && !on && 'opacity-50'
					)}
				>
					{category.label}
				</Button>
			{/each}
		</div>
	</div>

	<!-- The panel owns the hard cap and the list gives way inside it; its max-height is also the
	     reveal track (`motion-reveal`, the same spring as the panel, so the two read as one
	     gesture). -->
	<Command.List
		class={[
			'no-scrollbar motion-reveal min-h-0 flex-1 scroll-py-2 transition-[max-height,opacity,padding,border-color]',
			listOpen
				? 'border-border/40 max-h-[calc(var(--spotlight-max-h)-7.5rem)] border-t p-2 opacity-100'
				: 'pointer-events-none max-h-0 overflow-hidden border-transparent p-0 opacity-0'
		]}
	>
		{#if failed}
			<LoadErrorBanner class="m-1" message={failedMessage} onRetry={() => run(term)} />
		{:else if ready && loading && !results}
			<p class="text-muted-foreground px-3 py-6 text-center text-sm">Searching…</p>
		{:else if ready}
			{#if hits.length}
				<Command.Group
					heading="{activeScope?.label ?? 'Best matches'} · {hits.length}"
					class="py-1"
				>
					{#each hits as hit, index (`${hit.type}:${hit.target ?? ''}:${hit.id}:${index}`)}
						{@const Icon = icons[hit.type] ?? SearchIcon}
						<Command.Item
							value="result-{hit.type}-{hit.id}"
							onSelect={() => select(hit)}
							class="min-h-13 gap-3 px-3 py-2.5"
						>
							<div
								class="bg-muted text-muted-foreground flex size-8 shrink-0 items-center justify-center rounded-lg"
							>
								<Icon class="size-4" />
							</div>
							<div class="min-w-0 flex-1">
								<div class="flex min-w-0 items-center gap-2">
									<span class="truncate font-medium">{@render highlighted(hit.title)}</span>
									{#if !activeScope}
										<span
											class="border-border/70 text-muted-foreground hidden shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] font-medium sm:inline"
										>
											{typeLabels[hit.type] ?? 'Result'}
										</span>
									{/if}
								</div>
								{#if hit.subtitle}
									<p class="text-muted-foreground mt-0.5 truncate text-xs">
										{@render highlighted(hit.subtitle)}
									</p>
								{/if}
							</div>
							{#if hit.meta}
								<span
									class="text-muted-foreground max-w-36 shrink-0 truncate text-right text-xs tabular-nums"
								>
									{@render highlighted(hit.meta)}
								</span>
							{/if}
						</Command.Item>
					{/each}
				</Command.Group>
			{:else if !loading}
				<Command.Empty forceMount>{emptyMessage(term)}</Command.Empty>
			{/if}
		{/if}
	</Command.List>
</Command.Dialog>
