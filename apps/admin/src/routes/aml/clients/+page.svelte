<script lang="ts">
	// Every client Graphene screens, one row each: the latest identity check and AML screen, the
	// ComplyCube risk level and whether anybody needs to act. Ops works a client from their page, or
	// works the queue one profile at a time from the Workbench tab.
	//
	// Searching, filtering, sorting and paging live in the URL and are applied by the API. The list
	// spans every firm, so a client-side filter over one page would read as a search over the book
	// while covering a sliver of it.
	import { goto, invalidateAll } from '$app/navigation';
	import {
		Button,
		Card,
		CardContent,
		DataTable,
		EmptyState,
		LoadErrorBanner,
		PageHeader,
		Pagination,
		REFRESH_WHILE,
		RefreshWhile,
		StatusBadge,
		type DataTableColumn,
		type DataTableSort
	} from '@graphene/ui';
	import { ListingToolbar, type FilterDef } from '@graphene/ui/book/listing';
	import ShieldAlertIcon from '@lucide/svelte/icons/shield-alert';
	import {
		ORIGIN_LABEL,
		amlPill,
		clientHref,
		identityPill,
		listHref,
		rowAwaitingResult,
		riskOf,
		shortDate,
		type AmlClientRow,
		type AmlListQuery
	} from '$lib/aml';
	import ConsoleTabs from '$lib/compliance/console-tabs.svelte';
	import FirmBadge from '$lib/compliance/firm-badge.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/** Turn a control change into a navigation, carrying every other control with it. */
	function navigate(extra: Partial<AmlListQuery>) {
		goto(listHref({ ...data.query, ...extra }));
	}

	/**
	 * The sort shown in the headers — read from the URL, not held locally. The list pages, so ordering
	 * the rows in hand would reorder the PAGE; the click goes to the API, which orders the match set.
	 * No sort is the default order: clients that need a human first, then the latest change.
	 */
	const sort = $derived<DataTableSort | null>(
		data.query.sort ? { key: data.query.sort, dir: data.query.dir } : null
	);

	function chooseSort(next: DataTableSort | null) {
		navigate({ sort: next?.key ?? '', dir: next?.dir ?? 'asc', page: 1 });
	}

	// `tenant` stays `single`: it resolves the request's auth context (`aml_session`), not a column
	// filter.
	const filterDefs = $derived<FilterDef[]>([
		{
			key: 'tenant',
			label: 'Firm',
			options: data.firms.map((firm) => ({ value: firm.uid, label: firm.name })),
			single: true,
			allLabel: 'All firms'
		}
	]);
	const filterValue = $derived<Record<string, string[]>>(
		data.query.tenant ? { tenant: [data.query.tenant] } : {}
	);

	const dirty = $derived(!!(data.query.q || data.query.tenant));

	function clearFilters() {
		navigate({ q: '', tenant: '', page: 1 });
	}

	const pageCount = $derived(Math.max(1, Math.ceil(data.total / data.pageSize)));
	const awaiting = $derived(data.rows.some(rowAwaitingResult));

	// The page number is the URL's, not local state: a function binding reads it from the load data
	// and turns a click into a navigation.
	function onPage(next: number) {
		if (next !== data.query.page) navigate({ page: next });
	}

	const columns: DataTableColumn<AmlClientRow>[] = [
		{ key: 'client', header: 'Client', sortable: true, headerClass: 'w-[24%]' },
		{ key: 'firm', header: 'Firm', sortable: true, hideBelow: 'md', headerClass: 'w-[18%]' },
		{ key: 'identity', header: 'Identity', headerClass: 'w-[12%]' },
		{ key: 'aml', header: 'AML', headerClass: 'w-[14%]' },
		{
			key: 'risk',
			header: 'Risk',
			sortable: true,
			defaultDir: 'desc',
			hideBelow: 'sm',
			headerClass: 'w-[10%]'
		},
		{ key: 'attention', header: '', headerClass: 'w-[12%]' },
		{
			key: 'updated',
			header: 'Updated',
			sortable: true,
			defaultDir: 'desc',
			hideBelow: 'lg',
			headerClass: 'w-[10%]'
		}
	];
