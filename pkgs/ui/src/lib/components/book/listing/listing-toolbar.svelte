<script lang="ts">
	import { cn } from '../../../utils.js';
	import { SEARCH_BOX_CLASS } from './filters.js';
	import { FIELD_CLASS } from '../../../field-shell.js';
	import FunnelIcon from '@lucide/svelte/icons/funnel';
	// Search box + filter row shared by every listing, so the three pages line up pixel for
	// pixel and gain "Clear" behaviour in one place.
	//
	// The box is a fixed 24rem rather than `flex-1` — `SEARCH_BOX_CLASS`, shared with the search
	// boxes that sit outside this toolbar so none of them can drift wider.
	//
	// TWO MODES. Without `onsearch` the query is `bind:value` and the listing filters locally —
	// the original behaviour, still right for a fixture-backed or already-complete list. With
	// `onsearch` the toolbar is server-driven: it debounces the typing and hands the term up, and
	// the app turns it into a navigation. That difference matters because a client-side filter
	// over ONE PAGE is a lie — it says "no client called Halloway" when it means "not on this
	// page" (see book/listing/query.ts).
	//
	// THE FILTERS ARE DATA HERE, not a snippet: given the defs and the values, the toolbar owns the
	// whole filtering surface, so the badge cannot disagree with the tags.
	import { Badge } from '../../ui/badge/index.js';
	import { Button } from '../../ui/button/index.js';
	import { type FilterDef } from './filter-panel.svelte';
	import FilterBar from './filter-bar.svelte';
	import { countFilters } from './applied.js';
	import SearchIcon from '@lucide/svelte/icons/search';
	import XIcon from '@lucide/svelte/icons/x';
	import RefreshCwIcon from '@lucide/svelte/icons/refresh-cw';
	import type { Snippet } from 'svelte';

	let {
		query = $bindable(''),
		placeholder = 'Search…',
		filters,
		filterDefs = [],
		filterValue = {},
		onfilters,
		/** True when any filter is off its default — drives the Clear button. */
		dirty = false,
		onclear,
		onsearch,
		/** Present ⇒ show a refresh button that re-pulls the listing (e.g. to fetch events that
		 *  arrived since load). Absent ⇒ no button. */
		onrefresh,
		/** Drives the refresh button's spinner + disabled state while a refresh is in flight. */
		refreshing = false,
		/** Debounce for `onsearch`, in ms. Long enough that a search is one request per phrase
		 *  rather than one per keystroke; short enough not to feel like a submit. */
		debounce = 300,
		stacked = false,
		/** Page-level acts (New client, Requests, …). Parked on the right of this row — the
		 *  top-right of the table — because the listing has no heading to hang a button on. */
		actions,
		/** Extra classes on the toolbar's own row. `shrink-0` is what the internally-scrolling listings
		 *  pass: the toolbar is fixed furniture around a scrolling list, and as a flex sibling of the
		 *  table it would otherwise be compressed to give the rows more room. */
		class: className
	}: {
		query?: string;
		placeholder?: string;
		filters?: Snippet;
		/** The page's own buttons. The right-hand anchor — the funnel sits beside search. */
		filterDefs?: FilterDef[];
		filterValue?: Record<string, string[]>;
		onfilters?: (next: Record<string, string[]>) => void;
		dirty?: boolean;
		onclear?: () => void;
		/** Present ⇒ server-driven: the term is emitted, not applied locally. */
		onsearch?: (term: string) => void;
		onrefresh?: () => void;
		refreshing?: boolean;
		debounce?: number;
		stacked?: boolean;
		actions?: Snippet;
		class?: string;
	} = $props();

	let timer: ReturnType<typeof setTimeout> | undefined;

	function typed(term: string) {
		if (term !== query) ownChange = true;
		query = term;
		if (!onsearch) return;
		clearTimeout(timer);
		timer = setTimeout(() => onsearch?.(term), debounce);
	}

	function submit() {
		// Enter skips the debounce: someone who has finished typing and pressed Enter should not
		// then wait out a timer.
		if (!onsearch) return;
		clearTimeout(timer);
		onsearch(query);
	}

	function clear() {
		clearTimeout(timer);
		// Before the tags unmount. An open editor flushes on destroy, which would put a
		// half-typed amount back onto the listing this gesture just cleared.
		discardOpen();
		ownChange = true;
		query = '';
		// ONE of them, never both: firing `onsearch('')` then `onclear()` is two navigations for one
		// gesture, and a caller that rebuilds its query from its props wrote the cleared term back.
		if (onclear) onclear();
		else onsearch?.('');
	}

	/** The open tag's editor. The bar registers it; the funnel and Clear cannot see the editor. */
	let discardOpen: (key?: string) => void = () => {};
	/** A query edit or a single-tag clear is ours. An empty-state Clear calls the listing's
	 *  own clearer and never reaches `clear`, so the effect below is what drops a half-typed edit. */
	let ownChange = false;
	let seen: { applied: number; query: string } | undefined;
	const filterCount = $derived(countFilters(filterValue, filterDefs));
	const filtersApplied = $derived(filterCount > 0);
	/** `null` — the bar follows whether anything is applied. A click pins it open or shut. */
	let barForced = $state<boolean | null>(null);
	const barOpen = $derived(barForced ?? filtersApplied);
	const funnelLabel = $derived(
		(barOpen ? 'Hide filters' : 'Show filters') +
			(filtersApplied ? ' (' + filterCount + ' applied)' : '')
	);

	function toggleBar(): void {
		// Before the tags unmount. An open editor flushes on destroy, which would apply a
		// half-typed amount as the bar disappeared.
		discardOpen();
		barForced = !barOpen;
	}

	function clearField(key: string): void {
		discardOpen(key);
		// Clearing a value is not a request to hide the row. Without this, dropping the last
		// applied filter would shut the bar the reader is still looking at.
		if (barForced === null) barForced = true;
		if (!onfilters || !filterValue[key]?.length) return;
		ownChange = true;
		const next = { ...filterValue };
		delete next[key];
		onfilters(next);
	}

	$effect(() => {
		const applied = countFilters(filterValue);
		const q = query;
		const prev = seen;
		seen = { applied, query: q };
		// The first run only records what is already applied. Reading the props to seed `seen`
		// would freeze that first value.
		if (!prev || ownChange) {
			ownChange = false;
			return;
		}
		// Every applied value vanished, or the search was cleared while nothing is applied.
		// Either is a clear from outside this toolbar (the empty state's button). One tag's X
		// sets `ownChange` and must not discard the edit twice.
		const parentClearedFilters = prev.applied > 0 && applied === 0;
		const parentClearedSearch = prev.query !== '' && q === '' && applied === 0;
		if (parentClearedFilters || parentClearedSearch) discardOpen();
	});
