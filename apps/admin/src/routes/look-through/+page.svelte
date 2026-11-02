<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		Badge,
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		DataTable,
		type DataTableColumn,
		FileDropzone,
		humanise,
		PageHeader,
		RecordId,
		SectionHeading,
		StatusBadge
	} from '@graphene/ui';
	import {
		ListingToolbar,
		matches,
		optionsFrom,
		search,
		type FilterDef
	} from '@graphene/ui/book/listing';
	import type {
		LookthroughFundSummary,
		LookthroughImport,
		LookthroughImportResult
	} from '@graphene/ui/book/lookthrough';
	import FileSpreadsheetIcon from '@lucide/svelte/icons/file-spreadsheet';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import LayersIcon from '@lucide/svelte/icons/layers-3';
	import type { PageProps } from './$types';

	let { data, form }: PageProps = $props();
	let files = $state<File[]>([]);
	let importing = $state(false);
	let localError = $state<string | null>(null);
	const results = $derived((form?.results ?? []) as LookthroughImportResult[]);
	const date = (value: string | null) =>
		value
			? new Date(`${value}${value.includes('T') ? '' : 'T00:00:00'}`).toLocaleDateString('en-GB', {
					day: 'numeric',
					month: 'short',
					year: 'numeric'
				})
			: '—';
	const pct = (value: number | null) =>
		value == null
			? '—'
			: (value > 1 ? value / 100 : value).toLocaleString('en-GB', {
					style: 'percent',
					maximumFractionDigits: 1
				});

	const fundColumns: DataTableColumn<LookthroughFundSummary>[] = [
		{ key: 'name', header: 'Fund', sortable: true, sortDefault: true, cellClass: 'w-full max-w-0' },
		{
			key: 'isins',
			header: 'Matching ISINs',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (f) => f.isins?.length ?? 0,
			hideBelowWidth: 'xl'
		},
		{ key: 'latest_as_of', header: 'Latest snapshot', sortable: true, defaultDir: 'desc' },
		{
			key: 'position_count',
			header: 'Positions',
			sortable: true,
			align: 'right',
			defaultDir: 'desc'
		},
		{ key: 'coverage', header: 'Coverage', sortable: true, align: 'right', defaultDir: 'desc' }
	];
	const importColumns: DataTableColumn<LookthroughImport>[] = [
		{
			key: 'filename',
			header: 'File',
			sortable: true,
			sortDefault: true,
			cellClass: 'w-full max-w-0'
		},
		{ key: 'fund_name', header: 'Fund', sortable: true, hideBelowWidth: 'xl' },
		{ key: 'as_of_date', header: 'As at', sortable: true, defaultDir: 'desc' },
		{
			key: 'position_count',
			header: 'Positions',
			sortable: true,
			align: 'right',
			defaultDir: 'desc'
		},
		{ key: 'imported_at', header: 'Imported', sortable: true, defaultDir: 'desc' }
	];

	// Import history's own filter state — deliberately separate from the fund register above,
	// which is a curated list and stays unfiltered. The API caps this list at 250 rows (server
	// side), so search and the fund filter run over what's already in hand.
	let importQuery = $state('');
	// MULTI: "fund A or fund B" is a real question when chasing two related snapshots.
	let importFundFilter = $state<string[]>([]);
	const importFundOptions = $derived(optionsFrom(data.lookthrough.imports, (i) => i.fund_name));
	const importFilterDefs = $derived<FilterDef[]>([
		{ key: 'fund', label: 'Fund', options: importFundOptions }
	]);
	const importFilterValue = $derived<Record<string, string[]>>(
		importFundFilter.length ? { fund: importFundFilter } : {}
	);
	function onImportFilters(next: Record<string, string[]>) {
		importFundFilter = next.fund ?? [];
	}
	const importDirty = $derived(importFundFilter.length > 0);
	function clearImportFilters(): void {
		importFundFilter = [];
	}
	const filteredImports = $derived(
		data.lookthrough.imports.filter(
			(i) => matches(importFundFilter, i.fund_name) && search(importQuery, i.filename, i.fund_name)
		)
	);
</script>

<svelte:head><title>Look-through · Operations</title></svelte:head>

<PageHeader
	title="Fund look-through"
	description="Import IFSL workbooks and review the fund snapshots available to client and adviser portals."
/>

