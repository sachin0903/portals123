<script module lang="ts">
	import type { FilterDef } from '@graphene/ui/book/listing';

	export type DocumentsView = 'table' | 'grid';

	export type RowMenuItem = 'view' | 'download' | 'delete';

	/** A row kebab's items. Delete needs `documents:write`; a provider-synced document 409s it. */
	export function rowMenuItems(
		doc: { isFile?: boolean; deletable?: boolean },
		canWrite: boolean
	): RowMenuItem[] {
		const items: RowMenuItem[] = doc.isFile ? ['download'] : ['view', 'download'];
		if (canWrite && (doc.isFile || doc.deletable !== false)) items.push('delete');
		return items;
	}

	/** A pure function, not a `$derived`: SSR renders nothing while the popover is shut, so this is
	 *  the one thing it can still pin about the panel's shape. */
	export function clientDocumentsFilterDefs(input: {
		typeOptions: FilterDef['options'];
		authorOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			{
				key: 'type',
				label: 'Type',
				options: input.typeOptions,
				searchPlaceholder: 'Search types…'
			},
			{
				key: 'author',
				label: 'Author',
				options: input.authorOptions,
				searchPlaceholder: 'Search authors…'
			}
		];
	}
</script>

<script lang="ts">
	import { untrack } from 'svelte';
	import { goto, invalidateAll } from '$app/navigation';
	import { deserialize, enhance } from '$app/forms';
	import { page } from '$app/state';
	import {
		Badge,
		Button,
		REFRESH_WHILE,
		RefreshWhile,
		buttonVariants,
		Card,
		cn,
		CardContent,
		Checkbox,
		ConfirmDialog,
		DataTable,
		Dialog,
		DropdownMenu,
		EventFeed,
		formatBytes,
		formatDate,
		formatDateTime,
		Input,
		Segmented,
		Select,
		sortRows,
		StatusBadge,
		toast,
		TruncatedText,
		type ActionEntity,
		type ActionEvent,
		type DataTableColumn,
		type DataTableGroupKey,
		type DataTableSort
	} from '@graphene/ui';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis-vertical';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Rows3Icon from '@lucide/svelte/icons/rows-3';
	import LayoutGridIcon from '@lucide/svelte/icons/layout-grid';
	import FileOutputIcon from '@lucide/svelte/icons/file-output';
	import FilePlusIcon from '@lucide/svelte/icons/file-plus';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import { FORMAT_META } from '@graphene/ui/book/documents';
	import {
		ListingToolbar,
		PAGE_SIZE,
		PaginationStrip,
		rangeLabelFromOffset,
		matches,
		optionsFrom,
		search
	} from '@graphene/ui/book/listing';
	import {
		toRow,
		fileToRow,
		bulkTargets,
		DocumentsBulkBar,
		DocumentThumb,
		deleteTitle,
		documentInFlight,
		pickRow,
		RenderState,
		SelectBox,
		renderedOrder,
		selectionOf,
		toggleMany,
		FORMAT_LABELS,
		libraryTypeLabel,
		type ApiDocument,
		type ApiFile,
		type ClientOption,
		type DocumentTypeOption,
		type LibraryRow
	} from '@graphene/ui/book/documents';
	import UploadDialog from '$lib/components/documents/upload-dialog.svelte';
	import { portalEntityHref } from '$lib/audit/entity-href';
	import { RequestDialog } from '@graphene/ui/book/documents';
	import { outcomeOf, type Outcome } from './action-result';

	// The document data for this client, resolved by the client-detail loader. Rendered as the
	// "Documents" tab of /clients/[id]; mutating dialogs POST to that route's actions (?/archive,
	// and the shared /documents actions) then invalidateAll() to refresh.
	let {
		data,
		canSend = false,
		canWrite = false,
		view = $bindable('table')
	}: {
		data: {
			client: { id: number; uid: string; name: string };
			clientOption: ClientOption;
			documents: ApiDocument[];
			files: ApiFile[];
			library: ApiDocument[];
			events: ActionEvent[];
			/** Whether to SHOW the "Recent activity" glance — it reads the event stream, so the loader
			 *  gates it on activity:read and the panel is hidden (not empty) when the caller lacks it. */
			canReadActivity: boolean;
			documentTypes: DocumentTypeOption[];
			forms: { uid: string; name: string }[];
		};
		/** `documents:send` (the six-splits re-key): gates the affordances that REACH the client —
		 *  "Request from client" — which is NOT covered by
		 *  documents:write (drafting). The loader passes it; the server actions enforce it. */
		canSend?: boolean;
		/** `documents:write`: earns the select boxes and the bulk Delete. */
		canWrite?: boolean;
		/** A table in type folders, or a grid of previews. */
		view?: DocumentsView;
	} = $props();

	// The document stays UNDER the client in the URL. It used to link out to the flat
	// `/documents/{uid}` carrying `?from=/clients/{id}` so the viewer's Back knew where to return —
	// a query string standing in for the hierarchy the route now expresses directly. The nested
	// route also refuses a document that is not this client's, which the flat one could not check.
	const docHref = (uid: string): string => `/clients/${data.client.uid}/documents/${uid}`;

	const client = $derived(data.client);
	let library = $state<ApiDocument[]>([]);
	let libraryLoading = $state(false);
	async function ensureLibrary(): Promise<void> {
		if (library.length > 0 || libraryLoading) return;
		libraryLoading = true;
		try {
			const res = await fetch(`/clients/${client.uid}/documents/library`);
			if (res.ok) library = (await res.json()) as ApiDocument[];
		} finally {
			libraryLoading = false;
		}
	}

	// This client's live record: their documents + their supporting files. There is no Live/Archived
	// switch any more — archiving reads as DELETING, and a tab that offers to browse a client's
	// deleted documents invites undeleting as an ordinary act. A deleted document keeps its own URL
	// (Restore lives on the record), and browsing them is the ops-side "Deleted documents" view.
	const docs = $derived([...data.documents.map(toRow), ...data.files.map(fileToRow)]);
	const renderingSome = $derived(docs.some((d) => documentInFlight(d.status)));

	// "Add from existing documents": file an existing firm document under this client (sets its
	// made-for). Only UNASSIGNED documents are offered (`!d.client`) — one already made for another
	// client belongs to them and must not be silently re-homed here — and never supporting files.
	let assignOpen = $state(false);
	let assignUids = $state<Set<string>>(new Set());
	let assignSearch = $state('');
	let assigning = $state(false);
	const assignableDocs = $derived(
		library.map(toRow).filter((d) => !d.isFile && d.status === 'active' && !d.client)
	);
	const filteredAssignable = $derived(
		assignableDocs.filter(
			(d) => !assignSearch || d.name.toLowerCase().includes(assignSearch.toLowerCase())
		)
	);
	const allFilteredAssignSelected = $derived(
		filteredAssignable.length > 0 && filteredAssignable.every((d) => assignUids.has(d.uid))
	);
	function openAssign(): void {
		assignUids = new Set();
		assignSearch = '';
		assignOpen = true;
		void ensureLibrary();
	}
	function toggleAssign(uid: string, checked: boolean): void {
		const next = new Set(assignUids);
		if (checked) next.add(uid);
		else next.delete(uid);
		assignUids = next;
	}
	function toggleAllAssign(checked: boolean): void {
		const next = new Set(assignUids);
		for (const d of filteredAssignable) {
			if (checked) next.add(d.uid);
			else next.delete(d.uid);
		}
		assignUids = next;
	}
	async function assign(): Promise<void> {
		if (assignUids.size === 0 || assigning) return;
		assigning = true;
		const count = assignUids.size;
		const body = new FormData();
		for (const uid of assignUids) body.append('uid', uid);
		body.append('contact_id', String(client.id));
		let outcome: Outcome;
		try {
			const res = await fetch('?/assign', {
				method: 'POST',
				body,
				headers: { accept: 'application/json', 'x-sveltekit-action': 'true' }
			});
			outcome = outcomeOf(deserialize(await res.text()), 'Couldn’t add those documents.');
		} catch {
			outcome = { ok: false, error: 'Couldn’t add those documents.' };
		}
		assigning = false;
		if (outcome.ok) {
			toast.success(`${count} document${count === 1 ? '' : 's'} added to ${client.name}.`);
			assignOpen = false;
			await invalidateAll();
		} else {
			toast.error(outcome.error ?? 'Couldn’t add those documents.');
		}
	}

	// Documents list — search + column-header sorting, mirroring the firm-wide library's toolbar.
	let uploadOpen = $state(false);
	// The search bar's "Upload document" lands here with `?upload=1` when it is used on a client's
	// record — the firm-wide page needs documents:show, which this tab does not.
	$effect(() => {
		if (page.url.searchParams.get('upload') === '1') uploadOpen = true;
	});
	// The in-place "ask this client for something" dialog (locked to this client), so raising a
	// request never leaves the record.
	let requestOpen = $state(false);
	let localSearch = $state('');
	// Type and author, the same two the firm-wide library offers. NO client filter: this tab IS one
	// client, so the control could only ever have one value.
	//
	// Filtered CLIENT-SIDE, unlike the library's. The difference is the population: this list is one
	// client's documents, fetched whole (`?contact_id=`, no cap), so the rows on screen ARE the match
	// set and options taken from them describe all of it. The library pages a firm-wide book behind a
	// 200-row fetch, where the same trick would offer only what happened to load.
	let typeFilter = $state<string[]>([]);
	let authorFilter = $state<string[]>([]);
	const typeOptions = $derived(
		optionsFrom(
			docs,
			(d) => d.category,
			(v) => libraryTypeLabel(v, fileTypeSlugs.has(v))
		)
	);
	const authorOptions = $derived(optionsFrom(docs, (d) => d.createdBy));
	const filterDefs = $derived(clientDocumentsFilterDefs({ typeOptions, authorOptions }));
	const barValue = $derived<Record<string, string[]>>({
		...(typeFilter.length ? { type: typeFilter } : {}),
		...(authorFilter.length ? { author: authorFilter } : {})
	});
	// Which slugs belong to the FILE vocabulary — `other` means "Other" as a document type and
	// "Other file" as an attachment type, so the label needs telling which kind it is looking at.
	const fileTypeSlugs = $derived(new Set(docs.filter((d) => d.isFile).map((d) => d.category)));
	// Same-origin content URL, for Download and the grid's previews.
	const contentSrc = (doc: LibraryRow): string =>
		doc.isFile ? `/files/${doc.uid}/download` : `/documents/${doc.uid}/content`;
	const thumbStatus = (d: LibraryRow) =>
		d.status === 'failed'
			? 'failed'
			: d.status === 'queued' || d.status === 'generating'
				? 'rendering'
				: 'ready';
	function rowOpen(d: LibraryRow): void {
		// A supporting file is inline (a passport, a statement). Assigning `location.href` replaces
		// the client record with that URL. A new tab keeps the record where the reader left it.
		if (d.isFile) {
			window.open(contentSrc(d), '_blank', 'noopener');
			return;
		}
		goto(docHref(d.uid));
	}
	// Human label for the Type column — documents map their category slug to a label (shared with the
	// overview); supporting files already carry a label as their category (Passport…).
	const typeLabel = (d: LibraryRow): string => libraryTypeLabel(d.category, d.isFile);

	// With `documents:write` a select column leads; `h-12` is a data row's height, so bands match it.
	const columns = $derived<DataTableColumn<LibraryRow>[]>([
		...(canWrite
			? [
					{
						key: 'select',
						header: 'Select',
						head: selectAllHead,
						lead: true,
						headerClass: 'w-9 pl-5',
						cellClass: 'h-12 pl-5'
					},
					{ key: 'format', header: '', headerClass: 'w-10' }
				]
			: [{ key: 'format', header: '', headerClass: 'w-8 pl-5', cellClass: 'pl-5' }]),
		{
			key: 'name',
			header: 'Document',
			sortable: true,
			indent: true,
			sortValue: (d) => d.name.toLowerCase(),
			cellClass: 'max-w-[16rem]'
		},
		{
			key: 'date',
			header: 'Created',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (d) => d.createdAt,
			cellClass: 'text-muted-foreground text-xs whitespace-nowrap'
		},
		{
			key: 'author',
			header: 'Author',
			sortable: true,
			sortValue: (d) => (d.createdBy || '').toLowerCase(),
			cellClass: 'max-w-[9rem] truncate text-xs'
		},
		{
			key: 'type',
			header: 'Type',
			sortable: true,
			sortValue: (d) => typeLabel(d).toLowerCase()
		},
		{
			key: 'delivery',
			header: 'Delivery',
			sortable: true,
			// A file has no delivery state, so it sorts after both rather than as "Unread".
			sortValue: (d) => (d.isFile ? 2 : d.clientViewed ? 1 : 0)
		},
		{
			key: 'size',
			header: 'Size',
			sortable: true,
			defaultDir: 'desc',
			align: 'right',
			sortValue: (d) => d.sizeBytes,
			cellClass: 'text-muted-foreground text-xs tabular-nums whitespace-nowrap'
		},
		{ key: 'actions', header: '' }
	]);

	// `$state`, not a template literal: a bound literal is recreated per update, resetting the sort.
	let sort = $state<DataTableSort | null>({ key: 'date', dir: 'desc' });

	// Type folders only: this tab is one client.
	const folders = $derived(view === 'table');
	const VIEWS = [
		{ value: 'table', label: 'Table', ariaLabel: 'Show as a table', icon: Rows3Icon },
		{ value: 'grid', label: 'Grid', ariaLabel: 'Show as a grid', icon: LayoutGridIcon }
	] as const;
	// The grid has no headers, so it sorts with a Select over the same `sort`.
	const GRID_SORTS = [
		{ value: 'date:desc', label: 'Newest first' },
		{ value: 'date:asc', label: 'Oldest first' },
		{ value: 'name:asc', label: 'Name A–Z' },
		{ value: 'name:desc', label: 'Name Z–A' },
		{ value: 'type:asc', label: 'Type' },
		{ value: 'author:asc', label: 'Author' },
		{ value: 'delivery:asc', label: 'Unread first' },
		{ value: 'size:desc', label: 'Largest first' },
		{ value: 'size:asc', label: 'Smallest first' }
	];
	const sortValue = $derived(sort ? `${sort.key}:${sort.dir}` : 'date:desc');
	const sortLabel = $derived.by(() => {
		const known = GRID_SORTS.find((o) => o.value === sortValue);
		if (known) return known.label;
		const header = columns.find((c) => c.key === sort?.key)?.header ?? 'Sort';
		return `${header} ${sort?.dir === 'asc' ? '↑' : '↓'}`;
	});
	function pickSort(next: string): void {
		const [key, dir] = next.split(':');
		sort = { key, dir: dir === 'asc' ? 'asc' : 'desc' };
	}
	let collapsed = $state<Set<string>>(new Set());
	const groupByType = (d: LibraryRow): DataTableGroupKey[] => [
		{
			key: d.category || '_none',
			label: libraryTypeLabel(d.category),
			last: !d.category,
			column: 'type'
		}
	];

	const visibleDocs = $derived.by(() => {
		// No documents/files kind filter: both kinds arrive in one list and the Type column already
		// says which is which, so the control only ever narrowed what was in front of you.
		const list = docs.filter(
			(d) =>
				search(localSearch, d.name) &&
				matches(typeFilter, d.category) &&
				matches(authorFilter, d.createdBy)
		);
		// Newest first: the stable column sort keeps it as the tiebreak.
		const recent = [...list].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
		return sortRows(recent, columns, sort);
	});

	// Client-side pagination of the filtered/sorted documents; any search/sort change returns to
	// page 1 — a re-sorted list whose page 3 you are still on shows rows you never asked for.
	// The page size is the product-wide listing one (`@graphene/ui/book/listing`).
	const PER_PAGE = PAGE_SIZE;
	let docsPage = $state(1);
	const docsPageCount = $derived(Math.max(1, Math.ceil(visibleDocs.length / PER_PAGE)));
	// No pager with folders: a page break would split a folder.
	const rows = $derived(
		folders ? visibleDocs : visibleDocs.slice((docsPage - 1) * PER_PAGE, docsPage * PER_PAGE)
	);
	$effect(() => {
		void localSearch;
		void typeFilter;
		void authorFilter;
		void sort;
		docsPage = 1;
	});

	// Dropped on a filter or view change; a re-sort or a page turn keeps it.
	let selected = $state<string[]>([]);
	let lastPicked = $state<string | null>(null);
	function resetSelection(): void {
		selected = [];
		lastPicked = null;
	}
	$effect(() => {
		void [localSearch, typeFilter, authorFilter, view];
		resetSelection();
	});
	const renderedUids = $derived(
		renderedOrder(rows, folders ? groupByType : undefined, columns, sort, collapsed)
	);
	function toggleRow(e: MouseEvent, uid: string): void {
		selected = pickRow(selected, renderedUids, uid, lastPicked, e.shiftKey);
		lastPicked = uid;
	}
	/** Select-all covers the whole filtered list: every page, shut folders included. */
	const allState = $derived(selectionOf(selected, visibleDocs));
	const targets = $derived(bulkTargets(selected, visibleDocs));
	// A reload can drop rows: keep only the ticks on rows still here.
	$effect(() => {
		const live = new Set(docs.map((d) => d.uid));
		const kept = untrack(() => selected).filter((u) => live.has(u));
		if (kept.length !== untrack(() => selected).length) selected = kept;
	});

	// Post to a form action from a plain fetch and READ THE OUTCOME. SvelteKit answers a `fail()`
	// with HTTP 200 (the failure is in the body's `type`), so `res.ok` proves nothing — these two
	// mutations used to ignore the response entirely: a 403 produced no toast and no error, the row
	// simply stayed put and the user assumed a UI glitch. See ./action-result.ts.
	async function send(action: string, uid: string, fallback: string): Promise<Outcome> {
		const body = new FormData();
		body.append('uid', uid);
		try {
			const res = await fetch(action, {
				method: 'POST',
				body,
				headers: { accept: 'application/json', 'x-sveltekit-action': 'true' }
			});
			return outcomeOf(deserialize(await res.text()), fallback);
		} catch {
			return { ok: false, error: fallback };
		}
	}

	async function post(
		action: string,
		uid: string,
		success: string,
		fallback: string
	): Promise<boolean> {
		const outcome = await send(action, uid, fallback);
		if (outcome.ok) {
			toast.success(success);
			await invalidateAll();
		} else {
			toast.error(outcome.error ?? fallback);
		}
		return outcome.ok;
	}

	function unselect(uid: string): void {
		selected = selected.filter((u) => u !== uid);
		if (lastPicked === uid) lastPicked = null;
	}

	// Delete one of this client's documents — the API's soft `archive`, so it leaves this list and
	// the client's portal and stays on the record. invalidateAll() reruns the load so the row goes
	// (the API omits archived documents). Permission is enforced by the ?/archive action.
	const archive = async (uid: string) => {
		if (await post('?/archive', uid, 'Document deleted', 'Couldn’t delete that document.'))
			unselect(uid);
	};

	// (No restore here: this tab has no deleted view to restore FROM. The document's own record keeps
	// Restore, which is where a deletion is undone — see the route's `?/restore` action.)

	// Requeue a failed render. Posted to the LIBRARY route's action (like the file delete posts to
	// /files?/delete) rather than duplicating a third copy of the same documents:write guard on this
	// route — the API endpoint and the permission are identical, only the caller's page differs. The
	// row stays put until the worker picks the job up again, so the toast is the whole feedback.
	let retrying = $state<string | null>(null);
	async function retryRender(uid: string): Promise<void> {
		if (retrying) return;
		retrying = uid;
		try {
			await post(
				'/documents?/retryRender',
				uid,
				'Document preparation restarted',
				'Couldn’t restart that document.'
			);
		} finally {
			retrying = null;
		}
	}

	// Soft-delete a supporting file — posts to the /files route's action (kept live behind its
	// redirect), which enforces documents:write. Confirmed first: this is a client-supplied
	// document (an ID, a statement) and deleting it is not something to do on a mis-click.
	// Armed row + dialog open flag (the same pattern the client record uses for cancel/deactivate).
	let deleting = $state<LibraryRow | null>(null);
	let deleteOpen = $state(false);
	const deleteFile = async (uid: string) => {
		if (await post('/files?/delete', uid, 'File deleted', 'Couldn’t delete that file.'))
			unselect(uid);
	};

	// Bulk Delete: the row menu's actions, one uid at a time; a failure names the rows it missed.
	type BulkKind = 'documents' | 'files';
	let bulk = $state<{ kind: BulkKind; uids: string[] } | null>(null);
	let bulkOpen = $state(false);
	function requestBulk(kind: BulkKind, uids: string[]): void {
		bulk = { kind, uids };
		bulkOpen = true;
	}
	async function runBulk(kind: BulkKind, uids: string[]): Promise<void> {
		const action = kind === 'documents' ? '?/archive' : '/files?/delete';
		const failed: string[] = [];
		for (const uid of uids) {
			if (!(await send(action, uid, '')).ok) failed.push(uid);
		}
		resetSelection();
		await invalidateAll();
		if (failed.length === 0) {
			const one = kind === 'documents' ? 'Document deleted' : 'File deleted';
			toast.success(uids.length === 1 ? one : `Deleted ${uids.length} ${kind}`);
			return;
		}
		const nameOf = (uid: string) => docs.find((d) => d.uid === uid)?.name ?? uid;
		const more = failed.length > 3 ? `, +${failed.length - 3} more` : '';
		toast.error(`Couldn’t delete ${failed.slice(0, 3).map(nameOf).join(', ')}${more}.`);
	}
