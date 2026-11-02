<script lang="ts">
	import { page } from '$app/state';
	import {
		Button,
		Card,
		CardContent,
		DataTable,
		DropdownMenu,
		Input,
		Label,
		Money,
		PageHeader,
		humanise,
		type DataTableColumn
	} from '@graphene/ui';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const report = $derived(data.report);
	const BACK = { href: '/reports', label: 'Reports' };

	let type = $state('');
	$effect(() => {
		type = data.values.type ?? '';
	});

	type PreviewRow = Record<string, string | number | null> & { _key: string };

	const tableRows = $derived(
		(data.preview?.rows ?? []).map((row, index): PreviewRow => ({ ...row, _key: String(index) }))
	);

	const columns = $derived(
		report.columns.map((column): DataTableColumn<PreviewRow> => ({
			key: column.key,
			header: column.label,
			align:
				column.kind === 'money' || column.kind === 'number' || column.kind === 'percent'
					? 'right'
					: 'left',
			sortable: false
		}))
	);

	const chosenType = $derived(
		report.params.find((param) => param.key === 'type')?.options?.find((option) => option === type)
	);
	const typeLabel = $derived(chosenType ? humanise(chosenType) : 'All types');

	function downloadHref(format: 'csv' | 'xlsx'): string {
		const params = new URLSearchParams(page.url.searchParams);
		params.set('format', format);
		return `/reports/${report.code}/export?${params}`;
	}
</script>

<svelte:head><title>{report.name}</title></svelte:head>

<div class="flex min-w-0 flex-col">
	<!-- The same heading every staff screen uses. The description used to sit alone under the
	     topbar with no title above it, so the page opened on a sentence about itself. -->
	<PageHeader title={report.name} description={report.description} back={BACK} class="mb-4">
		{#snippet actions()}
			{#if data.preview && data.canExport && !data.missing}
				<DropdownMenu.Root>
					<DropdownMenu.Trigger>
						{#snippet child({ props })}
							<Button {...props} variant="outline" size="sm">
								<DownloadIcon class="size-4" />
								Download
								<ChevronDownIcon class="size-3.5 opacity-70" />
							</Button>
						{/snippet}
					</DropdownMenu.Trigger>
					<DropdownMenu.Content align="end">
						<DropdownMenu.Item>
							{#snippet child({ props })}
								<a {...props} href={downloadHref('csv')}>CSV</a>
							{/snippet}
						</DropdownMenu.Item>
						<DropdownMenu.Item>
							{#snippet child({ props })}
								<a {...props} href={downloadHref('xlsx')}>Excel</a>
							{/snippet}
						</DropdownMenu.Item>
					</DropdownMenu.Content>
				</DropdownMenu.Root>
			{/if}
		{/snippet}
	</PageHeader>

	<div class="flex flex-col gap-4">
		{#if report.params.length > 0}
			<form method="GET" class="flex flex-wrap items-end gap-3">
				{#each report.params as param (param.key)}
					{#if param.kind === 'date'}
						<div class="flex flex-col gap-1.5">
							<Label for={param.key}>{param.label}</Label>
							<Input
								id={param.key}
								name={param.key}
								type="date"
								class="w-44"
								value={data.values[param.key] ?? ''}
								required={param.required}
							/>
						</div>
					{:else if param.kind === 'enum'}
						<div class="flex flex-col gap-1.5">
							<Label>{param.label}</Label>
							<input type="hidden" name={param.key} value={type} />
							<DropdownMenu.Root>
								<DropdownMenu.Trigger>
									{#snippet child({ props })}
										<Button {...props} type="button" variant="outline">
											{typeLabel}
											<ChevronDownIcon class="size-3.5 opacity-70" />
										</Button>
									{/snippet}
								</DropdownMenu.Trigger>
								<DropdownMenu.Content align="start" class="max-h-72 min-w-44 overflow-y-auto">
									<DropdownMenu.Item onclick={() => (type = '')}>All types</DropdownMenu.Item>
									{#each param.options as option (option)}
										<DropdownMenu.Item onclick={() => (type = option)}>
											{humanise(option)}
										</DropdownMenu.Item>
									{/each}
								</DropdownMenu.Content>
							</DropdownMenu.Root>
						</div>
					{/if}
				{/each}
				<Button type="submit">Run</Button>
			</form>
		{/if}

		{#if data.previewError}
			<p class="text-destructive text-sm">{data.previewError}</p>
		{/if}

		{#if data.preview}
			{#if data.preview.narrowed || data.preview.truncated}
				<p class="text-muted-foreground text-sm">
					{#if data.preview.narrowed}These rows are the clients you can see, not the whole firm.{/if}
					{#if data.preview.truncated}Showing the first 100 rows. The download is the full extract,
						up to the row cap.{/if}
				</p>
			{/if}
			<Card glass>
				<CardContent>
					<DataTable
						{columns}
						rows={tableRows}
						getKey={(row) => String(row._key)}
						label={report.name}
						empty="No rows."
					>
						{#snippet cell(row, column)}
							{@const kind = report.columns.find((item) => item.key === column.key)?.kind}
							{#if row[column.key] == null || row[column.key] === ''}
								<span class="text-muted-foreground">—</span>
							{:else if kind === 'money'}
								<Money amount={Number(row[column.key])} />
							{:else if kind === 'percent'}
								<span class="tabular-nums">{Number(row[column.key]).toFixed(2)}%</span>
							{:else}
								{row[column.key]}
							{/if}
						{/snippet}
					</DataTable>
				</CardContent>
			</Card>
		{/if}
	</div>
</div>
