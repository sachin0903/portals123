<script lang="ts">
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		ConfirmDialog,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		DropdownMenu,
		EmptyState,
		humanise,
		Input,
		Label,
		LoadErrorBanner,
		RecordId,
		Sheet,
		slugifyCode,
		StatusBadge,
		Textarea,
		toast
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { ListingOverflow, ListingToolbar, nextUrlFromStart } from '@graphene/ui/book/listing';
	import { goto, invalidateAll } from '$app/navigation';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import WorkflowIcon from '@lucide/svelte/icons/workflow';
	import { flowStatusTone, type FlowOut } from '@graphene/ui/book/journeys';
	import ListingFrame from '$lib/components/listing-frame.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const flows = $derived(data.flows);

	// Seeded once from the URL, then owned by the input — the search navigates with
	// `replaceState`, so no history entry carries a different `q` to fall out of step with.
	// svelte-ignore state_referenced_locally
	let search = $state(data.query ?? '');

	/** The search is the URL's, so the API answers it across every flow. The local filter below
	 *  stays as instant feedback while the round trip is in flight — same needle, same answer. */
	function onSearch(term: string): void {
		void goto(nextUrlFromStart(page.url, { q: term }), {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
	// The search lives in the URL, so clearing it must navigate, not just reset the input.
	function clearSearch(): void {
		search = '';
		void goto(nextUrlFromStart(page.url, { q: '' }), { keepFocus: true, noScroll: true });
	}
	let createOpen = $state(false);
	let creating = $state(false);
	let createError = $state<string | null>(null);

	// New-flow fields.
	let name = $state('');
	let code = $state('');
	let codeEdited = $state(false);
	let description = $state('');

	$effect(() => {
		if (!codeEdited) code = slugifyCode(name);
	});

	const existingCodes = $derived(flows.map((f) => f.code));
	const codeExists = $derived(code.trim().length > 0 && existingCodes.includes(code.trim()));
	const canCreate = $derived(name.trim().length > 0 && code.trim().length > 0 && !codeExists);

	// NOT `flows.length === 0`. The search lives in the URL and the API answers it, so `flows` IS
	// the narrowed list — a search matching nothing makes it empty, and the row count then reports
	// "No flows yet" with a Create button over a filtered view. That is the exact defect this state
	// was rewritten to fix, reintroduced by the discriminator chosen to fix it. Ask whether anything
	// is NARROWING, never how many rows came back.
	const narrowed = $derived(search.trim() !== '');

	const filtered = $derived(
		flows.filter((f) => {
			const q = search.trim().toLowerCase();
			return !q || `${f.name} ${f.code}`.toLowerCase().includes(q);
		})
	);

	function resetCreate() {
		name = '';
		code = '';
		codeEdited = false;
		description = '';
		createError = null;
	}

	// Import. A `<code>.flow.json` another firm (or this one) exported — the API resolves its
	// forms and templates by code and lands it as a draft. What it could not carry comes back as
	// warnings, shown here before the author opens the draft, because a toast would lose them.
	let importOpen = $state(false);
	let importing = $state(false);
	let importError = $state<string | null>(null);
	let importFiles = $state<FileList | undefined>(undefined);
	let importCode = $state('');
	type ImportReport = {
		created: boolean;
		forms: { created: string[]; reused: string[] };
		templates: { created: string[]; reused: string[] };
		warnings: string[];
	};
	let imported = $state<({ uid: string; name: string } & ImportReport) | null>(null);
	const canImport = $derived((importFiles?.length ?? 0) > 0 && !importing);
	// The API wants a slug; typing a name here is the natural mistake, so it is slugified on the
	// way out (the same rule the create sheet applies) and the hint says what will be sent.
	const importCodeSlug = $derived(slugifyCode(importCode));
	const importedSummary = $derived.by(() => {
		if (!imported) return [];
		const lines: string[] = [];
		const made = [...imported.forms.created, ...imported.templates.created];
		const kept = [...imported.forms.reused, ...imported.templates.reused];
		if (made.length) lines.push(`Created ${made.join(', ')}.`);
		if (kept.length) lines.push(`Reused ${kept.join(', ')} already on file.`);
		return lines;
	});
	function resetImport() {
		importFiles = undefined;
		importCode = '';
		importError = null;
		imported = null;
	}

	// Archive / restore. Archiving is the lever the "New journey" picker already respects (it only
	// offers published flows), so a mis-created flow no longer has to stay as permanent clutter.
	let statusOpen = $state(false);
	let statusTarget = $state<FlowOut | null>(null);
	let statusFormEl: HTMLFormElement | null = null;
	const nextStatus = $derived(statusTarget?.status === 'archived' ? 'published' : 'archived');
	function askStatus(flow: FlowOut) {
		statusTarget = flow;
		statusOpen = true;
	}

	let sort = $state<DataTableSort | null>(null);
	const count = {
		sortable: true,
		align: 'center',
		hideBelowWidth: 'sm',
		headerClass: 'w-[14%]',
		defaultDir: 'desc'
	} as const;
	const FLOW_COLUMNS: DataTableColumn<FlowOut>[] = [
		{ key: 'name', header: 'Flow', sortable: true, sortDefault: true, headerClass: 'w-[46%]' },
		{ ...count, key: 'published', header: 'Published', sortValue: (f) => f.published_version },
		{ ...count, key: 'versions', header: 'Versions', sortValue: (f) => f.version_count },
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[16%]',
			sortValue: (f) => humanise(f.status)
		},
		{ key: 'actions', header: '', align: 'right', headerClass: 'w-[10%] min-w-24' }
	];
	const columns = $derived(data.canManage ? FLOW_COLUMNS : FLOW_COLUMNS.slice(0, -1));
</script>

{#snippet createFirstFlow()}
	<Button size="sm" onclick={() => (createOpen = true)}>New flow</Button>
{/snippet}

{#snippet clearFlowSearch()}
	<Button variant="outline" size="sm" onclick={clearSearch}>Clear search</Button>
{/snippet}

<svelte:head><title>Flows · Platform</title></svelte:head>

{#snippet listingActions()}
	<ListingOverflow>
		<DropdownMenu.Item onclick={() => (createOpen = true)}>New flow</DropdownMenu.Item>
		<DropdownMenu.Item onclick={() => (importOpen = true)}>Import flow…</DropdownMenu.Item>
		<DropdownMenu.Item>
			{#snippet child({ props })}
				<a {...props} href="/flows/dead-letters">Failed journey actions</a>
			{/snippet}
		</DropdownMenu.Item>
	</ListingOverflow>
{/snippet}

{#snippet loadBanner()}
	{#if data.loadError}
		<LoadErrorBanner message="Couldn’t load flows." onRetry={() => invalidateAll()} />
	{/if}
{/snippet}

<ListingFrame banner={loadBanner}>
	<ListingToolbar
		class="shrink-0"
		bind:query={search}
		placeholder="Search flows…"
		dirty={narrowed}
		debounce={250}
		onsearch={onSearch}
		onclear={clearSearch}
		actions={data.canManage ? listingActions : undefined}
	/>

	<!-- `&& !data.loadError`: a failed read empties `flows` too, so without this the retry banner
	     above carried a neutral "No flows yet" card and a Create button underneath it. The banner
	     states the failure and owns the retry; the honest thing here is to say nothing. -->
	{#if filtered.length === 0 && !data.loadError}
		<!-- Both branches named an act and offered neither. The search miss is the worse of the
		     two: the term lives in the URL, so a reader who arrived on a shared link has no idea
		     the list is narrowed at all. -->
		<EmptyState
			icon={WorkflowIcon}
			title={narrowed ? `No flows match “${search.trim()}”` : 'No flows yet'}
			description={narrowed
				? 'Try a different search, or clear it to see every flow.'
				: 'A flow is a multi-step process your firm follows, such as onboarding, an annual review or a transfer.'}
			action={narrowed ? clearFlowSearch : data.canManage ? createFirstFlow : undefined}
			chromeless
			compact
		/>
	{:else}
		<DataTable
			{columns}
			rows={filtered}
			bind:sort
			getKey={(flow) => flow.uid}
			label="Flows"
			scroll
			fixed
		>
			{#snippet cell(flow, column)}
				{#if column.key === 'name'}
					<a href={`/flows/${flow.uid}`} class="flex min-w-0 items-center gap-3">
						<div class="bg-brand/10 flex size-9 shrink-0 items-center justify-center rounded-lg">
							<WorkflowIcon class="text-brand size-4.5" />
						</div>
						<div class="min-w-0 flex-1">
							<p class="truncate text-xs font-medium">{flow.name}</p>
							<RecordId value={flow.code} />
						</div>
					</a>
				{:else if column.key === 'published'}
					{#if flow.published_version != null}
						<span class="tabular-nums">v{flow.published_version}</span>
					{:else}
						<span class="text-muted-foreground text-xs">—</span>
					{/if}
				{:else if column.key === 'versions'}
					<span class="text-muted-foreground text-xs tabular-nums">{flow.version_count}</span>
				{:else if column.key === 'status'}
					<StatusBadge tone={flowStatusTone(flow.status)}>{humanise(flow.status)}</StatusBadge>
				{:else if column.key === 'actions'}
					<Button
						variant="ghost"
						size="sm"
						class="text-muted-foreground"
						onclick={() => askStatus(flow)}
					>
						{flow.status === 'archived' ? 'Restore' : 'Archive'}
					</Button>
				{/if}
			{/snippet}
		</DataTable>
	{/if}
</ListingFrame>

<Sheet.Root bind:open={createOpen} onOpenChange={(v) => !v && resetCreate()}>
	<Sheet.Content class="flex flex-col gap-0">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title class="flex items-center gap-2">
				<WorkflowIcon class="text-primary size-4" /> New flow
			</Sheet.Title>
		</Sheet.Header>
		<form
			method="POST"
			action="?/create"
			class="flex min-h-0 flex-1 flex-col"
			use:enhance={() => {
				creating = true;
				createError = null;
				return async ({ result }) => {
					creating = false;
					if (result.type === 'success' && result.data?.uid) {
						createOpen = false;
						const uid = result.data.uid as string;
						resetCreate();
						await invalidateAll();
						goto(`/flows/${uid}`);
					} else if (result.type === 'failure') {
						createError = (result.data?.error as string) ?? 'Could not create the flow.';
					}
				};
			}}
		>
			<div class="flex-1 space-y-4 overflow-y-auto px-6 py-5">
				{#if createError}
					<Alert variant="destructive">
						<AlertDescription>{createError}</AlertDescription>
					</Alert>
				{/if}
				<div class="space-y-1.5">
					<Label for="flow-name">Name</Label>
					<Input
						id="flow-name"
						name="name"
						placeholder="e.g. Client Onboarding"
						bind:value={name}
					/>
				</div>
				<div class="space-y-1.5">
					<Label for="flow-code">Reference code</Label>
					<Input
						id="flow-code"
						name="code"
						class="font-mono text-xs"
						placeholder="client_onboarding"
						bind:value={code}
						oninput={() => (codeEdited = true)}
						aria-invalid={codeExists}
					/>
					{#if codeExists}
						<p class="text-destructive text-xs">A flow with this code already exists.</p>
					{:else}
						<p class="text-muted-foreground text-xs">Auto-filled from the name.</p>
					{/if}
				</div>
				<div class="space-y-1.5">
					<Label for="flow-entity">
						Entity <span class="text-muted-foreground font-normal">(optional)</span>
					</Label>
					<Input
						id="flow-entity"
						name="entity"
						placeholder="e.g. applicant"
						class="font-mono text-xs"
					/>
				</div>
				<div class="space-y-1.5">
					<Label for="flow-desc">
						Description <span class="text-muted-foreground font-normal">(optional)</span>
					</Label>
					<Textarea
						id="flow-desc"
						name="description"
						rows={3}
						placeholder="What this flow captures…"
						bind:value={description}
					/>
				</div>
			</div>
			<div class="flex items-center justify-between border-t px-6 py-4">
				<Button type="button" variant="ghost" onclick={() => (createOpen = false)}>Cancel</Button>
				<Button type="submit" disabled={!canCreate || creating}>
					{creating ? 'Creating…' : 'Create flow'}
				</Button>
			</div>
		</form>
	</Sheet.Content>
</Sheet.Root>

<Sheet.Root bind:open={importOpen} onOpenChange={(v) => !v && resetImport()}>
	<Sheet.Content class="flex flex-col gap-0">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title class="flex items-center gap-2">
				<UploadIcon class="text-primary size-4" /> Import flow
			</Sheet.Title>
			<Sheet.Description>
				A flow export (<span class="font-mono text-xs">.flow.json</span>) lands here as a draft,
				with the forms and templates it names. Nothing is published until you publish it.
			</Sheet.Description>
		</Sheet.Header>
		{#if imported}
			<div class="flex-1 space-y-4 overflow-y-auto px-6 py-5" data-testid="import-result">
				<Alert>
					<AlertTitle>{imported.name} is ready as a draft</AlertTitle>
					<AlertDescription>
						{imported.created
							? 'A new flow was created.'
							: 'A flow with this code already existed; the file was saved as its next draft version.'}
						{#each importedSummary as line, i (i)}
							{' '}{line}
						{/each}
					</AlertDescription>
				</Alert>
				{#if imported.warnings.length}
					<Alert variant="warning">
						<AlertTitle>Check these before publishing</AlertTitle>
						<AlertDescription>
							<ul class="list-disc space-y-1 pl-4">
								{#each imported.warnings as warning, i (i)}
									<li>{warning}</li>
								{/each}
							</ul>
						</AlertDescription>
					</Alert>
				{/if}
			</div>
			<div class="flex items-center justify-between border-t px-6 py-4">
				<Button type="button" variant="ghost" onclick={() => (importOpen = false)}>Close</Button>
				<Button href={`/flows/${imported.uid}`} onclick={() => (importOpen = false)}>
					Open the draft
				</Button>
			</div>
		{:else}
			<form
				method="POST"
				action="?/import"
				enctype="multipart/form-data"
				class="flex min-h-0 flex-1 flex-col"
				use:enhance={() => {
					importing = true;
					importError = null;
					return async ({ result }) => {
						importing = false;
						if (result.type === 'success' && result.data?.uid) {
							const data = result.data as { uid: string; name: string; imported: ImportReport };
							await invalidateAll();
							if (data.imported.warnings.length === 0) {
								importOpen = false;
								resetImport();
								toast.success(`${data.name} imported as a draft`);
								goto(`/flows/${data.uid}`);
							} else {
								imported = { uid: data.uid, name: data.name, ...data.imported };
							}
						} else if (result.type === 'failure') {
							importError = (result.data?.error as string) ?? 'Could not import the flow.';
						}
					};
				}}
			>
				<div class="flex-1 space-y-4 overflow-y-auto px-6 py-5">
					{#if importError}
						<Alert variant="destructive">
							<AlertDescription>{importError}</AlertDescription>
						</Alert>
					{/if}
					<div class="space-y-1.5">
						<Label for="flow-bundle">Flow export</Label>
						<Input
							id="flow-bundle"
							name="bundle"
							type="file"
							accept="application/json,.json"
							bind:files={importFiles}
						/>
					</div>
					<div class="space-y-1.5">
						<Label for="flow-import-code">
							Reference code <span class="text-muted-foreground font-normal">(optional)</span>
						</Label>
						<Input
							id="flow-import-code"
							class="font-mono text-xs"
							placeholder="Keep the file’s code"
							bind:value={importCode}
						/>
						<input type="hidden" name="code" value={importCodeSlug} />
						<p class="text-muted-foreground text-xs">
							{#if importCodeSlug && importCodeSlug !== importCode.trim()}
								Imported as <span class="font-mono">{importCodeSlug}</span>.
							{:else}
								Set one to import beside a flow you already have rather than as its next draft.
							{/if}
						</p>
					</div>
				</div>
				<div class="flex items-center justify-between border-t px-6 py-4">
					<Button type="button" variant="ghost" onclick={() => (importOpen = false)}>Cancel</Button>
					<Button type="submit" disabled={!canImport}>
						{importing ? 'Importing…' : 'Import flow'}
					</Button>
				</div>
			</form>
		{/if}
	</Sheet.Content>
</Sheet.Root>

<!-- Archive / restore a flow. Running journeys are unaffected — they run on a pinned version. -->
<form
	method="POST"
	action="?/setStatus"
	bind:this={statusFormEl}
	class="hidden"
	use:enhance={() => {
		return async ({ result, update }) => {
			statusOpen = false;
			if (result.type === 'success') {
				toast.success(nextStatus === 'archived' ? 'Flow archived' : 'Flow restored');
				await invalidateAll();
			} else {
				toast.error(
					result.type === 'failure'
						? String(result.data?.error ?? 'Something went wrong.')
						: 'Something went wrong.'
				);
				await update();
			}
		};
	}}
>
	<input type="hidden" name="uid" value={statusTarget?.uid ?? ''} />
	<input type="hidden" name="status" value={nextStatus} />
</form>

<ConfirmDialog
	bind:open={statusOpen}
	title={nextStatus === 'archived' ? 'Archive this flow?' : 'Restore this flow?'}
	description={nextStatus === 'archived'
		? 'It stops being offered when starting a new journey. Journeys already running finish on the version they started with, and you can restore it later.'
		: 'It becomes available again when starting a new journey.'}
	confirmLabel={nextStatus === 'archived' ? 'Archive' : 'Restore'}
	destructive={nextStatus === 'archived'}
	onconfirm={() => statusFormEl?.requestSubmit()}
/>
