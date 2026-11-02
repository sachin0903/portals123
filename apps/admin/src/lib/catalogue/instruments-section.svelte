<script module lang="ts">
	import type { FilterDef } from '@graphene/ui/book/listing';

	export function instrumentFilterDefs(facets: Record<string, string[]>): FilterDef[] {
		const options = (key: string) => (facets[key] ?? []).map((value) => ({ value, label: value }));
		return [
			{ key: 'asset_type', label: 'Type', options: options('asset_type'), allLabel: 'All types' },
			{
				key: 'currency',
				label: 'Currency',
				options: options('currency'),
				allLabel: 'All currencies'
			},
			{
				key: 'exchange_code',
				label: 'Exchange',
				options: options('exchange_code'),
				allLabel: 'All exchanges'
			}
		];
	}
</script>

<script lang="ts">
	import {
		Badge,
		Card,
		CardContent,
		CELL_INSTRUMENT,
		DataTable,
		Input,
		TruncatedText,
		type DataTableColumn,
		type DataTableSort
	} from '@graphene/ui';
	import { ListingToolbar } from '@graphene/ui/book/listing';
	import type { Instrument } from './types';

	let {
		instruments,
		total = instruments.length,
		query: initialQuery = '',
		filters: active = {},
		facets = {},
		onsearch,
		onfilter,
		onclear
	}: {
		instruments: Instrument[];
		/** How many exist, not how many were sent — what makes "200 of 6,170" honest. */
		total?: number;
		query?: string;
		/** The filters currently in force, by column. Read from the URL by the page's loader. */
		filters?: Record<string, string[]>;
		/**
		 * The values each filter may take, from the API.
		 *
		 * They have to come from the server for the same reason the search does: built from the
		 * rows on screen, the currency list could only ever offer the currencies in this window of
		 * 200, so narrowing would change what you are allowed to narrow BY. The API computes each
		 * one with its own clause lifted, so choosing USD does not leave USD as the only option.
		 */
		facets?: Record<string, string[]>;
		/** Runs the search on the SERVER. Absent ⇒ the rows are the whole set and search is local,
		 *  which is only true for a caller that has genuinely loaded everything. */
		onsearch?: (q: string) => void;
		/** Applies filters on the SERVER; absent, the filter row does not show. */
		onfilter?: (values: Record<string, string[]>) => void;
		onclear?: () => void;
		canManage?: boolean;
		form?: unknown;
	} = $props();

	// Read-only on purpose. Instruments arrive from the custodians' own asset universes through
	// the materialiser (0041: global, un-scoped — an ISIN is not tenant data), so editing one here
	// would be overwritten by the next sync. The page exists to answer "do we hold this security,
	// and under what identifiers", which is what a model allocation needs.
	// Seeded once, then owned by the input. Safe because the page searches with `replaceState`, so
	// no history entry ever carries a different `q` for this component to fall out of step with —
	// the only way to arrive with another one is a fresh navigation, which remounts this.
	// svelte-ignore state_referenced_locally
	let query = $state(initialQuery);
	let sort = $state<DataTableSort | null>({ key: 'name', dir: 'asc' });

	const server = $derived(!!onsearch);

	const filterDefs = $derived(instrumentFilterDefs(facets));

	/** True when anything beyond the default view is narrowing the universe — drives Clear. */
	const dirty = $derived(filterDefs.some((f) => (active[f.key]?.length ?? 0) > 0));

	const barValue = $derived<Record<string, string[]>>(
		Object.fromEntries(
			filterDefs.filter((f) => active[f.key]?.length).map((f) => [f.key, active[f.key]])
		)
	);

	/** Sent as one call: two `goto`s in a row race and the first key is lost, so Save must apply
	 *  the whole changed set at once. */
	function setFilters(next: Record<string, string[]>): void {
		onfilter?.(Object.fromEntries(filterDefs.map((f) => [f.key, next[f.key] ?? []])));
	}

	// Debounced, because every keystroke is a round trip otherwise. 250ms is the same figure
	// `ListingToolbar` uses for its own server mode — one search feel across the estate.
	let timer: ReturnType<typeof setTimeout> | undefined;
	function onInput(value: string): void {
		query = value;
		if (!onsearch) return;
		clearTimeout(timer);
		timer = setTimeout(() => onsearch?.(query.trim()), 250);
	}

	/**
	 * In SERVER mode the rows are already the answer — filtering them again would narrow a window
	 * that was narrowed for us, which is how a search starts hiding rows the server just found.
	 *
	 * The local branch is kept for a caller that passes no `onsearch`, and only that caller: this
	 * table used to read the bundled catalogue's first 500 instruments and search THOSE, so a
	 * security outside the cap simply could not be found and the page said so as if it were a fact.
	 */
	const rows = $derived(
		server || query.trim() === ''
			? instruments
			: instruments.filter((instrument) => {
					const needle = query.trim().toLowerCase();
					return (
						instrument.name.toLowerCase().includes(needle) ||
						(instrument.isin ?? '').toLowerCase().includes(needle) ||
						(instrument.sedol ?? '').toLowerCase().includes(needle)
					);
				})
	);

	/** True when there are more matches than were sent — the reason the frame scrolls to a stop
	 *  rather than to the end of the universe, and the thing a count has to admit. */
	const windowed = $derived(total > rows.length);

	const columns: DataTableColumn<Instrument>[] = [
		{ key: 'name', header: 'Asset', sortable: true, headerClass: 'w-[28%]' },
		{ key: 'isin', header: 'ISIN', sortable: true, headerClass: 'w-[18%]' },
		{ key: 'sedol', header: 'SEDOL', sortable: true, hideBelow: 'md', headerClass: 'w-[14%]' },
		{ key: 'asset_type', header: 'Type', sortable: true, headerClass: 'w-[16%]' },
		// Left: a currency and an exchange are codes, not figures.
		{ key: 'currency', header: 'Ccy', sortable: true, headerClass: 'w-[10%]' },
		{
			key: 'exchange_code',
			header: 'Exchange',
			sortable: true,
			hideBelow: 'lg',
			headerClass: 'w-[14%]'
		}
	];