<div class="grid gap-5 xl:grid-cols-[minmax(20rem,0.72fr)_minmax(0,1.28fr)]">
	<Card glass>
		<CardHeader>
			<div class="flex items-start gap-3">
				<div
					class="bg-primary/10 text-primary flex size-10 shrink-0 items-center justify-center rounded-xl"
				>
					<FileSpreadsheetIcon class="size-5" />
				</div>
				<div>
					<CardTitle>Import IFSL workbooks</CardTitle>
					<p class="text-muted-foreground mt-1 text-sm">
						Upload one or more .xlsx disclosures. A newer file supersedes the matching dated
						snapshot.
					</p>
				</div>
			</div>
		</CardHeader>
		<CardContent>
			{#if data.canImport}
				<!-- `enctype` is load-bearing, not decoration: without it `enhance` sends the body as
				     URLSearchParams, so each workbook arrives as the string "[object File]" and the
				     action reports "Choose at least one XLSX file"; in dev it throws before the
				     request, which reads as the button doing nothing at all. -->
				<form
					method="POST"
					enctype="multipart/form-data"
					use:enhance={() => {
						localError = null;
						importing = true;
						return async ({ result, update }) => {
							importing = false;
							await update({ reset: false });
							if (result.type === 'success') {
								files = [];
								await invalidateAll();
							}
						};
					}}
					class="space-y-4"
				>
					<FileDropzone
						bind:files
						name="files"
						multiple
						rich
						disabled={importing}
						accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
						formats="XLSX"
						title="Choose IFSL workbooks"
						onreject={(reason) => (localError = reason)}
					/>
					{#if localError || form?.error}
						<p class="text-destructive text-sm" role="alert">{localError ?? form?.error}</p>
					{/if}
					<Button type="submit" disabled={importing || files.length === 0} class="w-full">
						{importing
							? 'Importing…'
							: `Import ${files.length || ''} workbook${files.length === 1 ? '' : 's'}`}
					</Button>
				</form>
			{:else}
				<div class="rounded-xl border border-dashed p-5 text-center">
					<p class="font-medium">Read-only access</p>
					<p class="text-muted-foreground mt-1 text-sm">
						You can review the register and import history, but cannot upload files.
					</p>
				</div>
			{/if}

			{#if results.length}
				<div class="mt-5 border-t pt-5" aria-live="polite">
					<SectionHeading title="Latest import" count={results.length} />
					<ul class="space-y-2">
						{#each results as result (result.filename)}
							<li
								class="bg-muted/40 flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm"
							>
								<span class="min-w-0 truncate">{result.filename}</span>
								<StatusBadge
									tone={result.status === 'imported' || result.status === 'enriched'
										? 'success'
										: result.status === 'rejected'
											? 'danger'
											: 'neutral'}>{humanise(result.status)}</StatusBadge
								>
							</li>
						{/each}
					</ul>
				</div>
			{/if}
		</CardContent>
	</Card>

	<Card glass class="overflow-hidden">
		<CardHeader>
			<div class="flex items-center justify-between gap-4">
				<div>
					<CardTitle>Fund register</CardTitle>
					<p class="text-muted-foreground mt-1 text-sm">
						{data.lookthrough.funds.length} funds with imported look-through data. Coverage is how much
						of the fund is covered by disclosed positions. The remainder is residual cash or unclassified
						exposure.
					</p>
				</div>
				<LayersIcon class="text-muted-foreground size-5" />
			</div>
		</CardHeader>
		<CardContent>
			<DataTable
				columns={fundColumns}
				rows={data.lookthrough.funds}
				getKey={(row) => row.uid}
				label="Look-through funds"
				empty="No fund snapshots have been imported yet."
			>
				{#snippet cell(row, column)}
					{#if column.key === 'name'}<div class="min-w-0">
							<a
								href={`/look-through/${row.uid}`}
								class="hover:text-primary focus-visible:ring-ring block truncate rounded-sm font-medium underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:outline-none"
							>
								{row.name}
							</a>
							<RecordId value={row.fund_code ?? row.uid} />
						</div>
					{:else if column.key === 'isins'}<div
							class="flex items-center gap-1.5 whitespace-nowrap"
							title={row.isins.join(' · ')}
						>
							{#if row.isins.length}
								<Badge variant="outline" class="font-mono text-[11px]">{row.isins[0]}</Badge>
								{#if row.isins.length > 1}
									<span class="text-muted-foreground text-xs tabular-nums"
										>+{row.isins.length - 1}</span
									>
								{/if}
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						</div>
					{:else if column.key === 'latest_as_of'}<span class="whitespace-nowrap"
							>{date(row.latest_as_of)}</span
						>
					{:else if column.key === 'position_count'}<span class="tabular-nums"
							>{row.position_count.toLocaleString('en-GB')}</span
						>
					{:else if column.key === 'coverage'}<Badge
							variant="outline"
							title="Share of the fund accounted for by the disclosed positions"
							>{pct(row.coverage)}</Badge
						>
					{/if}
				{/snippet}
			</DataTable>
		</CardContent>
	</Card>
</div>

<Card glass class="mt-5 overflow-hidden">
	<CardHeader>
		<div class="flex items-center justify-between gap-4">
			<div>
				<CardTitle>Import history</CardTitle>
				<p class="text-muted-foreground mt-1 text-sm">
					Every workbook accepted into the look-through register.
				</p>
			</div>
			<HistoryIcon class="text-muted-foreground size-5" />
		</div>
	</CardHeader>
	<CardContent>
		<ListingToolbar
			class="mb-4"
			bind:query={importQuery}
			placeholder="Search filename or fund"
			dirty={importDirty}
			onclear={clearImportFilters}
			filterDefs={importFilterDefs}
			filterValue={importFilterValue}
			onfilters={onImportFilters}
		/>
		<DataTable
			columns={importColumns}
			rows={filteredImports}
			getKey={(row) => row.uid}
			label="Look-through import history"
			empty={importQuery.trim() || importDirty
				? 'No imports match your filters.'
				: 'Import history will appear here.'}
		>
			{#snippet cell(row, column)}
				{#if column.key === 'filename'}<div class="min-w-0">
						<p class="truncate font-medium" title={row.filename}>{row.filename}</p>
						{#if row.supersedes_uid}<p class="text-muted-foreground text-xs">
								Superseded an earlier snapshot
							</p>{/if}
					</div>
				{:else if column.key === 'fund_name'}<span class="block truncate">{row.fund_name}</span>
				{:else if column.key === 'as_of_date'}<span class="whitespace-nowrap"
						>{date(row.as_of_date)}</span
					>
				{:else if column.key === 'position_count'}<span class="tabular-nums"
						>{row.position_count.toLocaleString('en-GB')}</span
					>
				{:else if column.key === 'imported_at'}<span class="whitespace-nowrap"
						>{date(row.imported_at)}</span
					>
				{/if}
			{/snippet}
		</DataTable>
	</CardContent>
</Card>