</script>

<svelte:head><title>Clients · AML and identity · Operations</title></svelte:head>

<!-- Only while a row on this page is still with ComplyCube, or a screen is about to start. -->
<RefreshWhile
	active={awaiting}
	interval={REFRESH_WHILE.pendingCheck}
	onpoll={() => invalidateAll()}
/>

<PageHeader
	eyebrow="Internal operations"
	title="AML and identity"
	description="Every client Graphene screens, with what needs a decision first."
/>

<ConsoleTabs current="clients" work={data.work} tenant={data.query.tenant} />

<div class="space-y-5">
	{#if data.loadError}
		<LoadErrorBanner message={data.loadError} onRetry={() => invalidateAll()} />
	{/if}

	<ListingToolbar
		query={data.query.q}
		placeholder="Search client name"
		{dirty}
		onclear={clearFilters}
		onsearch={(term) => navigate({ q: term, page: 1 })}
		{filterDefs}
		{filterValue}
		onfilters={(next) => navigate({ tenant: next.tenant?.[0] ?? '', page: 1 })}
	>
		{#snippet actions()}
			<span class="text-muted-foreground text-sm tabular-nums">
				{data.total}
				{data.total === 1 ? 'client' : 'clients'}
			</span>
		{/snippet}
	</ListingToolbar>

	{#if data.rows.length === 0 && !data.loadError}
		{#snippet clearAll()}
			<Button variant="outline" size="sm" onclick={clearFilters}>Clear all filters</Button>
		{/snippet}
		<EmptyState
			icon={ShieldAlertIcon}
			title={dirty ? 'No clients match' : 'No clients yet'}
			description={dirty
				? 'Widen the search, or clear all filters.'
				: 'Clients appear here when one is due a screen or a firm runs a check through the partner API.'}
			action={dirty ? clearAll : undefined}
		/>
	{:else}
		<Card glass>
			<CardContent>
				<DataTable
					{columns}
					rows={data.rows}
					{sort}
					onSort={chooseSort}
					onRowClick={(row) => goto(clientHref(row.key))}
					getKey={(row) => row.key}
					fixed
					label="Clients"
					empty="No clients match."
				>
					{#snippet cell(row, column)}
						{#if column.key === 'client'}
							<a href={clientHref(row.key)} class="block min-w-0 hover:underline">
								<span class="block truncate font-medium">{row.client_name || '—'}</span>
								<span class="text-muted-foreground text-xs">{ORIGIN_LABEL[row.origin]}</span>
							</a>
						{:else if column.key === 'firm'}
							<FirmBadge name={row.tenant_name} iconUrl={row.tenant_icon_url} class="max-w-48" />
						{:else if column.key === 'identity'}
							{@const pill = identityPill(row.identity)}
							<StatusBadge tone={pill.tone}>{pill.label}</StatusBadge>
						{:else if column.key === 'aml'}
							{@const pill = amlPill(row.aml, row.origin)}
							<div class="flex flex-wrap items-center gap-1">
								<StatusBadge tone={pill.tone}>{pill.label}</StatusBadge>
								{#if row.stale}
									<!-- The client's details changed after their last screen cleared, so the clear no
									     longer describes them. -->
									<StatusBadge tone="warning">Stale</StatusBadge>
								{/if}
							</div>
						{:else if column.key === 'risk'}
							{@const risk = riskOf(row.risk)}
							<StatusBadge tone={risk.tone}>{risk.label}</StatusBadge>
						{:else if column.key === 'attention'}
							{#if row.needs_attention}
								<StatusBadge tone="warning">Needs attention</StatusBadge>
							{/if}
						{:else if column.key === 'updated'}
							<span class="text-muted-foreground whitespace-nowrap"
								>{shortDate(row.updated_at)}</span
							>
						{/if}
					{/snippet}
				</DataTable>
			</CardContent>
		</Card>

		<Pagination bind:page={() => data.query.page, onPage} {pageCount} />
	{/if}
</div>
