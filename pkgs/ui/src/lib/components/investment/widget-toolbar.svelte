<script lang="ts">
	import type { Component } from 'svelte';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { Input } from '../ui/input/index.js';
	import SelectMenu from '../ui/select-menu/select-menu.svelte';
	import BrowseBar from './browse-bar.svelte';
	import { cn } from '../../utils.js';
	import { ALL, type CategoryPill } from './listing.js';
	import { SEARCH_BOX_CLASS } from '../book/listing/filters.js';
	import type { SelectMenuOption } from '../ui/select-menu/select-menu.svelte';

	// The shared listing toolbar — the quiet control row the holdings/activity/documents widgets
	// show under their header when the block's `toolbar` setting is on: a debounced search box,
	// an optional account SelectMenu, optional category pills with counts (documents) and an
	// optional right-aligned note ("3 unread"). Presentational only: filter state lives in
	// filtered-listing.svelte (or, next wave, the app via PortalPage's listingQuery), so this
	// stays reusable under every look — it paints nothing but a border-toned input row and
	// hairline pills, so it sits on the card AND the chrome-less page grounds.
	let {
		query,
		onquery,
		accounts = null,
		account = ALL,
		onaccount,
		categories = null,
		category = ALL,
		oncategory,
		categoryStyle = 'pills',
		categoryIcon = null,
		note = null,
		searchScope = null,
		search = true,
		searchDisabled = false,
		placeholder = 'Search…',
		label,
		interactive = true
	}: {
		/** The current search text (external state — see filtered-listing). */
		query: string;
		/** Debounced: fires ~200ms after typing pauses, not per keystroke. */
		onquery: (q: string) => void;
		/** Account filter options ('all' first, from listing.ts accountItems); the control hides
		 * itself with fewer than two REAL options AND no filter in force — a one-account filter
		 * can't filter anything, but one that is already filtering must always be reachable. */
		accounts?: SelectMenuOption[] | null;
		account?: string;
		onaccount?: (value: string) => void;
		/** Category pills with counts (documents); null hides the row. */
		categories?: CategoryPill[] | null;
		category?: string;
		oncategory?: (value: string) => void;
		/**
		 * How the categories are offered. `pills` is the compact row under a card's header;
		 * `tiles` is a grid of tappable cards (mark, name, count) for a page whose categories ARE
		 * its navigation — the Docs page, where "Statements" and "Tax" are how a client thinks
		 * about what they are looking for. Same state and same semantics; one is bigger.
		 * `tabs` is the portal's browse bar (browse-bar.svelte, shared with the Insights library):
		 * the categories as primary tabs with "All" first, the account filter as the pull-down
		 * beside them, and search a button that opens the portal's palette on `searchScope` — so
		 * neither a box nor the note takes a row of its own.
		 */
		categoryStyle?: 'pills' | 'tiles' | 'tabs';
		/** The mark a tile wears for a category value (tiles only; null ⇒ no mark). */
		categoryIcon?: ((value: string) => Component) | null;
		note?: string | null;
		/** Tabs style only: the search palette category the bar's search button opens on (an API
		 * group type such as `document`), or null for everything. */
		searchScope?: string | null;
		/** False drops the search box — a listing short enough to scan whole (filtered-listing's
		 * threshold) offers its pills and note without a box that would find nothing a glance
		 * doesn't. */
		search?: boolean;
		/** True when the slice is one page of a bigger set and nobody can run the search
		 * server-side — the controls disable with an explanation rather than silently searching
		 * a fraction of the data (the DataTable sort-disable pattern). */
		searchDisabled?: boolean;
		placeholder?: string;
		/** Accessible name prefix ("Holdings", "Documents") for the controls' labels. */
		label: string;
		/** False renders inert controls (the studio preview). */
		interactive?: boolean;
	} = $props();

	const SEARCH_DISABLED_TITLE = 'Searching the full list isn’t available yet.';

	// SSR-stable unique id for the account select's label (several toolbars can share a page).
	const uid = $props.id();

	const disabled = $derived(searchDisabled || !interactive);
	// value/count of every row for the leading 'All' pill.
	const total = $derived((categories ?? []).reduce((s, c) => s + c.count, 0));

	// Debounce so a controlled parent (URL-driven, next wave) isn't hammered per keystroke.
	// SSR-safe: the timer only exists once an input event fires.
	let timer: ReturnType<typeof setTimeout> | undefined;
	function typed(e: Event & { currentTarget: HTMLInputElement }) {
		const value = e.currentTarget.value;
		clearTimeout(timer);
		timer = setTimeout(() => onquery(value), 200);
	}
	$effect(() => () => clearTimeout(timer));

	const showAccounts = $derived(!!accounts && (accounts.length > 2 || account !== ALL));
	/** Tabs mode offers search only past filtered-listing's glance threshold, like the box — a
	 * search button over a handful of rows is the same tool-for-a-library-you-do-not-have. */
	const tabsSearch = $derived(
		search ? { label: `Search ${label.toLowerCase()}`, scope: searchScope } : null
	);
</script>

