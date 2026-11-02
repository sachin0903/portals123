<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import {
		Card,
		CardContent,
		DataTable,
		LoadErrorBanner,
		PageHeader,
		type DataTableColumn
	} from '@graphene/ui';
	import { ListingToolbar, search } from '@graphene/ui/book/listing';
	import type { ActionData, PageData } from './$types';
	import type { ReportRow } from './+page.server';
	import ReportSwitch from './report-switch.svelte';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	let query = $state('');

	const columns: DataTableColumn<ReportRow>[] = [
		{ key: 'name', header: 'Report' },
		{ key: 'description', header: 'What it returns', sortable: false },
		{ key: 'enabled', header: 'On', align: 'center', sortValue: (row) => (row.enabled ? 1 : 0) }
	];

	const rows = $derived(
		data.reports.filter((row) => search(query, row.name, row.description, row.code))
	);
</script>

<svelte:head><title>Reports · Operations</title></svelte:head>

<PageHeader
	title="Reports"
	description="Shipped queries a firm can download. Switching one on makes it available to every firm."
/>

{#if data.loadError}
	<div class="mt-6">
		<LoadErrorBanner onRetry={() => invalidateAll()} />
	</div>
{/if}

{#if form?.error}
	<p class="text-destructive mt-4 text-sm">{form.error}</p>
{/if}

<Card glass class="mt-6">
	<CardContent class="flex flex-col gap-4">
		<ListingToolbar
			bind:query
			placeholder="Search reports…"
			dirty={query.trim() !== ''}
			onclear={() => (query = '')}
		/>
		<DataTable
			{columns}
			{rows}
			getKey={(row) => row.code}
			label="Reports"
			empty="No reports match your search."
		>
			{#snippet cell(row, column)}
				{#if column.key === 'name'}
					<a class="font-medium hover:underline" href="/operations/reports/{row.code}">{row.name}</a
					>
				{:else if column.key === 'description'}
					<span class="text-muted-foreground">{row.description}</span>
				{:else if column.key === 'enabled'}
					{#if data.canManage}
						<ReportSwitch
							code={row.code}
							enabled={row.enabled}
							label={row.enabled ? `Switch off ${row.name}` : `Switch on ${row.name}`}
						/>
					{:else}
						<span class="text-muted-foreground">{row.enabled ? 'On' : 'Off'}</span>
					{/if}
				{/if}
			{/snippet}
		</DataTable>
	</CardContent>
</Card>
