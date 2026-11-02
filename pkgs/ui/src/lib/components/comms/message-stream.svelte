<script lang="ts">
	/**
	 * The Activity listing — every email the firm has sent, as a table.
	 *
	 * Shaped like a send log: To · Status · Subject · Sent, an envelope on the address, status as
	 * a pill, relative time, a row kebab. A table rather than a card list because that is what this
	 * is: four short, comparable facts per row, scanned down a column.
	 *
	 * SORTED BY THE SERVER — a header click refetches. The default order stays off the wire so the
	 * endpoint keeps its keyset cursor instead of falling back to offset paging.
	 *
	 * Filters are applied SERVER-SIDE and never written to the URL — the doctrine `EventStream` and
	 * `/activity` already follow — and the app injects every fetch, so this package builds no routes and
	 * knows no endpoints. Opening a message is a real navigation (`messageHref`), not a side pane:
	 * the body is the substance of the page and it wants the width.
	 */
	import MailIcon from '@lucide/svelte/icons/mail';
	import SearchXIcon from '@lucide/svelte/icons/search-x';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import { DataTable, type DataTableColumn, type DataTableSort } from '../ui/data-table/index.js';
	import { Card, CardContent } from '../ui/card/index.js';
	import { Button, buttonVariants } from '../ui/button/index.js';
	import { default as EmptyState } from '../ui/empty-state/empty-state.svelte';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import * as DropdownMenu from '../ui/dropdown-menu/index.js';
	import { untrack } from 'svelte';
	import { ListingToolbar, type FilterDef } from '../book/listing/index.js';
	import { relativeTime } from '../notifications/describe.js';
	import { formatDateTime } from '../../dates.js';
	import { now } from '../notifications/now.svelte.js';
	import type { CommsFacets, CommsFilters, CommsMessage, CommsPage } from './describe.js';
	import { LINK_RECORD } from '../../text-shell.js';
	import {
		EMPTY_COMMS_FILTERS,
		humanise,
		recipientLabel,
		statusMeta,
		templateLabel
	} from './describe.js';

	let {
		messages,
		facets,
		hasMore = false,
		loadError = false,
		sendingDisabled = false,
		messageHref,
		clientHref,
		onOpen,
		onFilterApply,
		onLoadMore,
		onRefresh,
		initialFilters
	}: {
		messages: CommsMessage[];
		/** The endpoint's vocabulary for this firm. Absent ⇒ offer only what the loaded rows carry. */
		facets?: CommsFacets;
		hasMore?: boolean;
		/** The first page failed, so the empty state must read as an error rather than "no mail". */
		loadError?: boolean;
		/**
		 * Whether sending is switched off. A feed whose every row reads "Not sent" has to say so, or
		 * it reads as total failure. A FLAG and not the reason: the reason names configuration, so
		 * carrying it here would put it in the page's serialised data whether or not it is rendered.
		 */
		sendingDisabled?: boolean;
		/** Injected by the app — the shared package never builds a route. */
		messageHref?: (uid: string) => string;
		/** The record of the person a message went TO — the recipient cell.
		 *
		 *  Safe inside a row that also activates on click: `DataTable` steps aside for any anchor
		 *  within a row, so the link navigates to the client and the row keeps opening the message.
		 *  Takes the contact UID, never the serial. Absent ⇒ the name stays plain text, which is the
		 *  read-only posture and how a mount with no client route behaves. */
		clientHref?: (contactUid: string) => string;
		/** Whole-row activation. The subject cell still carries `messageHref` for middle-click. */
		onOpen?: (uid: string) => void;
		/**
		 * Fetch a page. Returns the endpoint's OWN `has_more` alongside the rows — computed there
		 * with the limit+1 trick — because `items.length > 0` is not the same question: a filter
		 * matching exactly three messages returns three rows and offers "Load more" on a complete
		 * list, which then fetches nothing before the button disappears.
		 */
		onFilterApply?: (filters: CommsFilters, sort: DataTableSort | null) => Promise<CommsPage>;
		/** The next page. `cursor` carries both positions: the default order keysets on `before`;
		 *  any chosen order walks by `offset` instead, since a keyset position is only valid in one order. */
		onLoadMore?: (
			cursor: { before: number; offset: number },
			filters: CommsFilters,
			sort: DataTableSort | null
		) => Promise<CommsPage>;
		onRefresh?: () => void;
		/**
		 * Filters to OPEN with — an inbound deep link, not a two-way binding. Only the keys given are
		 * set; the rest stay empty. The event registry's 30-day count links here narrowed to one
		 * event, and without this it landed on an unfiltered feed: a count leading nowhere.
		 */
		initialFilters?: Partial<CommsFilters>;
	} = $props();

	let held = $state<CommsMessage[] | null>(null);
	let moreHeld = $state<boolean | null>(null);
	/**
	 * The filters in force, seeded from `initialFilters`.
	 *
	 * `untrack`: a capture, not a subscription. The controls own the filters from the first render
	 * on, so re-reading the prop would discard whatever the reader had changed every time a page
	 * came back.
	 */
	let filters = $state<CommsFilters>(
		untrack(() => ({ ...EMPTY_COMMS_FILTERS, ...(initialFilters ?? {}) }))
	);
	let busy = $state(false);

	// `held` is null until a client-side filter or load-more; SSR (and the first paint) therefore
	// reads the loader's `messages` directly. `$effect` does not run on the server, so seeding rows
	// only there used to SSR as "No email has been sent yet."
	const rows = $derived(held ?? messages);
	const more = $derived(moreHeld ?? hasMore);

	$effect(() => {
		held = messages;
		moreHeld = hasMore;
	});

	/**
	 * Whether ANY filter is in force. Every field of `CommsFilters` is read — `eventType` and
	 * `until` were missing, and both can be set without the reader having touched a control:
	 * `eventType` arrives from the registry's deep link, `until` from the date range. The empty
	 * state then said "No email has been sent yet" to a firm that had sent plenty and merely none
	 * of the filtered event, and the toolbar offered no way to see or clear it.
	 */
	const dirty = $derived(
		filters.status.length > 0 ||
			filters.templateCode.length > 0 ||
			filters.category.length > 0 ||
			filters.eventType.length > 0 ||
			filters.search.trim() !== '' ||
			filters.since !== '' ||
			filters.until !== ''
	);

	/** Options from the endpoint's facets when it gave any, else from the rows in hand — so the
	 *  dropdowns are never empty on a surface that has data. */
	function optionsFor(
		facetValues: string[] | undefined,
		fromRows: (m: CommsMessage) => string | null,
		label: (value: string) => string
	) {
		const values = facetValues?.length
			? facetValues
			: [...new Set(rows.map(fromRows).filter((v): v is string => !!v))].sort();
		return values.map((value) => ({ value, label: label(value) }));
	}

	const statusOptions = $derived(
		optionsFor(
			facets?.statuses,
			(m) => m.status,
			(v) => statusMeta(v).label
		)
	);
	const templateOptions = $derived(
		optionsFor(facets?.template_codes, (m) => m.template_code, templateLabel)
	);
	const categoryOptions = $derived(optionsFor(facets?.categories, (m) => m.category, humanise));

	// Derived outside the toolbar snippet: that snippet is named `filters`, so reading `filters.status`
	// inside it is the snippet itself (undefined on SSR) — which 500'd Activity as
	// `Cannot read properties of undefined (reading '0')`.
	const filterDefs = $derived<FilterDef[]>([
		{ key: 'status', label: 'Status', options: statusOptions },
		{
			key: 'templateCode',
			label: 'Template',
			options: templateOptions,
			searchPlaceholder: 'Search templates…'
		},
		...(categoryOptions.length
			? [{ key: 'category', label: 'Category', options: categoryOptions }]
			: []),
		{ key: 'date', label: 'Send date', options: [], kind: 'daterange' }
	]);

	const barValue = $derived<Record<string, string[]>>({
		...(filters.status.length ? { status: filters.status } : {}),
		...(filters.templateCode.length ? { templateCode: filters.templateCode } : {}),
		...(filters.category.length ? { category: filters.category } : {}),
		...(filters.since || filters.until ? { date: [filters.since, filters.until] } : {})
	});
	const canOpen = $derived(!!onOpen || !!messageHref);

	const columns = $derived.by(() => {
		const cols: DataTableColumn<CommsMessage>[] = [
			// Envelope lives IN this cell, not a neighbour with a blank header — that empty first
			// column was the gap to the left of "To". `shrink-0` on the icon keeps `max-w-0` (which
			// is what lets the address truncate under `table-fixed`) from crushing it.
			{
				key: 'to',
				header: 'To',
				sortable: true,
				headerClass: 'w-[24%]',
				cellClass: 'max-w-0'
			},
			// FROM, because on this platform the sender is not a constant: `resolve_sender` sends as
			// the firm, or as the ADVISER for a manual send, or from the verified domain wearing the
			// firm's display name when their own address is not on it. A feed showing only the
			// recipient cannot answer "who did this appear to come from" without opening every row.
			// Hidden below `lg`, where the columns that identify the message matter more.
			{
				key: 'from',
				header: 'From',
				sortable: true,
				headerClass: 'w-[20%]',
				cellClass: 'max-w-0',
				hideBelowWidth: 'lg'
			},
			{
				key: 'status',
				header: 'Status',
				sortable: true,
				headerClass: 'w-[8.5rem]'
			},
			{ key: 'subject', header: 'Subject', sortable: true, cellClass: 'max-w-0' },
			{
				key: 'sent',
				header: 'Sent',
				sortable: true,
				defaultDir: 'desc',
				sortDefault: true,
				headerClass: 'w-[6.5rem]'
			}
		];
		if (canOpen) cols.push({ key: 'actions', header: '', align: 'right', headerClass: 'w-10' });
		return cols;
	});

	const DEFAULT_SORT = { key: 'sent', dir: 'desc' } as const;
	let tableSort = $state<DataTableSort | null>({ ...DEFAULT_SORT });

	/** Null while the table is in its default order, so the request doesn't trade the keyset for an offset page. */
	const wireSort = $derived(
		tableSort && (tableSort.key !== DEFAULT_SORT.key || tableSort.dir !== DEFAULT_SORT.dir)
			? tableSort
			: null
	);

	async function refetch(): Promise<void> {
		if (!onFilterApply) return;
		busy = true;
		try {
			const page = await onFilterApply(filters, wireSort);
			held = page.items;
			moreHeld = page.hasMore;
		} finally {
			busy = false;
		}
	}

	async function apply(next: Partial<CommsFilters>) {
		filters = { ...filters, ...next };
		await refetch();
	}

	/** A header click refetches rather than re-ordering the page in hand. */
	async function chooseSort(next: DataTableSort | null) {
		tableSort = next ?? { ...DEFAULT_SORT };
		await refetch();
	}

	function openMessage(uid: string) {
		if (onOpen) {
			onOpen(uid);
			return;
		}
		if (messageHref) window.location.assign(messageHref(uid));
	}

	async function loadMore() {
		const oldest = rows[rows.length - 1];
		if (!onLoadMore || !oldest) return;
		busy = true;
		try {
			const page = await onLoadMore({ before: oldest.id, offset: rows.length }, filters, wireSort);
			held = [...rows, ...page.items];
			moreHeld = page.hasMore;
		} finally {
			busy = false;
		}
	}