</script>

{#snippet searchBox(widthClass: string)}
	<div class="relative {widthClass}">
		<SearchIcon
			class="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
		/>
		<!-- `FIELD_CLASS`, not a class string of its own. This box carried a hand-written copy of the
		     border/background/radius/ring, and every part of it had drifted from the shell that every
		     other field in the app composes: `rounded-md` where a field is `rounded-lg`, a hard 2px
		     full-opacity focus ring where the rest of the estate tints one at 50% and moves the border
		     with it, `bg-background` (wrong in dark mode, where a field is `input/30`), a
		     browser-default placeholder, and `text-sm` with no `text-base` under `md` — which makes
		     iOS Safari zoom the whole page when you tap it. That is the drift design-language.md
		     names, and it happened here exactly as predicted: by copying the string rather than
		     composing the shell.
		     THREE overrides, each for a reason the shell cannot know:
		       `h-9`  — the toolbar's row height, so the box lines up with the buttons beside it
		                rather than the 8-high fields on a form.
		       `pl-9` — room for the magnifier.
		       `ring-inset` — kept from the old string, and the one part of it that was right. This
		                box lives inside a `CardContent` carrying `overflow-hidden` (it has to: the
		                table scrolls inside it) and sits flush against its top edge, so an OUTWARD
		                ring is painted outside the clip rectangle and comes back with its top shaved
		                off. Reserving a couple of pixels around the input was tried and is not
		                enough — a ring is only ever as safe as the tightest ancestor, and this
		                component is dropped into containers it cannot see.
		                It flips the shell's own ring rather than replacing it (`--tw-ring-inset`,
		                which `focus-visible:ring-3` then draws inside the border box), so the width
		                and the colour still come from `FIELD_FOCUS` and follow it if it changes.
		                `inset-ring-3` + `inset-ring-ring/50` + `ring-0` also works in v4 and was
		                tried; it restates both and would drift the moment the shell's ring did. -->
		<input
			value={query}
			oninput={(event) => typed((event.currentTarget as HTMLInputElement).value)}
			onkeydown={(event) => event.key === 'Enter' && submit()}
			type="search"
			{placeholder}
			aria-label={placeholder}
			class={cn(
				FIELD_CLASS,
				'placeholder:text-muted-foreground h-9 pl-9 focus-visible:ring-inset',
				// `type="search"` brings WebKit's own clear button — an OS blob that ignores the theme.
				'[&::-webkit-search-cancel-button]:appearance-none',
				query && !barOpen && 'pr-9'
			)}
		/>
		<!-- The bar's Clear leaves with the bar. A search on its own is cleared here, in the box. -->
		{#if query && !barOpen}
			<button
				type="button"
				class="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 flex size-5 -translate-y-1/2 cursor-pointer items-center justify-center rounded-full"
				aria-label="Clear search"
				onclick={clear}
			>
				<XIcon class="size-3.5" aria-hidden="true" />
			</button>
		{/if}
	</div>
{/snippet}

{#snippet clearButton()}
	{#if dirty || query}
		<Button variant="ghost" size="sm" class="h-9" onclick={clear}>
			<XIcon class="mr-1 size-3.5" /> Clear
		</Button>
	{/if}
{/snippet}

{#snippet refreshButton()}
	{#if onrefresh}
		<Button
			variant="ghost"
			size="sm"
			class="h-9 w-9 p-0"
			onclick={onrefresh}
			disabled={refreshing}
			title="Refresh"
			aria-label="Refresh"
		>
			<RefreshCwIcon class="size-4 {refreshing ? 'animate-spin' : ''}" />
		</Button>
	{/if}
{/snippet}

{#snippet fieldMenu()}
	{#if onfilters}
		<!-- Ghost, no border, hard against the search box. The funnel shows and hides the bar.
		     Muted at rest. Open, or hovered, it takes the highlight. Closed with filters applied,
		     it stays the current foreground — the badge is what says a filter is on. -->
		<Button
			variant="ghost"
			size="sm"
			class="h-9 shrink-0 cursor-pointer gap-1 border-transparent px-2 {barOpen || filtersApplied
				? 'text-foreground'
				: 'text-muted-foreground hover:text-foreground'}"
			aria-expanded={barOpen}
			aria-label={funnelLabel}
			title={funnelLabel}
			onclick={toggleBar}
		>
			<FunnelIcon class="size-4" aria-hidden="true" />
			{#if filtersApplied}
				<!-- `aria-hidden`: announcing it twice reads as "Show filters 3 applied 3". -->
				<Badge
					variant="default"
					class="ml-0.5 h-5 min-w-5 justify-center px-1.5 tabular-nums"
					aria-hidden="true">{filterCount}</Badge
				>
			{/if}
		</Button>
	{/if}
{/snippet}

{#snippet rightGroup()}
	<!-- `ml-auto`: the page's own action is the right-hand anchor. The height is enforced HERE,
	     not by every action snippet: a primary Button defaults to h-8 at `size="sm"`, while the
	     search and the funnel are h-9. Letting callers decide made the top edge step up and down
	     from one book to the next. Portalled menu items sit outside this subtree, so only the
	     toolbar triggers are normalised. -->
	{#if actions}
		<div class="ml-auto flex min-h-9 items-center gap-2 [&_[data-slot=button]]:h-9">
			{@render actions()}
		</div>
	{/if}
{/snippet}

{#snippet appliedBar()}
	{#if barOpen}
		<!-- A step under the sheet. Light chrome: `--muted` is that step. The staff dark chrome
		     inverts it (`--muted` is lighter than the card), so there the row uses `--background`,
		     the paper behind the sheet. -->
		<div
			class="bg-muted motion-item-enter flex w-full flex-wrap items-center gap-1.5 rounded-md px-2.5 py-1.5 dark:bg-background"
			data-slot="filter-bar"
		>
			{#if onfilters}
				<FilterBar
					available={filterDefs}
					value={filterValue}
					onchange={onfilters}
					ondismiss={clearField}
					register={(discard) => (discardOpen = discard)}
				/>
			{/if}
			<!-- Clear belongs to this row. Once the row is gone the button is gone with it;
			     a search on its own clears from the box. -->
			{@render clearButton()}
		</div>
	{/if}
{/snippet}

{#if stacked}
	<!-- Two rows: search alone (still the bounded box, not stretched), then the filter row beneath
	     it (see `stacked` prop). -->
	<div class={cn('flex w-full flex-col items-start gap-2', className)}>
		<div class="flex min-h-9 w-full flex-wrap items-center gap-2">
			{@render searchBox(SEARCH_BOX_CLASS)}
			{@render fieldMenu()}
			{@render refreshButton()}
			{@render rightGroup()}
		</div>
		{#if filters}
			<div class="flex w-full flex-wrap items-center gap-2">
				{@render filters()}
			</div>
		{/if}
		{@render appliedBar()}
	</div>
{:else}
	<div class={cn('flex flex-col gap-2', className)}>
		<div class="flex min-h-9 flex-wrap items-center gap-2">
			{@render searchBox(SEARCH_BOX_CLASS)}
			{@render fieldMenu()}
			{@render refreshButton()}
			{@render filters?.()}
			{@render rightGroup()}
		</div>
		{@render appliedBar()}
	</div>
{/if}
