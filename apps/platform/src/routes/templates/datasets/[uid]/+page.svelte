<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardAction,
		CardContent,
		CardHeader,
		CardTitle,
		Checkbox,
		ConfirmDialog,
		DataTable,
		EmptyState,
		PageHeader,
		SelectMenu,
		StatusBadge,
		Sheet,
		Input,
		Label,
		Textarea,
		toast,
		DATASET_FORMAT_META,
		DATASET_SCOPE_META,
		type DataTableColumn,
		type SortValue
	} from '@graphene/ui';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import PlayIcon from '@lucide/svelte/icons/play';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import BackButton from '$lib/components/back-button.svelte';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import { DatasetDataGrid, toCsvLine } from '@graphene/ui/dataset-authoring';
	import DatasetParams from '$lib/components/datasets/dataset-params.svelte';
	import { formatDate } from '@graphene/ui';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const ds = $derived(data.dataset);
	const sample = $derived(data.sample ?? undefined);
	const columns = $derived(data.columns ?? []);
	const versions = $derived(data.versions ?? []);
	const canManage = $derived(data.canManage);
	const fm = $derived(ds ? DATASET_FORMAT_META[ds.format] : null);
	const scope = $derived(ds ? DATASET_SCOPE_META[ds.scope] : null);
	// Rows whose cell count doesn't match the columns — a sign the data was corrupted at creation
	// (a comma inside a value split it). Surfaced so a bad dataset doesn't look fine.
	const raggedSample = $derived(
		sample ? sample.rows.filter((r) => r.length !== sample.columns.length).length : 0
	);

	// Preview rows are bare arrays; `i` is the row key. A computed width needs `headerStyle`.
	type PreviewRow = { i: number; cells: unknown[] };
	function preview(names: string[], rows: unknown[][]) {
		const headerStyle = `width: ${(100 / names.length).toFixed(4)}%`;
		const columns = names.map((header, idx): DataTableColumn<PreviewRow> => ({
			key: String(idx),
			header,
			sortable: true,
			sortValue: (row) => row.cells[idx] as SortValue,
			headerClass: 'text-[11px] font-semibold whitespace-nowrap',
			cellClass: 'text-xs',
			headerStyle
		}));
		return { columns, rows: rows.map((cells, i) => ({ i, cells })) };
	}
	const samplePreview = $derived(preview(sample?.columns ?? [], sample?.rows ?? []));
	const headerMeta = $derived(
		[scope?.label, ds?.publishedVersionNumber ? `v${ds.publishedVersionNumber}` : null]
			.filter(Boolean)
			.join(' · ')
	);

	// Global or per-client only — 'collection' was selectable but meant nothing anywhere.
	// 'Global' is only offered when the author may actually declare firm-wide SQL: a global SQL
	// dataset is resolved across the whole firm at generate, so the API requires 'contacts:read_all'
	// (403 otherwise). An already-global dataset keeps the option — re-selecting its current scope
	// changes nothing, so it isn't refused — but a per-client one can't be flipped without it.
	const canFirmWide = $derived(data.canFirmWide ?? false);
	const canGoGlobal = $derived(canFirmWide || ds?.scope === 'global');
	const scopeItems = $derived(
		canGoGlobal
			? [
					{ value: 'global', label: 'Global (same for every client)' },
					{ value: 'subject', label: 'Subject (per client)' }
				]
			: [{ value: 'subject', label: 'Subject (per client)' }]
	);

	// ── Edit details (metadata) ──────────────────────────────────────────────────
	let detailsOpen = $state(false);
	let detailsError = $state<string | null>(null);
	let detailsSaving = $state(false);
	let editScope = $state('global');

	// Seed the selects from the current values so the Sheet always opens in sync.
	function openDetails(): void {
		if (!ds) return;
		detailsError = null;
		editScope = ds.scope;
		detailsOpen = true;
	}

	// ── Delete (guarded by a ConfirmDialog; the form still posts ?/delete) ───────
	const usedByTemplates = $derived(data.usedByTemplates ?? 0);
	let deleteOpen = $state(false);
	let deleting = $state(false);
	let deleteForm = $state<HTMLFormElement>();

	// ── Run query (resolve the dataset now) ─────────────────────────────────────
	const subjects = $derived(data.subjects ?? []);
	const subjectItems = $derived(
		subjects.map((s) => ({ value: String(s.contact_id), label: s.name }))
	);
	let runSubject = $state('');
	let running = $state(false);
	const run = $derived(form?.run);
	const runError = $derived(form?.runError);
	const runPreview = $derived(preview(run?.columns ?? [], run?.rows.slice(0, 50) ?? []));

	// ── Edit data (publishes a new version) ────────────────────────────────────────
	let dataOpen = $state(false);
	let dataError = $state<string | null>(null);
	let dataSaving = $state(false);
	let editColumns = $state<string[]>(['Column 1']);
	let editRows = $state<string[][]>([['']]);
	const editFilledRows = $derived(editRows.filter((r) => r.some((c) => c.trim() !== '')));
	const editColumnsSerialized = $derived(toCsvLine(editColumns));
	const editRowsSerialized = $derived(editFilledRows.map(toCsvLine).join('\n'));

	// The chosen settings for a provided dataset's new version, seeded from what is live.
	let editParams = $state<Record<string, string>>({});
	/** The catalogue entry this dataset draws on — the source of its settings and its columns. */
	const entry = $derived(
		(data.catalogue ?? []).find((e) => e.key === (ds?.catalogueKey ?? '')) ?? null
	);

	// Seed the editor from the current version before opening, so it starts from what's live.
	function openEditData(): void {
		dataError = null;
		if (ds?.format === 'catalogue') {
			// The stored tokens, falling back to the entry's own defaults for anything unset — so a
			// dataset configured before a setting existed opens with that setting's default rather
			// than blank.
			const stored = versions[0]?.catalogue?.params ?? {};
			editParams = Object.fromEntries(
				(entry?.params ?? []).map((prm) => [
					prm.name,
					String(stored[prm.name] ?? prm.default ?? '')
				])
			);
		} else if (ds?.format === 'inline') {
			const cur = versions[0]?.inline ?? sample;
			editColumns = cur?.columns.length ? [...cur.columns] : ['Column 1'];
			editRows = cur?.rows.length
				? cur.rows.map((r) => r.map((c) => String(c ?? '')))
				: [editColumns.map(() => '')];
		}
		dataOpen = true;
	}
