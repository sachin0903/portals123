<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import {
		Card,
		CardContent,
		DataTable,
		EmptyState,
		LoadErrorBanner,
		type DataTableColumn
	} from '@graphene/ui';
	import { ListingToolbar, search } from '@graphene/ui/book/listing';
	import ChartColumnIcon from '@lucide/svelte/icons/chart-column';
	import type { PageData } from './$types';
	import type { ReportSummary } from './+page.server';

	let { data }: { data: PageData } = $props();
	let query = $state('');

	// The description is a sentence, not a value, so it wraps rather than pushing the table sideways.
	const columns: DataTableColumn<ReportSummary>[] = [
		{ key: 'name', header: 'Report', headerClass: 'w-[24%]' },
		{
			key: 'description',
			header: 'What it returns',
			sortable: false,
			cellClass: 'whitespace-normal'
		}
	];

	const rows = $derived(data.reports.filter((row) => search(query, row.name, row.description)));
</script>

<svelte:head><title>Reports</title></svelte:head>

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#if data.loadError}
		<LoadErrorBanner message="Reports couldn’t be loaded." onRetry={() => invalidateAll()} />
	{:else if data.reports.length === 0}
		<EmptyState
			icon={ChartColumnIcon}
			title="No reports yet"
			description="Graphene has not switched any reports on."
		/>
	{:else}
		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				<ListingToolbar
					class="shrink-0"
					bind:query
					placeholder="Search reports…"
					dirty={query.trim() !== ''}
					onclear={() => (query = '')}
				/>
				<DataTable
					{columns}
					{rows}
					scroll
					fixed
					getKey={(row) => row.code}
					label="Reports"
					empty="No reports match your search."
				>
					{#snippet cell(row, column)}
						{#if column.key === 'name'}
							<a class="font-medium hover:underline" href="/reports/{row.code}">{row.name}</a>
						{:else}
							<span class="text-muted-foreground">{row.description}</span>
						{/if}
					{/snippet}
				</DataTable>
			</CardContent>
		</Card>
	{/if}
</div>
