<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		ConfirmDialog,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		DOC_TYPE_META,
		DropdownMenu,
		type DocumentType,
		EmptyState,
		type Form,
		FORM_STATUS_META,
		type FormEntityType,
		type FormStatus,
		humanise,
		Input,
		Label,
		ListingSkeleton,
		LoadErrorBanner,
		RecordId,
		SelectMenu,
		Sheet,
		slugifyCode,
		StatusBadge,
		Streamed,
		Textarea,
		toast,
		TruncatedText
	} from '@graphene/ui';
	import { deserialize, enhance } from '$app/forms';
	import { page } from '$app/state';
	import {
		ListingOverflow,
		ListingToolbar,
		nextUrlFromStart,
		type FilterDef
	} from '@graphene/ui/book/listing';
	import { goto, invalidateAll } from '$app/navigation';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import ArchiveIcon from '@lucide/svelte/icons/archive';
	import { formatDate } from '@graphene/ui';
	import ListingFrame from '$lib/components/listing-frame.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The rows are STREAMED (`data.book`), so everything derived from them lives inside the
	// `Streamed` snippet below as `{@const}`s — `filtered` for the table and `existingCodes` for
	// the create sheet's duplicate check — rather than up here, where there are no rows yet.
	let statusFilter = $state<FormStatus[]>([]);
	let entityFilter = $state<FormEntityType[]>([]);
	// Seeded once from the URL, then owned by the input — the search navigates with
	// `replaceState`, so no history entry carries a different `q` to fall out of step with.
	// svelte-ignore state_referenced_locally
	let query = $state(data.query ?? '');
	/** The search is the URL's, so the API answers it across every form rather than across the
	 *  rows already in hand. The other filters on this page stay local: they run over a complete
	 *  set, and narrowing one page of a list while calling it the whole is the failure this avoids.
	 */
	function onSearch(term: string): void {
		void goto(nextUrlFromStart(page.url, { q: term }), {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	let docFilter = $state<DocumentType[]>([]);
	let sort = $state<DataTableSort | null>(null);
	let createOpen = $state(false);
	let creating = $state(false);
	let createError = $state<string | null>(null);

	const DOC_TYPES = Object.entries(DOC_TYPE_META) as [
		DocumentType,
		{ label: string; textColor: string; bgColor: string }
	][];
	const ENTITY_ITEMS = [
		{ value: 'applicant', label: 'Applicant' },
		{ value: 'account', label: 'Account' }
	];
	const DOC_FILTER_OPTIONS = DOC_TYPES.map(([key, meta]) => ({ value: key, label: meta.label }));
	const DOC_TYPE_ITEMS = [
		{ value: '', label: 'General (any type)' },
		...DOC_TYPES.map(([key, meta]) => ({ value: key, label: meta.label }))
	];

	const filterDefs: FilterDef[] = [
		{
			key: 'status',
			label: 'Status',
			allLabel: 'All active',
			options: Object.entries(FORM_STATUS_META).map(([value, meta]) => ({
				value,
				label: meta.label
			}))
		},
		{ key: 'entity', label: 'Applies to', allLabel: 'All entities', options: ENTITY_ITEMS },
		{ key: 'doc_type', label: 'Type', options: DOC_FILTER_OPTIONS }
	];
	const filterValue = $derived<Record<string, string[]>>({
		...(statusFilter.length ? { status: statusFilter } : {}),
		...(entityFilter.length ? { entity: entityFilter } : {}),
		...(docFilter.length ? { doc_type: docFilter } : {})
	});

	// New-form fields.
	let name = $state('');
	let code = $state('');
	let codeEdited = $state(false);
	let entityType = $state<FormEntityType>('applicant');
	let documentType = $state<DocumentType | ''>('');
	let description = $state('');

	$effect(() => {
		if (!codeEdited) code = slugifyCode(name);
	});

	/** The listing's local pass over the loaded rows. */
	function filterForms(forms: Form[]): Form[] {
		return forms.filter((f) => {
			// An EMPTY selection means all active (archived is its own state); otherwise OR across
			// whichever statuses are ticked — "Draft or Archived" is one question, not two.
			if (statusFilter.length ? !statusFilter.includes(f.status) : f.status === 'archived')
				return false;
			if (docFilter.length && !docFilter.includes(f.documentType as DocumentType)) return false;
			if (entityFilter.length && !entityFilter.includes(f.entityType)) return false;
			const q = query.trim().toLowerCase();
			if (q && !`${f.name} ${f.code}`.toLowerCase().includes(q)) return false;
			return true;
		});
	}

	const filtersActive = $derived(
		statusFilter.length > 0 ||
			docFilter.length > 0 ||
			entityFilter.length > 0 ||
			query.trim() !== ''
	);

	function clearFilters() {
		statusFilter = [];
		docFilter = [];
		entityFilter = [];
		// The search is applied TWICE — the URL narrows what the server sends, `filtered` narrows
		// again locally — so clearing the state alone only half-cleared: the local pass stopped
		// filtering while the server kept answering the old term, and a reload re-narrowed the list.
		query = '';
		void goto(nextUrlFromStart(page.url, { q: '' }), { keepFocus: true, noScroll: true });
	}

	// Quick-archive from the list row — posts to the form detail route's action (guards there).
	let archiveOpen = $state(false);
	let archiveTarget = $state<{ uid: string; name: string } | null>(null);

	function requestArchive(uid: string, name: string) {
		archiveTarget = { uid, name };
		archiveOpen = true;
	}

	async function archiveForm(): Promise<void> {
		if (!archiveTarget) return;
		try {
			const res = await fetch(`/forms/${archiveTarget.uid}?/archive`, {
				method: 'POST',
				body: new FormData(),
				headers: { 'x-sveltekit-action': 'true' }
			});
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				toast.success('Form archived.');
				await invalidateAll();
			} else {
				toast.error(
					result.type === 'failure'
						? String(result.data?.error ?? 'Could not archive the form.')
						: 'Could not archive the form.'
				);
			}
		} catch {
			toast.error('Could not archive the form.');
		}
	}

	function resetCreate() {
		name = '';
		code = '';
		codeEdited = false;
		entityType = 'applicant';
		documentType = '';
		description = '';
		createError = null;
	}

	const desc = { sortable: true, defaultDir: 'desc' } as const;
	const FORM_COLUMNS: DataTableColumn<Form>[] = [
		{ key: 'name', header: 'Name', sortable: true, sortDefault: true, headerClass: 'w-[38%]' },
		{
			key: 'entityType',
			header: 'Applies to',
			sortable: true,
			hideBelowWidth: 'md',
			headerClass: 'w-[14%]',
			sortValue: (f) => humanise(f.entityType)
		},
		{
			...desc,
			key: 'fieldCount',
			header: 'Fields',
			align: 'center',
			hideBelowWidth: 'lg',
			headerClass: 'w-[12%]'
		},
		{ ...desc, key: 'createdAt', header: 'Created', hideBelowWidth: 'xl', headerClass: 'w-[14%]' },
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[12%]',
			sortValue: (f) => FORM_STATUS_META[f.status].label
		},
		{ key: 'actions', header: '', align: 'right', headerClass: 'w-[10%]' }
	];
	const columns = $derived(data.canManage ? FORM_COLUMNS : FORM_COLUMNS.slice(0, -1));
</script>

{#snippet clearFormFilters()}
	<Button variant="outline" size="sm" onclick={clearFilters}>Clear filters</Button>
{/snippet}

{#snippet createFirstForm()}
	<Button size="sm" onclick={() => (createOpen = true)}>New form</Button>
{/snippet}

<svelte:head><title>Forms · Platform</title></svelte:head>

{#snippet listingActions()}
	<ListingOverflow>
		<DropdownMenu.Item onclick={() => (createOpen = true)}>New form</DropdownMenu.Item>
	</ListingOverflow>
{/snippet}

{#snippet loadBanner(loadError: boolean)}
	{#if loadError}
		<LoadErrorBanner
			message="The forms list couldn’t be loaded. Try again."
			onRetry={() => invalidateAll()}
		/>
	{/if}
{/snippet}

<!-- `keep`: the search navigates with `replaceState` on every keystroke, and the create sheet's
     duplicate-code check reads the rows, so the last answer is held while the next one lands. -->
<Streamed data={data.book} onretry={() => invalidateAll()} keep>
	{#snippet pending()}
		<ListingSkeleton header={false} />
	{/snippet}
	{#snippet children(book)}
		{@const forms = book.forms}
		{@const filtered = filterForms(forms)}
		{@const existingCodes = forms.map((f) => f.code)}
		{@const codeExists = code.trim().length > 0 && existingCodes.includes(code.trim())}
		{@const canCreate = name.trim().length > 0 && code.trim().length > 0 && !codeExists}
		{#snippet banner()}
			{@render loadBanner(book.loadError)}
		{/snippet}
		<ListingFrame {banner}>
			<ListingToolbar
				class="shrink-0"
				bind:query
				placeholder="Search forms…"
				dirty={filtersActive}
				onsearch={onSearch}
				onclear={clearFilters}
				actions={data.canManage ? listingActions : undefined}
				{filterDefs}
				{filterValue}
				onfilters={(next) => {
					statusFilter = (next.status ?? []) as FormStatus[];
					entityFilter = (next.entity ?? []) as FormEntityType[];
					docFilter = (next.doc_type ?? []) as DocumentType[];
				}}
			/>

			{#if filtered.length === 0 && !book.loadError}
				<!-- ONE state for two: with no filters set this told a firm that had never made a form to
		     go and adjust filters it had not touched. And both acts it named — adjust, create —
		     were prose beside no control.
		     THREE, counting the failure. A failed read leaves `filtered` empty too, so this rendered
		     "No forms yet — a form collects answers from a client" with a Create button underneath
		     the error banner, telling a firm with fifty forms it had none. The banner above already
		     states the failure and carries the retry, so the honest thing here is to say nothing. -->
				<EmptyState
					icon={ListChecksIcon}
					title={filtersActive ? 'No forms match your filters' : 'No forms yet'}
					description={filtersActive
						? 'Try a different status or search term, or clear the filters to see every form.'
						: 'A form collects client answers, such as a fact-find, risk questionnaire or declaration.'}
					action={filtersActive ? clearFormFilters : data.canManage ? createFirstForm : undefined}
					chromeless
					compact
				/>
			{:else}
				<DataTable
					{columns}
					rows={filtered}
					bind:sort
					scroll
					fixed
					getKey={(form) => form.uid}
					label="Forms"
				>
					{#snippet cell(form, column)}
						{#if column.key === 'name'}
							<div class="flex min-w-0 items-center gap-3">
								<div
									class="bg-brand/10 flex size-9 shrink-0 items-center justify-center rounded-lg"
								>
									<ListChecksIcon class="text-brand size-4.5" />
								</div>
								<div class="min-w-0 flex-1">
									<TruncatedText
										text={form.name}
										href={`/forms/${form.uid}`}
										class="text-xs font-medium"
									/>
									<RecordId value={form.code} />
								</div>
							</div>
						{:else if column.key === 'entityType'}
							<Badge variant="outline" class="font-normal">{humanise(form.entityType)}</Badge>
						{:else if column.key === 'fieldCount'}
							<span class="text-muted-foreground text-xs tabular-nums">{form.fieldCount}</span>
						{:else if column.key === 'createdAt'}
							<span class="text-muted-foreground text-xs">{formatDate(form.createdAt)}</span>
						{:else if column.key === 'status'}
							{@const meta = FORM_STATUS_META[form.status]}
							<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
						{:else if column.key === 'actions' && form.status !== 'archived'}
							<div class="flex justify-end gap-1">
								<Button
									variant="ghost"
									size="icon"
									class="size-8"
									aria-label="Edit form"
									href={`/forms/${form.uid}/edit`}
								>
									<PencilIcon class="size-4" />
								</Button>
								<Button
									variant="ghost"
									size="icon"
									class="text-muted-foreground hover:text-destructive size-8"
									aria-label="Archive form"
									onclick={() => requestArchive(form.uid, form.name)}
								>
									<ArchiveIcon class="size-4" />
								</Button>
							</div>
						{/if}
					{/snippet}
				</DataTable>
			{/if}
		</ListingFrame>

		<Sheet.Root bind:open={createOpen} onOpenChange={(v) => !v && resetCreate()}>
			<Sheet.Content class="flex flex-col gap-0">
				<Sheet.Header class="border-b px-6 pt-6 pb-4">
					<Sheet.Title class="flex items-center gap-2">
						<ListChecksIcon class="text-primary size-4" /> New form
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
								// This custom callback replaces enhance's default, which would have refreshed the
								// list; invalidate so the new form shows when navigating back to /forms.
								await invalidateAll();
								goto(`/forms/${uid}/edit`);
							} else if (result.type === 'failure') {
								createError = (result.data?.error as string) ?? 'Could not create the form.';
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
							<Label for="form-name">Name</Label>
							<Input
								id="form-name"
								name="name"
								placeholder="e.g. Client Fact Find"
								bind:value={name}
							/>
						</div>
						<div class="space-y-1.5">
							<Label for="form-code">Reference code</Label>
							<Input
								id="form-code"
								name="code"
								class="font-mono text-xs"
								placeholder="client_fact_find"
								bind:value={code}
								oninput={() => (codeEdited = true)}
								aria-invalid={codeExists}
							/>
							{#if codeExists}
								<p class="text-destructive text-xs">A form with this code already exists.</p>
							{:else}
								<p class="text-muted-foreground text-xs">Auto-filled from the name.</p>
							{/if}
						</div>
						<div class="space-y-1.5">
							<Label for="form-entity">Applies to</Label>
							<SelectMenu
								id="form-entity"
								name="entity_type"
								items={ENTITY_ITEMS}
								value={entityType}
								onValueChange={(v) => (entityType = v as FormEntityType)}
							/>
						</div>
						<div class="space-y-1.5">
							<Label for="form-doctype">
								Document type <span class="text-muted-foreground font-normal">(optional)</span>
							</Label>
							<SelectMenu
								id="form-doctype"
								name="document_type"
								items={DOC_TYPE_ITEMS}
								value={documentType}
								onValueChange={(v) => (documentType = v as DocumentType | '')}
							/>
						</div>
						<div class="space-y-1.5">
							<Label for="form-desc"
								>Description <span class="text-muted-foreground font-normal">(optional)</span
								></Label
							>
							<Textarea
								id="form-desc"
								name="description"
								rows={3}
								placeholder="What this form captures…"
								bind:value={description}
							/>
						</div>
					</div>
					<div class="flex items-center justify-between border-t px-6 py-4">
						<Button type="button" variant="ghost" onclick={() => (createOpen = false)}
							>Cancel</Button
						>
						<Button type="submit" disabled={!canCreate || creating}>
							{creating ? 'Creating…' : 'Create form'}
						</Button>
					</div>
				</form>
			</Sheet.Content>
		</Sheet.Root>
	{/snippet}
</Streamed>

<ConfirmDialog
	bind:open={archiveOpen}
	title="Archive this form?"
	description={archiveTarget
		? `“${archiveTarget.name}” will move to the archived view. You can restore it later.`
		: undefined}
	confirmLabel="Archive"
	destructive
	onconfirm={archiveForm}
/>
