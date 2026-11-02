<script module lang="ts">
	import {
		optionsFrom,
		optionsFromFacet,
		rangeFromBounds,
		type FilterDef
	} from '../listing/index.js';
	import { documentInFlight, libraryTypeLabel, type LibraryRow } from './documents.js';

	// Zero-size rows (no file yet) are excluded: unknown, not the smallest document there is.
	function localSizeBounds(rows: LibraryRow[]): { min: string; max: string } | undefined {
		const sizes = rows.map((d) => d.sizeBytes).filter((n) => Number.isFinite(n) && n > 0);
		if (!sizes.length) return undefined;
		return { min: String(Math.min(...sizes)), max: String(Math.max(...sizes)) };
	}

	/** A pure function so it can be pinned in a test without rendering the panel. */
	export function documentFilterDefs(input: {
		server: boolean;
		rows: LibraryRow[];
		facets: Record<string, string[]>;
		facetLabels: Record<string, Record<string, string>>;
		bounds?: Record<string, number[]>;
	}): FilterDef[] {
		const { server, rows, facets, facetLabels, bounds } = input;

		// Filtered by uid, never display name — two clients can share a name.
		const clientNames = new Map(
			rows
				.filter((d) => d.clientContactUid)
				.map((d) => [d.clientContactUid as string, d.client || (d.clientContactUid as string)])
		);
		const clientLabel = (uid: string): string =>
			facetLabels.client?.[uid] ?? clientNames.get(uid) ?? uid;

		// Type unions two vocabularies (document/file) that label the same slug differently.
		const fileTypeSlugs = new Set(rows.filter((d) => d.isFile).map((d) => d.category));
		const typeOptionLabel = (slug: string): string =>
			libraryTypeLabel(slug, fileTypeSlugs.has(slug));

		const typeOptions = server
			? optionsFromFacet(facets, 'type', typeOptionLabel)
			: optionsFrom(rows, (d) => d.category, typeOptionLabel);
		const authorOptions = server
			? optionsFromFacet(facets, 'author')
			: optionsFrom(rows, (d) => d.createdBy);
		const clientOptions = server
			? optionsFromFacet(facets, 'client', clientLabel)
			: optionsFrom(rows, (d) => d.clientContactUid, clientLabel);

		return [
			{ key: 'type', label: 'Type', options: typeOptions },
			{
				key: 'client',
				label: 'Client',
				options: clientOptions,
				searchPlaceholder: 'Search clients…'
			},
			{
				key: 'author',
				label: 'Author',
				options: authorOptions,
				searchPlaceholder: 'Search authors…'
			},
			// Bytes, not KB/MB: the table formats for reading, the filter stays in the data's own unit.
			{
				key: 'size',
				label: 'Size',
				options: [],
				kind: 'numberrange',
				unit: ' B',
				...((server ? rangeFromBounds(bounds, 'size') : localSizeBounds(rows)) ?? {})
			},
			// No min/max: the API's bounds are numeric-only, so the calendar offers no capped window.
			{ key: 'created', label: 'Created', options: [], kind: 'daterange' }
		];
	}
</script>

