<script lang="ts">
	// One platform entity as a table: search, sort, page.
	//
	// The columns are DATA, not a component-time constant — they come from the vendor's own
	// documentation via the API (`morgan_lloyd.manifest`), intersected with what the warehouse
	// actually has. So the table is built from `data.columns` rather than a literal list, and a
	// vendor renaming a field costs one absent column and a note rather than a broken page.
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		DataTable,
		LoadErrorBanner,
		SectionHeading,
		Sheet,
		type DataTableColumn,
		type DataTableSort
	} from '@graphene/ui';
	import { ListingToolbar, PaginationStrip, rangeLabelFromOffset } from '@graphene/ui/book/listing';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';

	let { data } = $props();

	let query = $state('');
	let applied = $state('');
	$effect(() => {
		if (data.filters.q !== applied) {
			applied = data.filters.q;
			query = data.filters.q;
		}
	});

	let selected = $state<Record<string, unknown> | null>(null);
	let detailOpen = $state(false);

	function setFilter(patch: Record<string, string | number | null>) {
		const params = new URLSearchParams(page.url.searchParams);
		for (const [key, value] of Object.entries(patch)) {
			if (value === null || value === '') params.delete(key);
			else params.set(key, String(value));
		}
		if (!('page' in patch)) params.delete('page');
		void goto(`?${params}`, { keepFocus: true, noScroll: true });
	}

	// SERVER-side sort. `onSort` is the DataTable's own hook for exactly this: the headers stay
	// live and the loader re-fetches the whole set in the new order. The alternative — letting
	// the table sort — would order the fifty rows on screen and present that as the order of all
	// 1,553, which is the lie `sortDisabled` exists to prevent.
	function onSort(next: DataTableSort | null) {
		if (!next) {
			setFilter({ sort: null, dir: null });
			return;
		}
		setFilter({ sort: next.key, dir: next.dir });
	}

	// `cam_potentiallyvulnerable` -> "Potentially vulnerable". The vendor's names are the truth
	// and stay visible in the row detail; this is only the header.
	function header(column: string): string {
		const stripped = column.replace(/^cam_/, '').replace(/_value$/, '');
		const spaced = stripped.replace(/_/g, ' ');
		return spaced.charAt(0).toUpperCase() + spaced.slice(1);
	}

	// `auto`, not `fixed`: the columns come from whichever entity is open, so no widths are known.
	const columns: DataTableColumn<Record<string, unknown>>[] = $derived(
		data.columns.map((key, index) => ({
			key,
			header: header(key),
			sortable: true,
			// Everything past the first four collapses on a narrow viewport rather than forcing
			// the page to scroll sideways.
			hideBelow: index < 2 ? undefined : index < 4 ? 'md' : 'lg'
		}))
	);

	/** The table's own sort indicator, driven from the URL so Back restores it. */
	const sort: DataTableSort | null = $derived(
		data.filters.sort
			? { key: data.filters.sort, dir: data.filters.direction as 'asc' | 'desc' }
			: null
	);

	const pageCount = $derived(Math.max(1, Math.ceil(data.total / data.pageSize)));
	function pageHref(pageNumber: number): string {
		const params = new URLSearchParams(page.url.searchParams);
		if (pageNumber > 1) params.set('page', String(pageNumber));
		else params.delete('page');
		const qs = params.toString();
		return qs ? `?${qs}` : page.url.pathname;
	}

	function show(value: unknown): string {
		if (value === null || value === undefined || value === '') return '—';
		if (typeof value === 'boolean') return value ? 'Yes' : 'No';
		return String(value);
	}
</script>

<svelte:head><title>{data.entity.label} · Platform data</title></svelte:head>