</script>

<RefreshWhile
	active={renderingSome}
	interval={REFRESH_WHILE.inFlightList}
	onpoll={() => invalidateAll()}
/>

<div
	class="grid gap-5 lg:items-start {data.canReadActivity
		? 'lg:grid-cols-[minmax(0,1fr)_22rem]'
		: ''}"
>
	<!-- An empty record and an emptied search say different things. -->
	{#snippet empty()}
		<p class="text-muted-foreground px-5 py-6 text-center text-xs">
			{docs.length === 0 ? 'No documents yet for this client.' : 'No documents match your search.'}
		</p>
	{/snippet}

	<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
		<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
			<div class="flex shrink-0 items-stretch justify-between gap-3">
				<div class="min-w-0 flex-1">
					<ListingToolbar
						bind:query={localSearch}
						placeholder="Search by name…"
						dirty={!!localSearch || typeFilter.length > 0 || authorFilter.length > 0}
						onclear={() => {
							localSearch = '';
							typeFilter = [];
							authorFilter = [];
						}}
						{filterDefs}
						filterValue={barValue}
						onfilters={(next) => {
							typeFilter = next.type ?? [];
							authorFilter = next.author ?? [];
						}}
					/>
				</div>
				<div class="flex shrink-0 items-center gap-2">
					{#if view === 'grid'}
						<Select.Root type="single" value={sortValue} onValueChange={pickSort}>
							<Select.Trigger class="h-9 min-w-[9rem]" aria-label="Sort">
								{sortLabel}
							</Select.Trigger>
							<Select.Content align="end">
								{#each GRID_SORTS as o (o.value)}
									<Select.Item value={o.value}>{o.label}</Select.Item>
								{/each}
							</Select.Content>
						</Select.Root>
					{/if}
					<Segmented options={VIEWS} bind:value={view} iconOnly size="lg" label="View" />
					<DropdownMenu.Root>
						<DropdownMenu.Trigger
							class={cn(
								buttonVariants({ variant: 'ghost', size: 'sm' }),
								'text-muted-foreground hover:text-foreground h-9 shrink-0 px-2'
							)}
							aria-label="Add document"
							title="Add document"
						>
							<PlusIcon class="size-4" aria-hidden="true" />
						</DropdownMenu.Trigger>
						<DropdownMenu.Content align="end" class="w-60">
							<DropdownMenu.Item onclick={() => (uploadOpen = true)}>
								<UploadIcon class="mr-2 size-4" />
								Upload a document
							</DropdownMenu.Item>
							<DropdownMenu.Item onclick={openAssign}>
								<FilePlusIcon class="mr-2 size-4" />
								Add from existing documents
							</DropdownMenu.Item>
							<DropdownMenu.Item onclick={() => goto(`/templates?client=${client.id}`)}>
								<FileOutputIcon class="mr-2 size-4" />
								Generate from a template
							</DropdownMenu.Item>
							{#if canSend}
								<DropdownMenu.Item
									onclick={() => {
										requestOpen = true;
										void ensureLibrary();
									}}
								>
									<ClipboardListIcon class="mr-2 size-4" />
									Request from client
								</DropdownMenu.Item>
							{/if}
						</DropdownMenu.Content>
					</DropdownMenu.Root>
				</div>
			</div>

			{#if view === 'grid'}
				{#if canWrite && rows.length > 0}
					<label class="flex items-center gap-2 self-start text-xs">
						{@render selectAllHead()}
						Select all
					</label>
				{/if}
				{#if rows.length === 0}
					{@render empty()}
				{:else}
					<ul
						class="grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3"
						aria-label="Documents"
					>
						{#each rows as doc (doc.uid)}
							<li
								class="hover:bg-muted/30 relative flex min-w-0 flex-col gap-2 rounded-xl border p-2 transition-colors"
							>
								<DocumentThumb
									src={contentSrc(doc)}
									format={doc.documentType}
									status={thumbStatus(doc)}
								/>
								{#if canWrite}
									<div class="absolute top-3 left-3 z-10">
										{@render rowSelect(doc)}
									</div>
								{/if}
								<div class="flex min-w-0 items-start gap-1 pl-1">
									<div class="min-w-0 flex-1">
										{@render docName(doc, true)}
										<p class="text-muted-foreground truncate text-xs">
											{typeLabel(doc)} · {formatDate(doc.createdAt)}
										</p>
										<div class="relative z-10 mt-1 flex min-w-0 flex-wrap items-center gap-1.5">
											{#if doc.status === 'queued' || doc.status === 'failed'}
												{@render renderState(doc)}
											{:else if !doc.isFile}
												{@render delivery(doc)}
											{/if}
										</div>
									</div>
									<div class="relative z-10 -mr-1 shrink-0">
										{@render rowActions(doc)}
									</div>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			{:else}
				<div class="overflow-x-auto">
					<DataTable
						{columns}
						{rows}
						label="Documents"
						getKey={(d) => d.uid}
						bind:sort
						bind:collapsed
						groupBy={folders ? groupByType : undefined}
						groupLead={canWrite ? bandSelect : undefined}
						{empty}
						onRowClick={rowOpen}
						rowClass={() => 'hover:bg-muted/30'}
					>
						{#snippet cell(doc, column)}
							{#if column.key === 'select'}
								{@render rowSelect(doc)}
							{:else if column.key === 'format'}
								{@const FmtIcon = FORMAT_META[doc.documentType].icon}
								<div
									class="bg-muted/60 flex size-8 shrink-0 items-center justify-center rounded-lg"
								>
									<FmtIcon class="size-4 {FORMAT_META[doc.documentType].cls}" />
								</div>
							{:else if column.key === 'name'}
								{@render docName(doc, false)}
								{#if doc.version}
									<span class="text-muted-foreground text-xs" title="Template version">
										{doc.version}
									</span>
								{/if}
								{#if doc.status === 'queued' || doc.status === 'failed'}
									<span class="mt-1 flex min-w-0 items-center gap-1.5">
										{@render renderState(doc)}
									</span>
								{/if}
							{:else if column.key === 'date'}
								<span title={formatDateTime(doc.createdAt)}>
									{formatDate(doc.createdAt)}
								</span>
							{:else if column.key === 'author'}
								{#if doc.createdBy}
									<TruncatedText text={doc.createdBy} class="text-xs" />
								{:else}
									<span class="text-muted-foreground text-xs">—</span>
								{/if}
							{:else if column.key === 'type'}
								<span class="flex min-w-0 items-center gap-1.5">
									<Badge variant="outline" class="font-normal">{typeLabel(doc)}</Badge>
									<span class="text-muted-foreground shrink-0 text-xs"
										>{FORMAT_LABELS[doc.documentType]}</span
									>
								</span>
							{:else if column.key === 'delivery'}
								{#if doc.isFile}
									<span class="text-muted-foreground text-xs">—</span>
								{:else}
									{@render delivery(doc)}
								{/if}
							{:else if column.key === 'size'}
								{formatBytes(doc.sizeBytes || null)}
							{:else if column.key === 'actions'}
								<div class="flex shrink-0 items-center justify-end">
									{@render rowActions(doc)}
								</div>
							{/if}
						{/snippet}
					</DataTable>
				</div>
			{/if}

			{#if !folders && visibleDocs.length > 0}
				<PaginationStrip
					label={rangeLabelFromOffset((docsPage - 1) * PER_PAGE, rows.length, visibleDocs.length)}
					bind:page={docsPage}
					pageCount={docsPageCount}
				/>
			{/if}
		</CardContent>
	</Card>

	<!-- Recent activity — this client's document + share events off the action stream, humanised by
	     the shared EventFeed. EVENT data, so shown only with activity:read (the loader gates the
	     fetch); hidden otherwise and the list spans full width. The full trail is the Events tab. -->
	{#if data.canReadActivity}
		<Card
			glass
			class="min-w-0 lg:sticky lg:top-4 lg:flex lg:max-h-[calc(100vh-10rem)] lg:flex-col lg:self-start"
		>
			<div class="border-b p-4 lg:shrink-0">
				<h2 class="text-base font-semibold">Recent activity</h2>
				<p
					class="text-muted-foreground truncate text-xs"
					title="Latest document events for {data.client.name}."
				>
					Latest document events for {data.client.name}.
				</p>
			</div>
			<div class="min-h-0 flex-1 overflow-y-auto p-2 lg:p-3">
				<!-- The shared portal route map, so a row's detail offers "Open document" and reaches the
				     same page the list behind it links to. -->
				<EventFeed
					events={data.events}
					attribution="firm"
					entityHref={(_e: ActionEvent, ent: ActionEntity) => portalEntityHref(ent)}
					emptyText="No document activity yet."
				/>
			</div>
		</Card>
	{/if}
</div>

<!-- Top level so `columns` can reference it. -->
{#snippet selectAllHead()}
	<SelectBox
		state={allState}
		disabled={visibleDocs.length === 0}
		label="Select all"
		ontoggle={() => (selected = toggleMany(selected, visibleDocs))}
	/>
{/snippet}

<!-- A folder's box reaches every row beneath it, shut or open. -->
{#snippet bandSelect(band: { label: string; rows: readonly LibraryRow[] })}
	<SelectBox
		state={selectionOf(selected, band.rows)}
		label={`Select all in ${band.label}`}
		ontoggle={() => (selected = toggleMany(selected, band.rows))}
	/>
{/snippet}

<!-- Snippets shared by the table rows and the grid cards. -->
{#snippet rowSelect(doc: LibraryRow)}
	<SelectBox
		state={selected.includes(doc.uid)}
		label={`Select ${doc.name}`}
		ontoggle={(e) => toggleRow(e, doc.uid)}
	/>
{/snippet}

<!-- `stretch`: the grid card's link covers the card, so a click anywhere opens it. -->
{#snippet docName(doc: LibraryRow, stretch: boolean)}
	{@const title = stretch ? doc.name : undefined}
	{#if doc.isFile}
		<span class="block truncate text-xs font-medium" {title}>{doc.name}</span>
	{:else}
		<a
			href={docHref(doc.uid)}
			class="block truncate text-xs font-medium hover:underline {stretch
				? 'after:absolute after:inset-0'
				: ''}"
			{title}>{doc.name}</a
		>
	{/if}
{/snippet}

{#snippet renderState(doc: LibraryRow)}
	<RenderState
		{doc}
		retrying={retrying === doc.uid}
		onretry={canWrite ? () => retryRender(doc.uid) : undefined}
	/>
{/snippet}

<!-- Read/Unread alone: a filed document is already in the client's portal. -->
{#snippet delivery(doc: LibraryRow)}
	<span
		class="inline-flex items-center gap-1.5"
		title={doc.clientViewed && doc.clientViewedAt
			? `${client.name} viewed this ${formatDateTime(doc.clientViewedAt)}`
			: `${client.name} hasn’t opened this yet`}
	>
		<StatusBadge tone={doc.clientViewed ? 'success' : 'neutral'}>
			{doc.clientViewed ? 'Read' : 'Unread'}
		</StatusBadge>
	</span>
{/snippet}

{#snippet rowActions(doc: LibraryRow)}
	<DropdownMenu.Root>
		<DropdownMenu.Trigger
			class={buttonVariants({ variant: 'ghost', size: 'icon' })}
			aria-label={`Actions for ${doc.name}`}
		>
			<EllipsisIcon class="size-4" />
		</DropdownMenu.Trigger>
		<DropdownMenu.Content align="end" class="w-48">
			{@const items = rowMenuItems(doc, canWrite)}
			{#if items.includes('view')}
				<DropdownMenu.Item onclick={() => goto(docHref(doc.uid))}>View</DropdownMenu.Item>
			{/if}
			<DropdownMenu.Item onclick={() => window.open(contentSrc(doc), '_self')}>
				Download
			</DropdownMenu.Item>
			{#if items.includes('delete')}
				<!-- A document is soft-archived at once (Restore is on its page); a file is confirmed. -->
				<DropdownMenu.Separator />
				<DropdownMenu.Item
					variant="destructive"
					onclick={() => {
						if (!doc.isFile) return void archive(doc.uid);
						deleting = doc;
						deleteOpen = true;
					}}
				>
					Delete
				</DropdownMenu.Item>
			{/if}
		</DropdownMenu.Content>
	</DropdownMenu.Root>
{/snippet}

{#if canWrite}
	<DocumentsBulkBar
		count={selected.length}
		documents={targets.documents.length}
		files={targets.files.length}
		ondeletedocuments={() => requestBulk('documents', targets.documents)}
		ondeletefiles={() => requestBulk('files', targets.files)}
		onclear={resetSelection}
	/>
{/if}

<ConfirmDialog
	bind:open={bulkOpen}
	title={deleteTitle(bulk?.kind ?? 'documents', bulk?.uids.length ?? 0)}
	description={bulk?.kind === 'files'
		? 'Retained on the record for compliance, but it cannot be put back from here.'
		: 'They leave this client’s documents and their portal, and are retained on the record.'}
	confirmLabel="Delete"
	destructive
	onconfirm={() => {
		const target = bulk;
		bulk = null;
		if (target) void runBulk(target.kind, target.uids);
	}}
/>

<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete this file?"
	description={deleting
		? `${deleting.name} was supplied for this client's record. Deleting it removes it from the client's file list.`
		: ''}
	confirmLabel="Delete file"
	destructive
	onconfirm={() => {
		const uid = deleting?.uid;
		deleting = null;
		if (uid) void deleteFile(uid);
	}}
/>

<UploadDialog
	bind:open={uploadOpen}
	forClient={data.clientOption}
	documentTypes={data.documentTypes}
/>

<!-- Ask this client for something, in place (locked to this client). The library feeds agree/
     acknowledge targets; forms feed "complete a form". On success the tab revalidates (the new
     request shows up in the activity feed) — no navigation away. -->
<RequestDialog
	{enhance}
	invalidate={invalidateAll}
	bind:open={requestOpen}
	action="?/createRequest"
	forClient={data.clientOption}
	documents={library}
	forms={data.forms}
/>

<!-- Add from existing documents — file an unassigned firm document under this client (sets made-for
     via ?/assign → PATCH). Only unassigned documents are offered. -->
<Dialog.Root bind:open={assignOpen}>
	<Dialog.Content class="sm:max-w-2xl">
		<Dialog.Header>
			<Dialog.Title>Add an existing document</Dialog.Title>
			<Dialog.Description>
				File a document already in your firm under {client.name}. Only documents not yet filed under
				a client are shown.
			</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-3 py-2">
			{#if assignableDocs.length === 0}
				<p class="text-muted-foreground py-6 text-center text-sm">
					No unassigned documents to add. Everything is already filed under a client.
				</p>
			{:else}
				<div class="flex items-center gap-2">
					<Input bind:value={assignSearch} placeholder="Search documents…" class="h-9 flex-1" />
					<span class="text-muted-foreground shrink-0 text-xs">{assignUids.size} selected</span>
				</div>
				<!-- Multi-select rich rows (not a bare name dropdown): tick any number, then add them all.
				     Each shows type · format · created · author · size — the way documents show in the
				     table — so the right ones are identifiable. -->
				<div class="max-h-80 divide-y overflow-auto rounded-md border">
					<label class="bg-muted/40 flex items-center gap-3 px-3 py-2 text-sm">
						<Checkbox
							checked={allFilteredAssignSelected}
							onCheckedChange={(c) => toggleAllAssign(c === true)}
							aria-label="Select all documents"
						/>
						<span class="font-medium">Select all{assignSearch ? ' (filtered)' : ''}</span>
					</label>
					{#each filteredAssignable as d (d.uid)}
						{@const FmtIcon = FORMAT_META[d.documentType].icon}
						<label class="hover:bg-muted/30 flex items-center gap-3 px-3 py-2.5 transition-colors">
							<Checkbox
								checked={assignUids.has(d.uid)}
								onCheckedChange={(c) => toggleAssign(d.uid, c === true)}
								aria-label={`Select ${d.name}`}
							/>
							<div class="bg-muted/60 flex size-8 shrink-0 items-center justify-center rounded-lg">
								<FmtIcon class="size-4 {FORMAT_META[d.documentType].cls}" />
							</div>
							<div class="min-w-0 flex-1">
								<span class="block truncate text-xs font-medium">{d.name}</span>
								<p class="text-muted-foreground truncate text-xs">
									{typeLabel(d)} · {FORMAT_LABELS[d.documentType]} · {formatDate(d.createdAt)} · {d.createdBy ||
										'—'} · {formatBytes(d.sizeBytes || null)}
								</p>
							</div>
						</label>
					{:else}
						<p class="text-muted-foreground px-3 py-6 text-center text-xs">
							No documents match your search.
						</p>
					{/each}
				</div>
			{/if}
		</div>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (assignOpen = false)}>Cancel</Button>
			<Button disabled={assignUids.size === 0 || assigning} onclick={assign}>
				{assigning
					? 'Adding…'
					: assignUids.size > 1
						? `Add ${assignUids.size} documents`
						: 'Add document'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