<script lang="ts">
	// The documents library — the platform portal's /documents page, extracted so the admin portal
	// can render the same tenant book (the portal waterfall: ops are a proxy middle-office).
	//
	// Data comes in as typed props (the app's loader fetches the list + the feed up front), every
	// href is built by the injected `links`, and the mutations only exist when the app passes
	// `actions` — each callback owns its own fetch/toast/invalidate, this component only owns the
	// selection/pending state around them. Actions absent ⇒ the surface renders read-only: no
	// upload, no delete.
	//
	// This library shows the LIVE book and nothing else. The Live/Archived switch is gone with the
	// vocabulary behind it: archiving reads as DELETING now (`STATUS_META.archived`), and a listing
	// that offers to browse a firm's deleted documents invites undeleting as an ordinary act. A
	// deleted document is still reachable by its own URL (the record keeps Restore) and its home is
	// the ops-side "Deleted documents" view — accepted deliberately, not overlooked.
	import { Badge } from '../../ui/badge/index.js';
	import { Button, buttonVariants } from '../../ui/button/index.js';
	import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import RefreshWhile from '../../ui/refresh-while/refresh-while.svelte';
	import { REFRESH_WHILE } from '../../notifications/poll-controller.js';
	import {
		DataTable,
		escapeSegment,
		type DataTableColumn,
		type DataTableGroupKey,
		type DataTableGroupRow,
		type DataTableSort
	} from '../../ui/data-table/index.js';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis-vertical';
	import FileSearchIcon from '@lucide/svelte/icons/file-search';
	import {
		barValueFrom,
		matches,
		withBarValue,
		withinRange,
		isFiltered,
		type ListingQuery
	} from '../listing/index.js';
	import ListingOverflow from '../listing/listing-overflow.svelte';
	import ListingToolbar from '../listing/listing-toolbar.svelte';
	import { FORMAT_META } from './format-meta.js';
	import { formatBytes } from '../../../bytes.js';
	import { formatDate, formatDateTime } from '../../../dates.js';
	import { FORMAT_LABELS, downloadName, isDownloadable } from './documents.js';
	import DocumentsBulkBar from './documents-bulk-bar.svelte';
	import RenderState from './render-state.svelte';
	import SelectBox from './select-box.svelte';
	import {
		bulkTargets,
		deleteTitle,
		pickRow,
		renderedOrder,
		selectionOf,
		toggleMany
	} from './selection.js';
	import EventFeed from '../../audit/event-feed.svelte';
	import type { ActionEntity, ActionEvent } from '../../audit/describe.js';
	import type { DocumentsLibraryLinks } from './types.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';

	/** The mutations this library can host. Absent ⇒ read-only. Each callback owns its own
	 *  fetch/toast/invalidate (the app's form actions and dialogs stay app-side); this component
	 *  only wraps them in the selection/pending state the table needs.
	 *
	 *  Every member is OPTIONAL because a permission gates each affordance: drafting
	 *  (upload/delete/retry) is `documents:write` (docs/api.md "six splits"). The app passes
	 *  exactly the callbacks the caller's permissions earn, and each affordance renders only when
	 *  its callback is present.
	 *
	 *  There is no `notify` here: notifying names ONE document and ONE client, and this surface holds
	 *  neither — see the toolbar note. `documents:send` gates it where it lives instead. */
	export type DocumentsLibraryActions = {
		/** Delete documents — a SOFT delete (the API's `archive`), already confirmed here. Resolve
		 *  once the list has been refreshed. */
		archive?: (uids: string[]) => Promise<void>;
		/** Delete supporting files (already confirmed here). */
		deleteFiles?: (uids: string[]) => Promise<void>;
		/** Requeue a failed render. */
		retryRender?: (uid: string) => Promise<void>;
		// No `retype`: a document's type is set when it is uploaded and is not changed afterwards.
		/** Open the app's upload dialog, optionally pre-filled with OS-dropped files. */
		upload?: (presetFiles?: File[]) => void;
	};

	let {
		documents,
		openFolders = false,
		activity = [],
		canReadActivity = true,
		loadError = false,
		listing = null,
		facets = {},
		facetLabels = {},
		bounds = {},
		total = null,
		onquery,
		links,
		actions = null,
		/** Re-runs the app's load (`invalidateAll`) behind the error banner's Retry. */
		onretry,
		/** SPA navigation (the app passes `goto`); falls back to a full-page navigation. */
		onnavigate
	}: {
		documents: LibraryRow[];
		/** Start with every folder open. Off: client folders start shut. */
		openFolders?: boolean;
		/** Recent document events (the action stream, `document_family` scoped) for the "Recent
		 *  activity" glance — humanised by the shared EventFeed, not a bespoke feed. */
		activity?: ActionEvent[];
		/** SHOW the recent-activity glance at all? It reads the event stream, so the app gates it on
		 *  activity:read and hides the panel (not an empty card) when the caller lacks it. */
		canReadActivity?: boolean;
		// No `documentTypes`: the tenant's type registry fed the retype menu and the drag-to-a-type
		// strip, both gone, and the Type filter reads the API's facets instead — which describe the
		// whole match set rather than the types the registry happens to hold. The upload dialog
		// still needs the registry, and gets it app-side.
		loadError?: boolean;
		/** The resolved server query (search + filters), when the app drives this listing from the
		 *  URL. Supplying it AND `onquery` puts the toolbar in server mode: `documents` is then the
		 *  API's answer, already searched and filtered, and this component only asks for changes.
		 *  Absent ⇒ the local, client-side filtering a mount that has not adopted it still gets. */
		listing?: ListingQuery | null;
		/** The API's facet lists (`type` / `author` / `client`), which describe the WHOLE match set.
		 *  Options derived from the rows on screen could only ever offer values already visible. */
		facets?: Record<string, string[]>;
		/** Readable names for facet values that are ids — `client`, whose options are uids. From the
		 *  SERVER, because a facet is computed with its own filter lifted: labelling from the rows on
		 *  screen resolves only the client already filtered to and leaves every other option showing a
		 *  bare nanoid, and past the fetch cap it cannot name a client whose rows were not loaded. */
		facetLabels?: Record<string, Record<string, string>>;
		bounds?: Record<string, number[]>;
		/** How many rows MATCH across the whole book (the API's `total`), which is what the counter
		 *  counts up to in server mode. Null ⇒ count against the rows that were loaded. */
		total?: number | null;
		onquery?: (next: ListingQuery) => void;
		links: DocumentsLibraryLinks;
		actions?: DocumentsLibraryActions | null;
		onretry?: () => void;
		onnavigate?: (href: string) => void;
	} = $props();

	function navigate(href: string): void {
		if (onnavigate) onnavigate(href);
		else if (typeof location !== 'undefined') location.assign(href);
	}

	// One slice: the live book. (A deleted document is not browsable from here — see the header.)
	const rows: LibraryRow[] = $derived(documents);
	let search = $state('');
	let selected = $state<string[]>([]);

	// Human label for a type. BOTH kinds of row carry a raw slug now (a document's document_type, a
	// file's attachment_type) so the Type filter can match either against the API's facets, so both
	// are rendered through the one label function (./documents.js).
	const typeLabel = (d: LibraryRow): string => libraryTypeLabel(d.category, d.isFile);

	// Server-driven when `listing` + `onquery` are supplied: `documents` is the API's answer, already
	// searched and filtered, and this component only asks for changes. The clients listing forks the
	// same way — a mount that has not adopted the query keeps the local filtering below.
	const server = $derived(!!listing && !!onquery);
	const term = $derived(listing ? listing.q : search);

	/** A narrowed list is a DIFFERENT set of rows, so a selection made against the old one must not
	 *  survive into it: the bulk bar's count is its delete target, and keeping three ticks through a
	 *  filter change left "3 selected" beside a "Delete 1" button — or beside no button at all when
	 *  none of the three survived. Called from every query change and from nowhere else; a re-SORT
	 *  reorders the same rows and deliberately keeps the selection. */
	function resetSelection(): void {
		selected = [];
		lastPicked = null;
	}

	// The activity glance's way OUT: a row names the document it happened to, and this surface knows
	// where documents live, so the detail dialog can offer "Open document" rather than leaving the
	// reader to find it in the list behind. Only documents — the other entities a document event
	// names (the client it is filed for, the template it came from) have no href on this surface.
	const feedEntityHref = (_e: ActionEvent, ent: ActionEntity) =>
		ent.kind === 'document' && ent.uid ? links.document(ent.uid) : null;

	// Clicking a row's type narrows by that type. It used to be a separate `activeFolder` drill-in
	// with its own breadcrumb, which meant two controls narrowing by the same idea and disagreeing:
	// the breadcrumb said "All documents & files ›  Statement" while the Type dropdown still read
	// "All types". One concept now — the click just sets the filter.
	/** The filter bar's state in LOCAL mode. One object, because the bar hands back the whole
	 *  selection and three separate strings could not express "Suitability OR Illustration". */
	let localBar = $state<Record<string, string[]>>({});
	const localPick = (key: string) => localBar[key] ?? [];
	const BAR_KEYS = ['type', 'client', 'author', 'size', 'created'] as const;
	const RANGE_KEYS = ['size', 'created'] as const;
	const barValue = $derived(server ? barValueFrom(listing, BAR_KEYS, RANGE_KEYS) : localBar);

	/** ONE writer for both modes. Selection is dropped on any change: the rows behind a tick are
	 *  gone once the filter moves, and a bulk action on a selection you can no longer see is the
	 *  defect `resetSelection` exists for. */
	function setBar(next: Record<string, string[]>): void {
		resetSelection();
		if (server && listing && onquery) onquery(withBarValue(listing, BAR_KEYS, next, RANGE_KEYS));
		else localBar = next;
	}

	const filterDefs = $derived(documentFilterDefs({ server, rows, facets, facetLabels, bounds }));

	/** Is the list narrowed by something the reader can undo? Drives the empty state's copy, so
	 * "nothing here" is never blamed on the library when a filter is doing it. */
	const filtersActive = $derived(
		server ? isFiltered(listing!) : !!search || Object.keys(localBar).length > 0
	);

	function clearFilters(): void {
		resetSelection();
		if (server && listing && onquery) {
			onquery({ ...listing, q: '', filters: {}, offset: 0 });
			return;
		}
		search = '';
		localBar = {};
	}

	/** What the row counter counts UP TO. In server mode the API's `total` is the size of the match
	 *  set across the whole book — the reason the filters moved server-side at all; locally it is
	 *  simply how many rows were loaded. */
	const matchTotal = $derived(server ? (total ?? rows.length) : rows.length);
	/** True when the API matched MORE than it handed us: the fetch is capped, and the list can only
	 *  show what was fetched. Saying "200 of 500" and stopping at 200 would promise 300 documents no
	 *  amount of paging can reach, so the counter says the list is cut and the filters are the way
	 *  through. (Paging server-side is what removes this; it needs the two endpoints merged first.) */
	const capped = $derived(server && matchTotal > rows.length);

	// Day-count since the epoch: `withinRange` only compares real numbers.
	function dateOrdinal(iso: string | undefined): number | undefined {
		if (!iso) return undefined;
		const d = new Date(iso);
		if (Number.isNaN(d.getTime())) return undefined;
		return Math.floor(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 86400000);
	}

	const visible = $derived.by(() => {
		const list = rows.filter((d) => {
			if (d.status === 'superseded') return false;
			// In server mode the API has already searched and filtered; re-applying either here would
			// narrow the page a second time and make the row counter disagree with `total`.
			if (server) return true;
			// The name and the client, as GET /documents?q= matches: both modes answer a surname.
			if (search && !`${d.name}\n${d.client ?? ''}`.toLowerCase().includes(search.toLowerCase())) {
				return false;
			}
			if (!matches(localPick('type'), d.category)) return false;
			if (!matches(localPick('author'), d.createdBy)) return false;
			if (!matches(localPick('client'), d.clientContactUid ?? '')) return false;
			if (!withinRange(d.sizeBytes || NaN, localPick('size'))) return false;
			const createdBound = localPick('created').map((v) => {
				const ordinal = dateOrdinal(v);
				return ordinal === undefined ? '' : String(ordinal);
			});
			if (!withinRange(dateOrdinal(d.createdAt) ?? NaN, createdBound)) return false;
			return true;
		});
		return list;
	});

	// Delete documents (the API's soft `archive`): the row leaves this list and stays on the record.
	async function archive(uids: string[]): Promise<void> {
		const fn = actions?.archive;
		if (!fn) return;
		await fn(uids);
		selected = [];
	}

	// Supporting-file deletes post to the app's /files action; uploads for both kinds go through
	// the app's unified upload dialog.
	async function deleteFiles(uids: string[]): Promise<void> {
		const fn = actions?.deleteFiles;
		if (!fn) return;
		await fn(uids);
		selected = [];
	}

	// Requeue a failed render. The row stays in place until the worker picks the job up again, so
	// the callback's toast (and the refreshed status badge) is the whole feedback.
	let retrying = $state<string | null>(null);
	async function retryRender(uid: string): Promise<void> {
		const fn = actions?.retryRender;
		if (!fn || retrying) return;
		retrying = uid;
		try {
			await fn(uid);
		} finally {
			retrying = null;
		}
	}

	// Destructive actions (deleting documents, deleting files) are confirmed first — the kebab menu
	// and the bulk-action bar both route through these.
	let archiveConfirmOpen = $state(false);
	let archiveTarget = $state<string[]>([]);
	function requestArchive(uids: string[]): void {
		archiveTarget = uids;
		archiveConfirmOpen = true;
	}
	let deleteConfirmOpen = $state(false);
	let deleteTarget = $state<string[]>([]);
	function requestDelete(uids: string[]): void {
		deleteTarget = uids;
		deleteConfirmOpen = true;
	}

	// No Status column: a queued/failed render shows beside the name. `lead`: the box steps in with
	// the folder depth; `h-12` is a data row's height, so band rows match it.
	const columns = $derived<DataTableColumn<LibraryRow>[]>([
		{
			key: 'select',
			header: 'Select',
			head: selectAllHead,
			lead: true,
			headerClass: 'w-9 pl-5',
			cellClass: 'h-12 pl-5'
		},
		{
			key: 'name',
			header: 'Document',
			sortable: true,
			indent: true,
			cellClass: 'max-w-[22rem] py-3',
			sortValue: (d) => d.name.toLowerCase()
		},
		{
			key: 'date',
			header: 'Created',
			sortable: true,
			defaultDir: 'desc',
			cellClass: 'text-muted-foreground text-xs',
			sortValue: (d) => d.createdAt
		},
		{
			key: 'author',
			header: 'Author',
			sortable: true,
			sortValue: (d) => (d.createdBy || '').toLowerCase()
		},
		{
			key: 'client',
			header: 'Client',
			sortable: true,
			cellClass: 'text-muted-foreground max-w-[10rem] truncate text-xs',
			sortValue: (d) => d.client.toLowerCase()
		},
		{
			key: 'type',
			header: 'Type',
			sortable: true,
			sortValue: (d) => typeLabel(d).toLowerCase()
		},
		{
			key: 'size',
			header: 'Size',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			cellClass: 'text-muted-foreground text-xs tabular-nums',
			sortValue: (d) => d.sizeBytes
		},
		{ key: 'actions', header: '' }
	]);

	let sort = $state<DataTableSort | null>({ key: 'client', dir: 'asc' });

	/** Newest first; the table's stable sort keeps this as the tiebreak. */
	const newestFirst = $derived([...visible].sort((a, b) => b.createdAt.localeCompare(a.createdAt)));

	/** Keyed by uid (two clients can share a name); `column` lets the Client sort order them. */
	const clientBand = (d: LibraryRow): DataTableGroupKey =>
		d.client
			? { key: d.clientContactUid ?? d.client, label: d.client, column: 'client' }
			: { key: '_none', label: 'No client', last: true, column: 'client' };
	const typeBand = (d: LibraryRow): DataTableGroupKey => ({
		key: d.category,
		label: libraryTypeLabel(d.category),
		column: 'type'
	});
	const groupBy = (d: LibraryRow): DataTableGroupKey[] => [clientBand(d), typeBand(d)];

	const clientPath = (d: LibraryRow): string => escapeSegment(clientBand(d).key);

	/** Shut bands by path: client bands start shut, also when a new one arrives; type bands open. */
	// svelte-ignore state_referenced_locally
	let collapsed = $state<Set<string>>(new Set(openFolders ? [] : visible.map(clientPath)));
	// svelte-ignore state_referenced_locally
	const seen = new Set(visible.map(clientPath));
	$effect(() => {
		if (openFolders) return;
		const fresh = visible.map(clientPath).filter((p) => !seen.has(p));
		if (!fresh.length) return;
		for (const p of fresh) seen.add(p);
		collapsed = new Set([...collapsed, ...fresh]);
	});

	/** No pager: a page break would split a folder. Shift-ranges skip rows in shut bands. */
	const renderedUids = $derived(renderedOrder(newestFirst, groupBy, columns, sort, collapsed));

	/** Shift-range anchor, as a uid: a sort or an opened folder moves positions. */
	let lastPicked = $state<string | null>(null);
	function toggleRow(e: MouseEvent, uid: string): void {
		selected = pickRow(selected, renderedUids, uid, lastPicked, e.shiftKey);
		lastPicked = uid;
	}
	/** Select-all covers the whole filtered set, shut folders included. */
	const allState = $derived(selectionOf(selected, visible));
	const targets = $derived(bulkTargets(selected, visible));

	// Whole-row open, like a file manager (documents open their detail page; files download).
	function rowOpen(d: LibraryRow): void {
		if (d.isFile) location.href = links.fileDownload(d.uid);
		else navigate(links.document(d.uid));
	}

	// (The inline quick-preview sheet is gone — the row opens the document RECORD, which renders the
	// same bytes full-size with zoom and in-document search. `links.documentContent` is still used by
	// the record and the client tab, so it stays on the links contract.)

	// A document's TYPE IS NOT CHANGEABLE from here. There used to be a kebab submenu and a
	// drag-a-row-onto-a-type-chip gesture; both are gone, along with the row `draggable` attribute
	// and the "Change type:" strip they needed. The type is chosen at upload (where the dialog can
	// also create a new one) and is a property of the document from then on.
	//
	// The OS-file drag below is a DIFFERENT drag and stays: dropping files from the desktop onto the
	// list opens the upload dialog pre-filled.
	let dragOver = $state(false);
	function onListDrop(e: DragEvent): void {
		dragOver = false;
		const upload = actions?.upload;
		if (!upload) return;
		if (!e.dataTransfer?.files?.length) return;
		e.preventDefault();
		upload([...e.dataTransfer.files]);
	}

	const renderingSome = $derived(documents.some((d) => documentInFlight(d.status)));