</script>

<Card glass>
	<CardContent class="space-y-3">
		<div class="flex flex-wrap items-start justify-between gap-3">
			{#if onfilter}
				<!-- The shared toolbar, but keeping this component's own debounced input rather than
				     the toolbar's: the search here fires with `replaceState` so a keystroke never
				     becomes a history entry, and the toolbar's own debounce has no way to say that. -->
				<ListingToolbar
					{dirty}
					onclear={onclear ?? undefined}
					{filterDefs}
					filterValue={barValue}
					onfilters={setFilters}
				>
					{#snippet filters()}
						<Input
							placeholder="Search name, ISIN or SEDOL"
							value={query}
							oninput={(e) => onInput(e.currentTarget.value)}
							aria-label="Search name, ISIN or SEDOL"
							class="w-full sm:w-80"
						/>
					{/snippet}
				</ListingToolbar>
			{:else}
				<!-- No `onfilter` means the caller has the whole set in hand and there are no facets
				     to offer, so the row is a search box and nothing else. -->
				<Input
					placeholder="Search name, ISIN or SEDOL"
					value={query}
					oninput={(e) => onInput(e.currentTarget.value)}
					class="max-w-sm"
				/>
			{/if}
			<!-- The count says what is on screen AND what exists. A window that does not admit it is
			     a window reads as the whole universe — which is exactly how the old capped list
			     answered "we do not hold that security" for anything past its 500th row. -->
			<p class="text-muted-foreground text-sm tabular-nums">
				{#if windowed}
					{rows.length.toLocaleString()} of {total.toLocaleString()}
				{:else}
					{total.toLocaleString()}
					{total === 1 ? 'asset' : 'assets'}
				{/if}
			</p>
		</div>
		<!-- A fixed-height scrolling frame, like the platform's other tables: the header stays put
		     and the body scrolls, so the page does not grow with the result set and the search box
		     never leaves the screen. `min-h-0` is what lets the inner scroller size against the
		     frame instead of its own content. -->
		<div class="max-h-[calc(100svh-22rem)] min-h-[20rem] overflow-y-auto rounded-lg border">
			<DataTable
				{columns}
				{rows}
				bind:sort
				getKey={(instrument) => instrument.id}
				fixed
				empty={query.trim() || dirty
					? 'No instruments match. Try a different search or wider filters.'
					: 'No instruments are available yet. Refresh the platform data to check again.'}
			>
				{#snippet cell(instrument, column)}
					{#if column.key === 'name'}
						<!-- The one ceiling that fires on ordinary data: fund names run to 89 characters in the
					     real book, and a table cell is `whitespace-nowrap`, so one of them set this column's
					     width and every row wore it. `TruncatedText` rather than a bare `truncate` because
					     clipping without a way to read the rest does not shorten the name, it hides it. -->
						<TruncatedText text={instrument.name} class="{CELL_INSTRUMENT} font-medium" />
					{:else if column.key === 'isin'}
						<span class="font-mono text-xs">{instrument.isin ?? '—'}</span>
					{:else if column.key === 'sedol'}
						<span class="text-muted-foreground font-mono text-xs">{instrument.sedol ?? '—'}</span>
					{:else if column.key === 'asset_type'}
						{#if instrument.asset_type}
							<Badge variant="outline" class="font-normal">{instrument.asset_type}</Badge>
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					{:else if column.key === 'currency'}
						<span class="tabular-nums">{instrument.currency ?? '—'}</span>
					{:else if column.key === 'exchange_code'}
						<span class="text-muted-foreground text-xs">{instrument.exchange_code ?? '—'}</span>
					{/if}
				{/snippet}
			</DataTable>
		</div>
		{#if windowed}
			<p class="text-muted-foreground text-xs">
				Showing the first {rows.length.toLocaleString()} matches. Search across the full instrument list
				to narrow them.
			</p>
		{/if}
	</CardContent>
</Card>
