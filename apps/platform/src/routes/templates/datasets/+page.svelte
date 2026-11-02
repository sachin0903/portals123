<script lang="ts">
	// No `tabs` snippet: the topbar crumb (Templates › Datasets) is the way back.
	// Two tables: a provided dataset has no versions, edit screen or delete, and only "own" has Format.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		Badge,
		Button,
		DataTable,
		DATASET_FORMAT_META,
		DATASET_SCOPE_META,
		DropdownMenu,
		EmptyState,
		LoadErrorBanner,
		SectionHeading,
		TruncatedText,
		formatDate,
		type DataTableColumn,
		type Dataset
	} from '@graphene/ui';
	import {
		ListingOverflow,
		ListingToolbar,
		matches,
		nextUrlFromStart,
		type FilterDef
	} from '@graphene/ui/book/listing';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import ListingFrame from '$lib/components/listing-frame.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let scopeFilter = $state<string[]>([]);
	// svelte-ignore state_referenced_locally
	let searchQuery = $state(data.query);

	// The search is the URL's (the API searches every dataset); the toolbar debounces it.
	function onSearch(term: string): void {
		searchQuery = term;
		goto(nextUrlFromStart(page.url, { q: term }), {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	// No 'collection' scope: nothing implements it, and the API refuses it.
	const filterDefs: FilterDef[] = [
		{
			key: 'scope',
			label: 'Scope',
			options: [
				{ value: 'global', label: 'Whole firm' },
				{ value: 'subject', label: 'Per client' }
			]
		}
	];
	const barValue = $derived<Record<string, string[]>>(
		scopeFilter.length ? { scope: scopeFilter } : {}
	);

	function passesFilters(d: Dataset): boolean {
		if (!matches(scopeFilter, d.scope)) return false;
		const q = searchQuery.trim() && searchQuery.toLowerCase();
		return !q || d.name.toLowerCase().includes(q) || d.code.toLowerCase().includes(q);
	}

	const provided = $derived(data.datasets.filter((d) => d.provided && passesFilters(d)));
	const own = $derived(data.datasets.filter((d) => !d.provided && passesFilters(d)));

	const hasFilters = $derived(searchQuery !== '' || scopeFilter.length > 0);
	function clearFilters() {
		searchQuery = '';
		scopeFilter = [];
	}

	// Widths: [name, scope, version]. A provided dataset gets Source in place of Format and Created.
	function columns(own: boolean, [name, scope, version]: string[]): DataTableColumn<Dataset>[] {
		const hide = { sortable: true, hideBelowWidth: 'sm' } as const;
		const format: DataTableColumn<Dataset> = {
			...hide,
			key: 'format',
			header: 'Format',
			headerClass: 'w-[13%]',
			sortValue: (d) => DATASET_FORMAT_META[d.format].label
		};
		return [
			{ key: 'name', header: 'Name', sortable: true, sortDefault: true, headerClass: name },
			...(own ? [format] : []),
			{
				...hide,
				key: 'scope',
				header: 'Scope',
				headerClass: scope,
				sortValue: (d) => DATASET_SCOPE_META[d.scope].label
			},
			{
				...hide,
				key: 'version',
				header: 'Version',
				align: 'center',
				defaultDir: 'desc',
				headerClass: version,
				sortValue: (d) => d.publishedVersionNumber ?? 0
			},
			own
				? {
						key: 'created',
						header: 'Created',
						sortable: true,
						defaultDir: 'desc',
						hideBelowWidth: 'md',
						headerClass: 'w-[18%]',
						sortValue: (d) => d.createdAt
					}
				: {
						key: 'source',
						header: 'Source',
						hideBelowWidth: 'md',
						headerClass: 'w-[20%]'
					}
		];
	}
	const sections = [
		{
			own: true,
			title: 'Built by your firm',
			label: 'built by your firm',
			columns: columns(true, ['w-[40%]', 'w-[16%]', 'w-[13%]'])
		},
		{
			own: false,
			title: 'Provided by Graphene',
			label: 'provided by Graphene',
			columns: columns(false, ['w-[46%]', 'w-[20%]', 'w-[14%]'])
		}
	];
</script>

{#snippet datasetCell(d: Dataset, column: DataTableColumn<Dataset>)}
	{@const fm = DATASET_FORMAT_META[d.format]}
	{@const scope = DATASET_SCOPE_META[d.scope]}
	{#if column.key === 'name'}
		<div class="flex min-w-0 items-center gap-3">
			<div class="flex size-8 shrink-0 items-center justify-center rounded-lg {fm.bgColor}">
				<DatabaseIcon class="size-4 {fm.textColor}" />
			</div>
			<div class="min-w-0 flex-1">
				<div class="flex items-center gap-2">
					<TruncatedText
						text={d.name}
						href={`/templates/datasets/${d.uid}`}
						class="text-sm font-semibold"
					/>
					{#if !d.enabled}
						<span class="text-muted-foreground shrink-0 text-xs">(disabled)</span>
					{/if}
				</div>
				<p class="text-muted-foreground mt-0.5 truncate text-xs">
					ID: <span class="font-mono">{d.code}</span>
					{#if d.catalogueKey && !d.provided}
						· from {d.catalogueKey}
					{/if}
				</p>
			</div>
		</div>
	{:else if column.key === 'format'}
		<Badge class="{fm.bgColor} {fm.textColor}">{fm.label}</Badge>
	{:else if column.key === 'scope'}
		<Badge variant="outline" class="font-normal">{scope.label}</Badge>
	{:else if column.key === 'version'}
		{#if d.publishedVersionNumber}
			<span class="text-xs tabular-nums">v{d.publishedVersionNumber}</span>
		{:else}
			<span class="text-muted-foreground text-xs">—</span>
		{/if}
	{:else if column.key === 'created'}
		<p class="text-xs">{d.createdBy}</p>
		<p class="text-muted-foreground text-xs">{formatDate(d.createdAt)}</p>
	{:else if column.key === 'source'}
		<Badge variant="outline" class="font-normal">Graphene</Badge>
	{/if}
{/snippet}

{#snippet clearDatasetFilters()}
	<Button variant="link" size="sm" onclick={clearFilters}>Clear filters</Button>
{/snippet}

{#snippet createFirstDataset()}
	<Button variant="outline" size="sm" onclick={() => goto('/templates/datasets/new')}>
		<PlusIcon class="mr-1.5 size-4" />
		New dataset
	</Button>
{/snippet}

{#snippet listingActions()}
	<ListingOverflow>
		<DropdownMenu.Item>
			{#snippet child({ props })}
				<a {...props} href="/templates/datasets/new">New dataset</a>
			{/snippet}
		</DropdownMenu.Item>
	</ListingOverflow>
{/snippet}

<ListingFrame>
	{#snippet banner()}
		{#if data.loadError}
			<LoadErrorBanner
				message="The datasets list couldn’t be loaded. Try again."
				onRetry={() => invalidateAll()}
			/>
		{/if}
	{/snippet}
	<ListingToolbar
		class="shrink-0"
		query={searchQuery}
		onsearch={onSearch}
		placeholder="Search datasets…"
		dirty={hasFilters}
		onclear={clearFilters}
		actions={data.canManageDatasets ? listingActions : undefined}
		{filterDefs}
		filterValue={barValue}
		onfilters={(next) => (scopeFilter = next.scope ?? [])}
	/>

	<!-- The card body scrolls, not either table. -->
	<div class="flex min-w-0 flex-col gap-6 md:min-h-0 md:flex-1 md:overflow-y-auto">
		{#if own.length + provided.length === 0}
			<EmptyState
				icon={DatabaseIcon}
				title={hasFilters
					? 'No datasets match your filters.'
					: data.loadError
						? 'The datasets list couldn’t be loaded.'
						: 'No datasets yet.'}
				action={hasFilters
					? clearDatasetFilters
					: !data.loadError && data.canManageDatasets
						? createFirstDataset
						: undefined}
				compact
				chromeless
			/>
		{/if}
		{#each sections as s (s.title)}
			{@const rows = s.own ? own : provided}
			{#if rows.length > 0}
				<section class="space-y-3">
					<SectionHeading title={s.title} count={rows.length} />
					{#if !s.own}
						<p class="text-muted-foreground -mt-1.5 text-xs">
							Ready to use, and kept up to date for you. Open one to set its date range.
						</p>
					{/if}
					<DataTable
						columns={s.columns}
						{rows}
						getKey={(d) => d.uid}
						cell={datasetCell}
						onRowClick={(d) => goto(`/templates/datasets/${d.uid}`)}
						rowClass={(d) => (s.own && !d.enabled ? 'opacity-60' : undefined)}
						label="Datasets {s.label}"
						fixed
					/>
				</section>
			{/if}
		{/each}

		<p class="text-muted-foreground text-center text-xs">
			Datasets are resolved when a document is generated, and the rows used are snapshotted onto it.
		</p>
	</div>
</ListingFrame>