</script>

<!-- Three states, three treatments. The failed read said "Try again" beside no control to try
     again with, and wore the same neutral dashed frame as the two states that are not failures —
     which is what `empty-state.svelte`'s own header forbids: a read that fell over must not be
     legible as a ledger that is empty. -->
{#snippet feedFailed()}
	<!-- NOT `chromeless` alongside `tone="warning"`: chromeless suppresses the container
	     (`empty-state.svelte:79`) and the container is where the warning is painted, so the two
	     together render a failure that looks exactly like an absence — the distinction this state
	     exists to draw, discarded by the prop meant to tidy it. -->
	<EmptyState
		tone="warning"
		icon={TriangleAlertIcon}
		title="Couldn’t load the feed"
		description="Messages couldn’t be loaded. Try again."
		action={onRefresh ? retryFeed : undefined}
		compact
	/>
{/snippet}

{#snippet retryFeed()}
	<Button variant="outline" size="sm" disabled={busy} onclick={() => onRefresh?.()}>
		{busy ? 'Retrying…' : 'Try again'}
	</Button>
{/snippet}

{#snippet noMessageMatches()}
	<EmptyState
		icon={SearchXIcon}
		title="No messages match these filters"
		description="Try a wider send-date range or a different status, or clear the filters to see everything sent."
		action={clearMessageFilters}
		compact
		chromeless
	/>
{/snippet}

{#snippet clearMessageFilters()}
	<Button variant="outline" size="sm" onclick={() => void apply({ ...EMPTY_COMMS_FILTERS })}>
		Clear filters
	</Button>
{/snippet}

{#snippet noMessagesSent()}
	<EmptyState
		icon={MailIcon}
		title="No email sent yet"
		description="Every email sent to your clients, including invitations, task notices and document notices, is recorded here with whether it arrived."
		compact
		chromeless
	/>
{/snippet}

<div class="flex min-h-0 min-w-0 flex-1 flex-col gap-5">
	{#if sendingDisabled}
		<!-- Not an error banner: this is a legitimate state, and saying so is what stops a wall of
		     "Not sent" reading as an outage. The reason itself is deliberately not printed: it names
		     configuration, which is not something a firm can act on. -->
		<p class="bg-muted text-muted-foreground rounded-md border px-3 py-2 text-sm">
			Email sending is switched off. Messages below are recorded but were not delivered.
		</p>
	{/if}

	<!-- Toolbar and table share ONE glass card, the frame every other book listing uses (accounts,
	     transactions, clients). A bare table reads as a different feature bolted on rather than as
	     part of this platform. -->
	<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
		<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
			<ListingToolbar
				class="shrink-0"
				query={filters.search}
				placeholder="Search…"
				{dirty}
				refreshing={busy}
				onrefresh={onRefresh}
				onsearch={(q: string) => apply({ search: q })}
				{filterDefs}
				filterValue={barValue}
				onfilters={(next) =>
					void apply({
						status: next.status ?? [],
						templateCode: next.templateCode ?? [],
						category: next.category ?? [],
						since: next.date?.[0] ?? '',
						until: next.date?.[1] ?? ''
					})}
				onclear={() => void apply({ ...EMPTY_COMMS_FILTERS })}
			/>

			<DataTable
				{columns}
				{rows}
				scroll
				fixed
				getKey={(m) => m.uid}
				label="Sent email"
				sort={tableSort}
				onSort={onFilterApply ? chooseSort : undefined}
				onRowClick={onOpen ? (m) => onOpen(m.uid) : undefined}
				empty={loadError ? feedFailed : dirty ? noMessageMatches : noMessagesSent}
			>
				{#snippet cell(m, column)}
					{#if column.key === 'to'}
						{@const name = recipientLabel(m)}
						<div class="flex min-w-0 items-center gap-2.5">
							<span
								class="bg-muted text-muted-foreground flex size-9 shrink-0 items-center justify-center rounded-md"
								aria-hidden="true"
							>
								<MailIcon class="size-4" />
							</span>
							<!-- BOTH, when the recipient is a person we know: the name is who it went to and
							     the address is whether it went to the right place — "did the client get it"
							     needs the first, "is the address right" needs the second, and a name alone
							     cannot answer the second. It was address-only with the name hidden in a
							     `title`, which is invisible to anyone scanning and to touch entirely.
							     Where the two are the same (an external address), one line. -->
							<span
								class="block min-w-0"
								title={name !== m.to_address ? `${name} <${m.to_address}>` : m.to_address}
							>
								{#if name !== m.to_address}
									<!-- The named recipient is a client record when the API resolved one. The
									     address below it never links: an email address is not a record, and the
									     same person can be reached at more than one. -->
									{#if m.recipient_contact_uid && clientHref}
										<a
											href={clientHref(m.recipient_contact_uid)}
											class="block truncate {LINK_RECORD}"
										>
											{name}
										</a>
									{:else}
										<span class="block truncate">{name}</span>
									{/if}
									<span class="text-muted-foreground block truncate text-xs">{m.to_address}</span>
								{:else}
									<span class="block truncate">{m.to_address}</span>
								{/if}
							</span>
						</div>
					{:else if column.key === 'from'}
						{#if m.from_address}
							<span class="block truncate text-sm" title={m.from_address}>{m.from_address}</span>
						{:else}
							<!-- A row recorded before the wire request was kept. Said, rather than left blank:
							     an empty cell reads as "sent from nowhere". -->
							<span class="text-muted-foreground text-xs">Not recorded</span>
						{/if}
					{:else if column.key === 'status'}
						{@const meta = statusMeta(m.status)}
						<div class="flex flex-col items-start gap-0.5">
							<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
							{#if m.provider_message_id}
								<!-- RESEND'S id, on every row that has one — which is every row where something
								     was actually posted, `delayed` and `bounced` included. It is the value you
								     paste into their dashboard, and needing to open a message to get it made
								     cross-checking a run of failures far slower than it should be. Absent only
								     when nothing was posted (suppressed, unconfigured), where its absence is
								     itself the explanation. -->
								<span
									class="text-muted-foreground max-w-full truncate font-mono text-[10px]"
									title={m.provider_message_id}
								>
									{m.provider_message_id}
								</span>
							{/if}
						</div>
					{:else if column.key === 'subject'}
						{#if messageHref}
							<a
								class="text-foreground hover:text-foreground block min-w-0 truncate no-underline"
								href={messageHref(m.uid)}
							>
								{m.subject}
							</a>
						{:else}
							<span class="block min-w-0 truncate">{m.subject}</span>
						{/if}
					{:else if column.key === 'sent'}
						{@const sent = formatDateTime(m.created_at)}
						<!-- Relative, and it TICKS: `now()` is the shared notification clock, which re-syncs on
						     focus so a tab left open does not sit on a stale "2m ago". -->
						<span class="text-muted-foreground whitespace-nowrap" title={sent}>
							{relativeTime(m.created_at, now())}
						</span>
					{:else if column.key === 'actions' && (onOpen || messageHref)}
						<DropdownMenu.Root>
							<DropdownMenu.Trigger
								class={buttonVariants({ variant: 'ghost', size: 'icon' })}
								aria-label="Message actions"
							>
								<EllipsisIcon class="size-4" />
							</DropdownMenu.Trigger>
							<DropdownMenu.Content align="end" class="w-40">
								<DropdownMenu.Item onclick={() => openMessage(m.uid)}>
									<EyeIcon class="size-4" /> View
								</DropdownMenu.Item>
							</DropdownMenu.Content>
						</DropdownMenu.Root>
					{/if}
				{/snippet}
			</DataTable>

			{#if more && onLoadMore}
				<div class="shrink-0">
					<Button variant="ghost" class="w-full" disabled={busy} onclick={loadMore}>
						{busy ? 'Loading…' : 'Load more'}
					</Button>
				</div>
			{/if}
		</CardContent>
	</Card>
</div>