<div class="space-y-5">
	<!-- A band within the firm console, not a page in its own right — the console's own header
	     names the firm above it. So SectionHeading rather than PageHeader: this is the same
	     0.8125rem band every other grouped surface uses, where the hand-rolled `text-lg` it
	     replaces was a fourth size on a page that already has three. -->
	<div>
		<SectionHeading
			title={data.entity.label}
			count={data.entity.availability === 'ok' ? data.total.toLocaleString() : undefined}
		/>
		<p class="text-muted-foreground font-mono text-[11px]">{data.entity.relation}</p>
	</div>

	{#if data.entity.note}
		<p class="text-muted-foreground text-xs">{data.entity.note}</p>
	{/if}

	{#if data.loadError}
		<LoadErrorBanner
			message="This entity could not be read ({data.loadError})."
			onRetry={() => invalidateAll()}
		/>
	{/if}

	{#if data.entity.availability !== 'ok'}
		<!-- The reason, not an empty table. "No rows" and "no source" are opposite facts. -->
		<Alert>
			<AlertDescription>
				{data.entity.reason ?? 'This entity has no source.'}
			</AlertDescription>
		</Alert>
	{:else}
		{#if data.missingColumns.length}
			<Alert>
				<AlertDescription>
					<span class="font-medium">{data.missingColumns.length} documented column(s)</span> are
					absent from the warehouse and are not shown:
					<span class="font-mono text-xs">{data.missingColumns.join(', ')}</span>. The vendor may
					have renamed them.
				</AlertDescription>
			</Alert>
		{/if}

		<!-- Server-driven mode: this entity is paged by the API, so a local filter would answer "no
		     such record" when it means "not on this page". -->
		<ListingToolbar
			bind:query
			placeholder="Search {data.entity.label.toLowerCase()}"
			dirty={Boolean(data.filters.q)}
			onsearch={(term) => setFilter({ q: term })}
			onclear={() => setFilter({ q: null })}
		/>

		<Card>
			<CardContent class="p-0">
				<DataTable
					rows={data.items}
					{columns}
					label={data.entity.label}
					{sort}
					{onSort}
					getKey={(row) => String(row[data.columns[0]] ?? Math.random())}
					onRowClick={(row) => {
						selected = row;
						detailOpen = true;
					}}
					empty="No records match."
				>
					{#snippet cell(row, column)}
						{@const value = row[column.key]}
						{#if typeof value === 'boolean'}
							<Badge variant="ghost">{value ? 'Yes' : 'No'}</Badge>
						{:else if typeof value === 'number'}
							<!-- NOT `toLocaleString()`. Most numbers in a raw vendor table are IDENTIFIERS,
							     not quantities: a Dataverse option-set member rendered as `809,130,016`
							     reads as eight hundred million and cannot be matched against the
							     `809130016` in the config, the docs or an error message. This view shows
							     the warehouse's value; grouping belongs where a column is known to be
							     money, and nothing here knows that. -->
							<span class="tabular-nums">{value}</span>
						{:else}
							<span class="block max-w-[22rem] truncate" title={show(value)}>{show(value)}</span>
						{/if}
					{/snippet}
				</DataTable>
			</CardContent>
		</Card>

		{#if data.total > 0}
			<PaginationStrip
				label={rangeLabelFromOffset(
					(data.filters.page - 1) * data.pageSize,
					data.items.length,
					data.total
				)}
				page={data.filters.page}
				{pageCount}
				href={pageHref}
				class="mx-0"
			/>
		{/if}
	{/if}
</div>

<Sheet.Root bind:open={detailOpen}>
	<Sheet.Content class="flex w-full flex-col gap-0 overflow-hidden p-0">
		<Sheet.Header class="shrink-0 border-b px-6 pt-6 pb-4">
			<Sheet.Title>{data.entity.label}</Sheet.Title>
			<Sheet.Description class="font-mono text-xs">
				{selected ? String(selected[data.columns[0]] ?? '') : ''}
			</Sheet.Description>
		</Sheet.Header>
		{#if selected}
			<div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
				<!-- The vendor's own field names, not the prettified headers: this is the view
				     someone cross-references against the API documentation. -->
				<dl class="grid gap-3 sm:grid-cols-2">
					{#each data.columns as column (column)}
						<div>
							<dt class="text-muted-foreground font-mono text-[11px]">{column}</dt>
							<dd class="text-sm break-words">{show(selected[column])}</dd>
						</div>
					{/each}
				</dl>
			</div>
		{/if}
		<div class="flex shrink-0 justify-end gap-2 border-t px-6 py-4">
			<Button variant="ghost" onclick={() => (detailOpen = false)}>Close</Button>
		</div>
	</Sheet.Content>
</Sheet.Root>