{#if categoryStyle === 'tabs'}
	<div data-widget-toolbar>
		<BrowseBar
			tabs={[
				{ value: ALL, label: 'All' },
				...(categories ?? []).map((c) => ({ value: c.value, label: c.value }))
			]}
			tab={category}
			ontab={(v) => oncategory?.(v)}
			tabsLabel="Filter {label.toLowerCase()} by category"
			menu={showAccounts && accounts
				? {
						heading: 'Account',
						options: accounts.map((a) => ({ value: a.value, label: a.label })),
						value: account,
						onchange: (v) => onaccount?.(v),
						quiet: account === ALL
					}
				: null}
			search={tabsSearch}
			{interactive}
		/>
	</div>
{:else}
	<div data-widget-toolbar class="space-y-2">
		{#if search || (accounts && (accounts.length > 2 || account !== ALL)) || note}
			<div class="flex flex-wrap items-center gap-2">
				{#if search}
					<div class="relative min-w-0 {SEARCH_BOX_CLASS}">
						<SearchIcon
							class="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2"
						/>
						<Input
							type="search"
							value={query}
							oninput={typed}
							{placeholder}
							aria-label="Search {label.toLowerCase()}"
							{disabled}
							aria-disabled={disabled ? 'true' : undefined}
							title={searchDisabled ? SEARCH_DISABLED_TITLE : undefined}
							class="h-8 ps-8 text-sm"
						/>
					</div>
				{/if}
				{#if accounts && (accounts.length > 2 || account !== ALL)}
					<!-- > 2: 'All accounts' plus at least two real accounts. Same disable story as the
			     search — filtering one page of a paginated slice by account lies identically.
			     The second arm is the escape hatch and is not symmetry for its own sake: a filter
			     IS in force, so hiding the only control that can lift it leaves the reader on a
			     narrowed listing with no way back. That happens for real whenever the options
			     shrink under the applied filter — a server-side cut, or an account that has since
			     been renamed or closed. -->
					<label class="sr-only" for="{uid}-account">Filter {label.toLowerCase()} by account</label>
					<span title={searchDisabled ? SEARCH_DISABLED_TITLE : undefined} class="shrink-0">
						<SelectMenu
							id="{uid}-account"
							items={accounts}
							value={account}
							onValueChange={(v) => onaccount?.(v)}
							{disabled}
							size="sm"
							class="w-auto min-w-36"
						/>
					</span>
				{/if}
				{#if note}
					<span class="text-muted-foreground ms-auto shrink-0 text-xs tabular-nums">{note}</span>
				{/if}
			</div>
		{/if}
		{#if categories && categories.length > 1 && categoryStyle === 'tiles'}
			<!-- The same choice as the pills, drawn as a grid of cards. No "All" tile: everything is
		     shown until a tile is picked, a second tap on the picked tile clears it, and the line
		     beneath says what is showing and offers the way back — a filter in force must always
		     name itself and be one tap from gone. -->
			<div class="space-y-2">
				<!-- Tracks are sized by the tiles' needs, not a count: five fixed columns at 868px
			     gave each tile 165px, and "Correspondence" beside its mark needs 166 — the label
			     ran out of the tile. A tile is at least 12rem wide and the row holds as many as
			     fit; the label truncates as the last resort and carries its full text as a title. -->
				<div
					class="@lg:grid-cols-[repeat(auto-fill,minmax(12rem,1fr))] grid grid-cols-2 gap-2"
					role="group"
					aria-label="Browse {label.toLowerCase()} by category"
				>
					{#each categories as tile (tile.value)}
						{@const on = category === tile.value}
						{@const Icon = categoryIcon?.(tile.value)}
						<button
							type="button"
							disabled={!interactive}
							onclick={() => oncategory?.(on ? ALL : tile.value)}
							aria-pressed={on}
							class={cn(
								'glass-panel focus-visible:ring-ring @max-lg:flex-col @max-lg:items-start @max-lg:gap-2 flex items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none',
								on ? 'border-brand-line bg-brand-subtle' : 'border-transparent hover:bg-accent/40'
							)}
						>
							{#if Icon}
								<span
									class={cn(
										'flex size-9 shrink-0 items-center justify-center rounded-lg',
										on ? 'bg-background/70 text-brand-strong' : 'bg-muted text-muted-foreground'
									)}
								>
									<Icon class="size-4.5" />
								</span>
							{/if}
							<span class="min-w-0 max-w-full">
								<span class="block truncate text-sm leading-tight font-medium" title={tile.value}>
									{tile.value}
								</span>
								<span class="text-muted-foreground block text-xs tabular-nums">
									{tile.count}
									{tile.count === 1 ? 'document' : 'documents'}
								</span>
							</span>
						</button>
					{/each}
				</div>
				{#if category !== ALL}
					<p class="text-muted-foreground px-1 text-xs">
						Showing {category} only ·
						<button
							type="button"
							disabled={!interactive}
							onclick={() => oncategory?.(ALL)}
							class="text-foreground font-medium underline-offset-4 hover:underline"
						>
							Show all
						</button>
					</p>
				{/if}
			</div>
		{:else if categories && categories.length > 1}
			<!-- Category pills, counts included — enumerated from the data (listing.ts), so they can
		     never fall out of step with the buckets on screen. Hairline chrome + the brand trio
		     for the active pill (token-derived, unlike the legacy page's inline colours). -->
			<div
				class="flex flex-wrap items-center gap-1.5"
				role="group"
				aria-label="Filter {label.toLowerCase()} by category"
			>
				{#each [{ value: ALL, count: total, pillLabel: 'All' }, ...categories.map( (c) => ({ ...c, pillLabel: c.value }) )] as pill (pill.value)}
					{@const on = category === pill.value}
					<button
						type="button"
						disabled={!interactive}
						onclick={() => oncategory?.(on && pill.value !== ALL ? ALL : pill.value)}
						aria-pressed={on}
						class={cn(
							'focus-visible:ring-ring rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none',
							on
								? 'border-brand-line bg-brand-subtle text-brand-strong'
								: 'border-border text-muted-foreground hover:bg-muted/60'
						)}
					>
						{pill.pillLabel} ({pill.count})
					</button>
				{/each}
			</div>
		{/if}
	</div>
{/if}