</script>

{#snippet previewTable(p: ReturnType<typeof preview>, label: string)}
	<DataTable {...p} getKey={(r) => r.i} fixed {label} empty="This dataset returned no rows.">
		{#snippet cell(row, column)}<span>{row.cells[Number(column.key)] ?? '—'}</span>{/snippet}
	</DataTable>
{/snippet}

<svelte:head><title>{ds?.name ?? 'Dataset'} · Platform</title></svelte:head>

{#if !ds || !fm || !scope}
	<EmptyState icon={DatabaseIcon} title="Dataset not found">
		{#snippet action()}
			<Button variant="outline" onclick={() => goto('/templates/datasets')}>
				<ArrowLeftIcon class="mr-1 size-4" />
				Back to datasets
			</Button>
		{/snippet}
	</EmptyState>
{:else}
	<div>
		<BackButton fallback="/templates/datasets" fallbackLabel="datasets" class="mb-4" />

		<!-- Header -->
		<PageHeader title={ds.name} description={headerMeta || undefined}>
			{#snippet actions()}
				<Badge class="{fm.bgColor} {fm.textColor}">{fm.label}</Badge>
				<StatusBadge tone={ds.enabled ? 'success' : 'neutral'}>
					{ds.enabled ? 'Enabled' : 'Disabled'}
				</StatusBadge>
				{#if canManage}
					<Button variant="outline" size="sm" onclick={openDetails}>
						<PencilIcon class="mr-1.5 size-4" /> Edit details
					</Button>
					{#if ds.format !== 'csv'}
						<Button variant="outline" size="sm" onclick={openEditData}>
							<PencilIcon class="mr-1.5 size-4" /> Edit data
						</Button>
					{/if}
					<form
						method="POST"
						action="?/delete"
						bind:this={deleteForm}
						use:enhance={() => {
							deleting = true;
							return async ({ result, update }) => {
								deleting = false;
								if (result.type === 'failure')
									toast.error((result.data?.error as string) ?? 'Could not delete the dataset.');
								await update({ reset: false });
							};
						}}
					>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							class="text-destructive"
							disabled={deleting}
							onclick={() => (deleteOpen = true)}
						>
							<Trash2Icon class="mr-1.5 size-4" />
							{deleting ? 'Deleting…' : 'Delete'}
						</Button>
					</form>
				{/if}
			{/snippet}
		</PageHeader>

		<div class="grid gap-4 lg:grid-cols-[1fr_320px]">
			<div class="space-y-4">
				<!-- Data / schema -->
				<Card glass class="h-fit gap-0 overflow-hidden py-0">
					<CardHeader class="border-b px-5 py-3.5 [.border-b]:pb-3.5">
						<CardTitle class="text-sm">Data</CardTitle>
						{#if sample}
							<CardAction>
								<span class="text-muted-foreground text-xs">
									{sample.rows.length} sample row{sample.rows.length === 1 ? '' : 's'}
								</span>
							</CardAction>
						{/if}
					</CardHeader>
					{#if raggedSample}
						<Alert variant="warning" class="rounded-none border-0 border-b px-5">
							<TriangleAlertIcon class="size-4" />
							<AlertDescription>
								{raggedSample} row{raggedSample === 1 ? '' : 's'} don't match the {sample?.columns
									.length} columns. A value containing a comma was likely split when this dataset was
								created. Use <strong>Edit data</strong> to fix it (the grid keeps values intact).
							</AlertDescription>
						</Alert>
					{/if}
					{#if sample}
						{@render previewTable(samplePreview, 'Dataset preview')}
						<p class="text-muted-foreground border-t px-5 py-2.5 text-xs">
							Inline data, used as entered when a document is created.
						</p>
					{:else if columns.length}
						<!-- Live sources (sql/csv) resolve per document; show the known column schema. -->
						<div class="px-5 py-4">
							<p class="text-muted-foreground mb-2 text-xs">
								Resolved live at render time. Columns:
							</p>
							<div class="flex flex-wrap gap-1.5">
								{#each columns as col (col)}
									<code class="bg-muted rounded px-2 py-0.5 font-mono text-xs">{col}</code>
								{/each}
							</div>
						</div>
					{:else}
						<div class="p-5">
							<EmptyState
								compact
								title="No column schema for this dataset."
								description={ds.format === 'catalogue'
									? 'The columns of a SELECT * query can’t be read from the text. Run the query below to see the real columns, or list them explicitly (or add sample rows) so the editor’s field pickers have something to offer.'
									: 'Upload a CSV file for this dataset, or add sample rows.'}
							/>
						</div>
					{/if}
				</Card>

				<!-- Run query — resolve the dataset now, with your own client access. The one way to
			     answer "does this dataset actually return what I expect?" from the dataset itself. -->
				{#if ds.format !== 'inline'}
					<Card glass class="h-fit gap-0 overflow-hidden py-0">
						<CardHeader class="border-b px-5 py-3.5 [.border-b]:pb-3.5">
							<CardTitle class="text-sm">Run query</CardTitle>
							<CardAction>
								<span class="text-muted-foreground text-xs">
									Read-only · your own client access · {run?.rowLimit
										? `${run.rowLimit.toLocaleString()}-row cap`
										: 'row-capped'}
								</span>
							</CardAction>
						</CardHeader>
						<form
							method="POST"
							action="?/runQuery"
							use:enhance={() => {
								running = true;
								return async ({ result, update }) => {
									running = false;
									if (result.type === 'failure')
										toast.error((result.data?.runError as string) ?? 'The query didn’t run.');
									await update({ reset: false });
								};
							}}
						>
							<div class="flex flex-wrap items-end gap-3 px-5 py-4">
								{#if ds.scope === 'subject'}
									<div class="min-w-56 flex-1 space-y-1.5">
										<Label for="run-subject">Client</Label>
										{#if subjectItems.length}
											<SelectMenu
												id="run-subject"
												name="contact_id"
												items={subjectItems}
												bind:value={runSubject}
												placeholder="Choose a client…"
											/>
										{:else}
											<p class="text-muted-foreground text-xs">
												{data.subjectsError
													? 'The client list couldn’t be loaded, so this per-client dataset can’t be run here.'
													: 'You have no clients in scope, so this per-client dataset can’t be run here.'}
											</p>
										{/if}
									</div>
								{/if}
								<Button
									type="submit"
									variant="outline"
									disabled={running || (ds.scope === 'subject' && !runSubject)}
								>
									<PlayIcon class="mr-1.5 size-4" />
									{running ? 'Running…' : 'Run query'}
								</Button>
							</div>

							{#if runError}
								<Alert variant="destructive" class="rounded-none border-0 border-t px-5">
									<AlertDescription>{runError}</AlertDescription>
								</Alert>
							{:else if run}
								<!-- ONE alert carrying the API's own sentences (`notice` is every caveat the resolve
								     reported), warning-styled when the result was clipped. This used to be
								     `{#if truncated}…{:else if notice}`, which HID the second caveat — e.g. "this
								     firm-wide dataset ran under your own client access, so a document will show
								     more" — whenever the result was also clipped. -->
								{#if run.notice || run.truncated}
									<Alert
										variant={run.truncated ? 'warning' : 'default'}
										class="rounded-none border-0 border-t px-5"
									>
										{#if run.truncated}<TriangleAlertIcon class="size-4" />{/if}
										<AlertDescription>
											{run.notice ??
												`Clipped at ${run.rowLimit.toLocaleString()} rows. Aggregate or filter the query.`}{run.truncated
												? ' A render uses the same cap.'
												: ''}
										</AlertDescription>
									</Alert>
								{/if}
								<div class="border-t">{@render previewTable(runPreview, 'Run query results')}</div>
								{#if run.rows.length}
									<p class="text-muted-foreground border-t px-5 py-2.5 text-xs">
										{run.rows.length.toLocaleString()} row{run.rows.length === 1 ? '' : 's'}{run
											.rows.length > 50
											? '. Showing the first 50'
											: ''}
									</p>
								{/if}
							{:else}
								<p class="text-muted-foreground border-t px-5 py-2.5 text-center text-xs">
									This dataset has never been run.
								</p>
							{/if}
						</form>
					</Card>
				{/if}
			</div>

			<!-- Details sidebar -->
			<div class="space-y-3">
				<Card glass>
					<CardHeader>
						<CardTitle class="text-sm">Details</CardTitle>
					</CardHeader>
					<CardContent>
						<dl class="space-y-3">
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground text-xs">Code</dt>
								<dd>
									<code class="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">{ds.code}</code>
								</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground text-xs">Format</dt>
								<dd class="text-xs">{fm.label}</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground text-xs">Scope</dt>
								<dd class="text-xs">{scope.label}</dd>
							</div>
							{#if ds.publishedVersionNumber}
								<div class="flex justify-between gap-3">
									<dt class="text-muted-foreground text-xs">Published version</dt>
									<dd class="text-xs">v{ds.publishedVersionNumber}</dd>
								</div>
							{/if}
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground text-xs">Created by</dt>
								<dd class="text-xs">{ds.createdBy}</dd>
							</div>
							<div class="flex justify-between gap-3">
								<dt class="text-muted-foreground text-xs">Created</dt>
								<dd class="text-xs">{formatDate(ds.createdAt)}</dd>
							</div>
						</dl>
					</CardContent>
				</Card>

				{#if ds.description}
					<Card glass>
						<CardHeader>
							<CardTitle class="text-sm">Description</CardTitle>
						</CardHeader>
						<CardContent>
							<p class="text-muted-foreground text-sm leading-relaxed">{ds.description}</p>
						</CardContent>
					</Card>
				{/if}

				<!-- Version history — each data edit publishes a new version; the highest is current. -->
				<Card glass>
					<CardHeader>
						<CardTitle class="flex items-center gap-2 text-sm">
							<HistoryIcon class="text-muted-foreground size-4 shrink-0" />
							Version history
						</CardTitle>
					</CardHeader>
					<CardContent>
						{#if versions.length === 0}
							<EmptyState compact title="No versions yet." />
						{:else}
							<div class="max-h-[24rem] space-y-2 overflow-y-auto">
								{#each versions as v (v.uid)}
									{@const isCurrent = v.versionNumber === ds.publishedVersionNumber}
									<div class="flex items-start gap-2.5 rounded-lg border p-2.5">
										<span class="w-8 shrink-0 font-mono text-xs font-semibold"
											>v{v.versionNumber}</span
										>
										<div class="min-w-0 flex-1">
											{#if isCurrent}
												<span class="text-primary text-[11px] font-semibold">Current</span>
											{/if}
											<p class="text-muted-foreground text-xs">
												{formatDate(v.publishedAt ?? v.createdAt)}{v.createdBy
													? ` · ${v.createdBy}`
													: ''}
											</p>
										</div>
									</div>
								{/each}
							</div>
						{/if}
					</CardContent>
				</Card>
			</div>
		</div>
	</div>

	<!-- The count is disclosed up front: while templates bind this dataset the API refuses the
	     deletion (409), because they would otherwise keep rendering with no rows at all. -->
	<ConfirmDialog
		bind:open={deleteOpen}
		title={usedByTemplates > 0 ? `Can’t delete “${ds.name}”` : `Delete “${ds.name}”?`}
		description={usedByTemplates > 0
			? `${usedByTemplates} template${usedByTemplates === 1 ? '' : 's'} still use this dataset. ` +
				'Unlink it from them first, or disable the dataset instead. Deleting it would leave those ' +
				'templates rendering with no data.'
			: 'This removes the dataset and its versions. Documents already generated keep the data they were built with.'}
		confirmLabel={usedByTemplates > 0 ? 'Disable instead' : 'Delete'}
		destructive={usedByTemplates === 0}
		onconfirm={() => (usedByTemplates > 0 ? openDetails() : deleteForm?.requestSubmit())}
	/>

	<!-- Edit details (metadata) — format is immutable, so it isn't editable here. -->
	<Sheet.Root bind:open={detailsOpen}>
		<Sheet.Content class="flex w-full flex-col gap-0">
			<Sheet.Header class="border-b px-6 pb-4 pt-6">
				<Sheet.Title>Edit details</Sheet.Title>
			</Sheet.Header>
			<form
				method="POST"
				action="?/updateDetails"
				class="flex min-h-0 flex-1 flex-col"
				use:enhance={() => {
					detailsError = null;
					detailsSaving = true;
					return async ({ result }) => {
						detailsSaving = false;
						if (result.type === 'success') {
							detailsOpen = false;
							toast.success('Details saved.');
							await invalidateAll();
						} else if (result.type === 'failure') {
							detailsError = (result.data?.error as string) ?? 'Could not save the details.';
						}
					};
				}}
			>
				<div class="flex-1 space-y-4 overflow-y-auto px-6 py-5">
					{#if detailsError}
						<Alert variant="destructive">
							<AlertDescription>{detailsError}</AlertDescription>
						</Alert>
					{/if}
					<div class="space-y-1.5">
						<Label for="ds-name">Name</Label>
						<Input id="ds-name" name="name" value={ds.name} required />
					</div>
					<div class="space-y-1.5">
						<Label for="ds-desc"
							>Description <span class="text-muted-foreground">(optional)</span></Label
						>
						<Textarea id="ds-desc" name="description" rows={2} value={ds.description ?? ''} />
					</div>
					<div class="space-y-1.5">
						{#if ds.format === 'catalogue'}
							<Label for="ds-scope">Scope</Label>
							<SelectMenu id="ds-scope" name="scope" items={scopeItems} bind:value={editScope} />
							<p class="text-muted-foreground text-xs">
								{editScope === 'subject'
									? 'The query must filter on :subject_id for the document’s client.'
									: 'The query must not use :subject_id. It resolves across the whole firm at generate.'}
							</p>
							{#if !canGoGlobal}
								<p class="text-muted-foreground text-xs">
									Firm-wide (global) isn’t available to you: it runs the query across every client
									in the firm when a document is generated, which needs the “Read all clients”
									permission as well as dataset authoring.
								</p>
							{/if}
						{:else}
							<!-- Scope isn't editable on a non-SQL dataset: only a query can be parameterised by
							     the document's client, so an inline/CSV source is always global (the API
							     refuses 'subject' on one). No `name`, so the PATCH omits scope entirely. -->
							<Label>Scope</Label>
							<p class="text-xs">{scope.label}</p>
							<p class="text-muted-foreground text-xs">
								A {ds.format} source is the same rows for every client, so it can't be per-client. Only
								a SQL dataset can bind <code>:subject_id</code>.
							</p>
						{/if}
					</div>
					<div class="flex items-center gap-2">
						<Checkbox id="ds-enabled" name="enabled" checked={ds.enabled} />
						<Label for="ds-enabled" class="font-normal">
							Enabled (templates can use this dataset)
						</Label>
					</div>
					<p class="text-muted-foreground text-xs">
						Format is fixed at creation. Use a new dataset for a different format. Availability
						follows the format ({ds.format === 'sql'
							? 'the query runs for each document'
							: 'the stored rows remain fixed'}).
					</p>
				</div>
				<div class="flex items-center justify-between border-t px-6 py-4">
					<Button type="button" variant="ghost" onclick={() => (detailsOpen = false)}>Cancel</Button
					>
					<Button type="submit" disabled={detailsSaving}>
						{detailsSaving ? 'Saving…' : 'Save details'}
					</Button>
				</div>
			</form>
		</Sheet.Content>
	</Sheet.Root>

	<!-- Edit data — publishes a NEW version; generated documents keep their snapshot, so past
	     documents are unaffected. Inline uses the grid; SQL edits the query. -->
	<Sheet.Root bind:open={dataOpen}>
		<Sheet.Content class="flex w-full flex-col gap-0">
			<Sheet.Header class="border-b px-6 pb-4 pt-6">
				<Sheet.Title>Edit data</Sheet.Title>
				<Sheet.Description>
					Saving publishes version v{(ds.publishedVersionNumber ?? versions.length) + 1}. Existing
					documents keep the data they were generated with.
				</Sheet.Description>
			</Sheet.Header>
			<form
				method="POST"
				action="?/addVersion"
				class="flex min-h-0 flex-1 flex-col"
				use:enhance={() => {
					dataError = null;
					dataSaving = true;
					return async ({ result }) => {
						dataSaving = false;
						if (result.type === 'success') {
							dataOpen = false;
							toast.success('New version published.');
							await invalidateAll();
						} else if (result.type === 'failure') {
							dataError = (result.data?.error as string) ?? 'Could not save the data.';
						}
					};
				}}
			>
				<input type="hidden" name="format" value={ds.format} />
				<div class="flex-1 space-y-3 overflow-y-auto px-6 py-5">
					{#if dataError}
						<Alert variant="destructive">
							<AlertDescription>{dataError}</AlertDescription>
						</Alert>
					{/if}
					{#if ds.format === 'catalogue'}
						{#if entry}
							<p class="text-muted-foreground text-sm">{entry.description}</p>
							{#if entry.params.length > 0}
								<DatasetParams params={entry.params} bind:values={editParams} />
							{:else}
								<p class="text-muted-foreground text-sm">This dataset has no settings to change.</p>
							{/if}
							<input type="hidden" name="catalogue_key" value={entry.key} />
						{:else}
							<Alert variant="destructive">
								<AlertDescription>
									This dataset uses a provided dataset this version of the platform no longer
									offers, so its settings can't be edited here.
								</AlertDescription>
							</Alert>
						{/if}
					{:else}
						<DatasetDataGrid bind:columns={editColumns} bind:rows={editRows} id="ds-edit-grid" />
						<input type="hidden" name="columns" value={editColumnsSerialized} />
						<input type="hidden" name="rows" value={editRowsSerialized} />
					{/if}
				</div>
				<div class="flex items-center justify-between border-t px-6 py-4">
					<Button type="button" variant="ghost" onclick={() => (dataOpen = false)}>Cancel</Button>
					<Button type="submit" disabled={dataSaving}>
						{dataSaving ? 'Publishing…' : 'Publish new version'}
					</Button>
				</div>
			</form>
		</Sheet.Content>
	</Sheet.Root>
{/if}