</script>

<RefreshWhile
	active={renderingSome && !!onretry}
	interval={REFRESH_WHILE.inFlightList}
	onpoll={() => onretry?.()}
/>

{#snippet clearLibraryFilters()}
	<Button variant="outline" size="sm" onclick={clearFilters}>Clear filters</Button>
{/snippet}

{#snippet uploadFirstDocument()}
	<Button size="sm" onclick={() => actions?.upload?.()}>Upload a document</Button>
{/snippet}

<!-- A filter emptying the list is never reported as an empty library. Each branch carries the
     one control that resolves it; a read-only mount (no `upload`) gets the sentence alone. -->
{#snippet libraryEmpty()}
	<EmptyState
		compact
		icon={FileSearchIcon}
		title={filtersActive ? 'No documents match your filters' : 'No documents yet'}
		description={filtersActive
			? 'Try a different search or filter, or clear them to see the whole library.'
			: 'Documents you upload, and documents generated from a template, appear here.'}
		action={filtersActive ? clearLibraryFilters : actions?.upload ? uploadFirstDocument : undefined}
	/>
{/snippet}

<!-- THE PAGE DOES NOT SCROLL FROM `md` — the LIST does.
     This was a flowing `space-y-5 pb-24` column, so a full page of documents pushed the window
     scrollbar down and took the toolbar, the filters and the activity rail off the top with it: to
     change a filter you first had to scroll back up, on the one surface whose whole job is finding a
     document. The Events page solved this already, and this is the same shape — fill the inset below
     the topbar, clip, and give the scroll to the panes inside. On a phone the PAGE scrolls instead,
     so the table can take the width its columns need and move sideways rather than overlapping.
     The negative margin cancels the shell's content padding so the fixed-height box fills the area
     exactly, then re-adds its own padding within. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#snippet listingActions()}
		<ListingOverflow>
			<DropdownMenu.Item>
				{#snippet child({ props })}
					<a {...props} href={links.requests}>Requests</a>
				{/snippet}
			</DropdownMenu.Item>
			{#if actions?.upload && links.templates}
				<DropdownMenu.Item>
					{#snippet child({ props })}
						<a {...props} href={links.templates}>Generate</a>
					{/snippet}
				</DropdownMenu.Item>
			{/if}
			{#if actions?.upload}
				<DropdownMenu.Item onclick={() => actions?.upload?.()}>Upload</DropdownMenu.Item>
			{/if}
		</ListingOverflow>
	{/snippet}

	<!-- A failed/401 fetch must never read as "no documents" — retry re-runs load through a fresh
	     request, where the app's hooks refresh an expired token. -->
	{#if loadError}
		<LoadErrorBanner message="Some information couldn’t be loaded. Try again." onRetry={onretry} />
	{/if}

	<!-- `min-h-0` is what lets a flex child be SHORTER than its content: without it the grid keeps
	     its natural height and the clip above just hides the overflow instead of moving it inside. -->
	<div
		class="grid min-w-0 gap-5 md:min-h-0 md:flex-1 md:overflow-hidden {canReadActivity
			? 'lg:grid-cols-[minmax(0,1fr)_22rem]'
			: ''}"
	>
		<!-- Recent activity (side column) — a viewport-tall, sticky flex column so the feed fills the
		     screen beside the list and scrolls within itself rather than stretching the page. Capped at
		     100vh minus the top bar so it stops short of the fold rather than past it.
		     Shown only with activity:read (it is EVENT data); hidden entirely otherwise, and the list
		     then spans the full width. -->
		{#if canReadActivity}
			<!-- Fills its grid cell now rather than being capped at a guessed `100vh - 10rem`: the
			     parent owns the height, so the rail cannot disagree with it (the guess was wrong
			     whenever the header wrapped). Sticky/self-start are gone with the page scroll. -->
			<div class="min-h-0 lg:order-2 lg:flex lg:flex-col">
				<!-- Recent activity — the document + share events off the action stream, humanised by the
			     shared EventFeed (one vocabulary with the firm-wide Events page). A glance, capped by
			     the loader; the full, filterable trail lives on /events. -->
				<Card glass class="lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
					<CardHeader class="lg:shrink-0">
						<!-- No description. "The latest document events across your clients" narrates the panel it
						     sits in — the title already says Recent activity, and the rows below say whose and
						     what. `docs/design-language.md`: omit a description unless it says something the
						     title cannot. -->
						<CardTitle class="text-base">Recent activity</CardTitle>
					</CardHeader>
					<CardContent class="lg:flex lg:min-h-0 lg:flex-1 lg:flex-col lg:overflow-hidden">
						<div class="max-h-[30rem] overflow-y-auto px-1 lg:max-h-none lg:min-h-0 lg:flex-1">
							<EventFeed
								events={activity}
								attribution="firm"
								filterable
								entityHref={feedEntityHref}
								emptyText="No document activity yet."
							/>
						</div>
					</CardContent>
				</Card>
			</div>
		{/if}

		<!-- One compact action menu shared by both views (kebab, like a context menu). -->
		{#snippet rowMenu(doc: LibraryRow)}
			<DropdownMenu.Root>
				<DropdownMenu.Trigger
					class={buttonVariants({ variant: 'ghost', size: 'icon' })}
					aria-label="Actions"
				>
					<EllipsisIcon class="size-4" />
				</DropdownMenu.Trigger>
				<!-- One menu, one order, both surfaces: View · Download · Delete. The client
				     record's Documents tab offers exactly the same four, so a reader does not have to
				     learn a second arrangement of the same document.
				     Each item still renders only when the caller's permissions earn it, so a read-only
				     reader gets View and Download and no more.
				     A supporting FILE takes the two that apply to it: it has no record to view (its name
				     is not a link) and no type to change. -->
				<DropdownMenu.Content align="end" class="w-48">
					{#if doc.isFile}
						<DropdownMenu.Item>
							{#snippet child({ props })}
								<a {...props} href={links.fileDownload(doc.uid)} download={downloadName(doc)}
									>Download</a
								>
							{/snippet}
						</DropdownMenu.Item>
						{#if actions?.deleteFiles}
							<DropdownMenu.Separator />
							<DropdownMenu.Item variant="destructive" onclick={() => requestDelete([doc.uid])}>
								Delete
							</DropdownMenu.Item>
						{/if}
					{:else}
						<DropdownMenu.Item onclick={() => navigate(links.document(doc.uid))}
							>View</DropdownMenu.Item
						>
						<!-- Download was offered for supporting files but not for documents, which is the one
						     row where a reader most expects it. `documentContent` is the same-origin proxy the
						     record's own Download uses.
						     It has to be a real ANCHOR carrying `download`, not an onclick that navigates: the
						     API serves a PDF `Content-Disposition: inline`, so `location.assign` renders it in
						     the tab instead of saving it — the same defect the record's own Download button
						     already carries a comment about. The attribute is honoured because the proxy is
						     same-origin, and it names the file after the document rather than its uid. -->
						{#if isDownloadable(doc)}
							<DropdownMenu.Item>
								{#snippet child({ props })}
									<a {...props} href={links.documentContent(doc.uid)} download={downloadName(doc)}
										>Download</a
									>
								{/snippet}
							</DropdownMenu.Item>
						{/if}
						<!-- `deletable === false` withholds Delete: the API answers 409 for a document the
						     firm does not own (one synced from a custodian), so offering the button would
						     only ever produce an error toast. Withheld, not disabled — a disabled item in a
						     menu you have to open to read is worse than one that is simply not there, and
						     the record states the reason. -->
						{#if actions?.archive && doc.deletable !== false}
							<DropdownMenu.Separator />
							<DropdownMenu.Item variant="destructive" onclick={() => requestArchive([doc.uid])}>
								Delete
							</DropdownMenu.Item>
						{/if}
					{/if}
				</DropdownMenu.Content>
			</DropdownMenu.Root>
		{/snippet}

		{#snippet docCell(doc: LibraryRow, column: DataTableColumn<LibraryRow>)}
			{#if column.key === 'select'}
				<SelectBox
					state={selected.includes(doc.uid)}
					label={`Select ${doc.name}`}
					ontoggle={(e) => toggleRow(e, doc.uid)}
				/>
			{:else if column.key === 'name'}
				{@const Icon = FORMAT_META[doc.documentType].icon}
				<span class="flex min-w-0 items-center gap-2 text-xs font-medium">
					<Icon class="size-4 shrink-0 {FORMAT_META[doc.documentType].cls}" />
					{#if doc.isFile}
						<span class="truncate">{doc.name.replace(/\.pdf$|\.jpg$|\.png$|\.docx$/, '')}</span>
					{:else}
						<a href={links.document(doc.uid)} class="truncate hover:underline"
							>{doc.name.replace(/\.pdf$|\.jpg$|\.png$|\.docx$/, '')}</a
						>
					{/if}
				</span>
				{#if doc.status === 'queued' || doc.status === 'failed'}
					<span class="mt-1 flex min-w-0 items-center gap-1.5">
						<RenderState
							{doc}
							retrying={retrying === doc.uid}
							onretry={actions?.retryRender ? () => retryRender(doc.uid) : undefined}
						/>
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
			{:else if column.key === 'client'}
				<!-- Linked only by uid: matching a person by display name reaches the wrong one. -->
				{#if doc.client}
					<TruncatedText
						text={doc.client}
						href={doc.clientContactUid != null && links.client
							? links.client(doc.clientContactUid)
							: undefined}
					/>
				{:else}
					<span class="text-muted-foreground">—</span>
				{/if}
			{:else if column.key === 'type'}
				<span class="flex min-w-0 items-center gap-1.5">
					<button
						type="button"
						onclick={() => setBar({ ...barValue, type: [doc.category] })}
						class="min-w-0 rounded-full"
					>
						<Badge variant="outline" class="hover:bg-muted font-normal">{typeLabel(doc)}</Badge>
					</button>
					<span class="text-muted-foreground shrink-0 text-xs"
						>{FORMAT_LABELS[doc.documentType]}</span
					>
				</span>
			{:else if column.key === 'size'}
				<!-- `toRow` maps "no stored file" to 0; `|| null` shows an em dash, not "0 B". -->
				{formatBytes(doc.sizeBytes || null)}
			{:else if column.key === 'actions'}
				<span class="flex items-center justify-end gap-0.5">
					{@render rowMenu(doc)}
				</span>
			{/if}
		{/snippet}

		<!-- A band's box reaches every row beneath it, nested or shut. -->
		{#snippet bandSelect(band: DataTableGroupRow<LibraryRow>)}
			<SelectBox
				state={selectionOf(selected, band.rows)}
				label={`Select all in ${band.label}`}
				ontoggle={() => (selected = toggleMany(selected, band.rows))}
			/>
		{/snippet}

		{#snippet toolbar()}
			<div class="flex shrink-0 items-center gap-3">
				<div class="min-w-0 flex-1">
					<!-- No Live/Archived switch: this library is the live book (see the component header —
					     browsing a firm's deleted documents is an ops view, not a tab in here). And no
					     documents/files kind filter: both kinds arrive in one list and the Type column
					     already says which is which, so that control only ever narrowed what was already
					     in front of you.
					     `onsearch` is what puts the toolbar in SERVER mode — it debounces and hands the term
					     up instead of filtering locally. Absent ⇒ the local box, for a mount that has not
					     adopted the query. -->
					<!-- `onsearch` is ALWAYS supplied, and that is deliberate. Without it `ListingToolbar`
					     writes the typed term into its own `query` prop and stops — and `query` is passed
					     here as an expression, not a binding, so in local mode the term never reached
					     `search` and typing in the box did nothing at all. One handler, both modes: the
					     server one navigates, the local one sets the state the local predicate reads. -->
					<ListingToolbar
						query={term}
						placeholder="Search by name or client…"
						dirty={filtersActive}
						onclear={clearFilters}
						onsearch={(next) => {
							resetSelection();
							if (server && listing && onquery) onquery({ ...listing, q: next, offset: 0 });
							else search = next;
						}}
						{filterDefs}
						filterValue={barValue}
						onfilters={setBar}
						actions={listingActions}
					>
						{#snippet filters()}
							<!-- Row count. In server mode `total` is the size of the MATCH SET across the whole
							     book, not of the page — which is the point of moving the filters server-side.
							     When the match set is larger than the fetch, the counter says so rather than
							     quoting a total the list cannot reach. -->
							<span class="text-muted-foreground text-xs tabular-nums">
								{visible.length} of {matchTotal}
							</span>
							{#if capped}
								<span class="text-muted-foreground text-xs">
									· showing the first {rows.length} — narrow the filters to reach the rest
								</span>
							{/if}
						{/snippet}
					</ListingToolbar>
				</div>
			</div>
		{/snippet}

		<!-- Documents & files list. Drop files from the OS anywhere on it to upload them. -->
		<Card
			glass
			class="flex min-w-0 flex-col md:min-h-0 md:flex-1 lg:order-1 {dragOver
				? 'ring-ring ring-2'
				: ''}"
			role="region"
			aria-label="Documents and files — drop files here to upload"
			ondragover={(e) => {
				if (e.dataTransfer?.types.includes('Files')) {
					e.preventDefault();
					dragOver = true;
				}
			}}
			ondragleave={() => (dragOver = false)}
			ondrop={onListDrop}
		>
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				{@render toolbar()}
				<DataTable
					{columns}
					rows={newestFirst}
					getKey={(d) => d.uid}
					cell={docCell}
					bind:sort
					{groupBy}
					bind:collapsed
					groupLead={bandSelect}
					onRowClick={rowOpen}
					label="Documents and files"
					scroll
					empty={libraryEmpty}
				/>
			</CardContent>
		</Card>
	</div>
</div>

<!-- Top level so `columns` can reference it. -->
{#snippet selectAllHead()}
	<SelectBox
		state={allState}
		disabled={visible.length === 0}
		label="Select all"
		ontoggle={() => (selected = toggleMany(selected, visible))}
	/>
{/snippet}

<DocumentsBulkBar
	count={selected.length}
	documents={targets.documents.length}
	files={targets.files.length}
	ondeletedocuments={actions?.archive ? () => requestArchive(targets.documents) : undefined}
	ondeletefiles={actions?.deleteFiles ? () => requestDelete(targets.files) : undefined}
	onclear={() => (selected = [])}
/>

<!-- A delete here is the API's soft `archive`, so the copy says what actually happens — off the
     library, still on the record — and deliberately promises neither permanence (nothing is
     destroyed) nor a way back from this listing (there is none: the document's own page has
     Restore). -->
<ConfirmDialog
	bind:open={archiveConfirmOpen}
	title={deleteTitle('documents', archiveTarget.length)}
	description={archiveTarget.length === 1
		? 'It leaves your library and any client portal it appears in, and is retained on the record.'
		: 'They leave your library and any client portal they appear in, and are retained on the record.'}
	confirmLabel="Delete"
	destructive
	onconfirm={() => archive(archiveTarget)}
/>
<ConfirmDialog
	bind:open={deleteConfirmOpen}
	title={deleteTitle('files', deleteTarget.length)}
	description="Retained on the record for compliance, but it cannot be put back from here."
	confirmLabel="Delete"
	destructive
	onconfirm={() => deleteFiles(deleteTarget)}
/>
