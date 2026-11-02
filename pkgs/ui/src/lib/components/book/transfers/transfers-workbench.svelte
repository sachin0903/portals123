<script lang="ts">
	// The transfers workbench, on real rows from `GET /transfers`.
	//
	// What changed versus the prototype this replaces: the data is real (it was `seedTransfers()`
	// from a fixtures module), the stage rail is the verified saga rather than a per-route model
	// from a schema that never shipped, and the reconciliation panel compares the actual
	// expectation against the actual credit instead of invented "legs".
	//
	// The portal waterfall: rows in as typed props, mutations as injected callbacks, hrefs from
	// an injected builder. No callbacks ⇒ read-only, which is how the admin portal mounts it.
	//
	// The frame is the Contributions workbench's: a one-row stat strip, the queue at full width,
	// and the detail panel only while a row is picked. The queue's columns fold by the TABLE's own
	// width, with stand-in lines, so a narrow table loses columns instead of overlapping them.
	import { tick } from 'svelte';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import AdviserStack from '../../data/adviser-stack.svelte';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { DataTable, sortRows, visibleWhenHidden } from '../../ui/data-table/index.js';
	import type { DataTableColumn, DataTableSort } from '../../ui/data-table/index.js';
	import { Badge } from '../../ui/badge/index.js';
	import { Money } from '../../ui/money/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { formatDate } from '../../../dates.js';
	import {
		ALL,
		barValueFrom,
		type FilterDef,
		withBarValue,
		FilterSelect,
		ListingToolbar,
		Pagination,
		humanise,
		isFiltered,
		matches,
		optionsFrom,
		optionsFromFacet,
		search,
		type ListingQuery
	} from '../listing/index.js';
	import KpiStrip from './kpi-strip.svelte';
	import DetailPanel from './detail-panel.svelte';
	import { clipName } from '../../../text-shell.js';
	import { cn } from '../../../utils.js';
	import { panelSlide } from '../../../panel-motion.js';
	import {
		TRANSFER_STATUS_LABEL,
		ageInDays,
		ageLabel,
		basisLabel,
		TRANSFER_BASIS_OPTIONS,
		transferValue,
		type ApiTransfer,
		type ApiTransferStatus
	} from './api.js';
	import {
		ROUTE_LABEL,
		custodianFilterOptions,
		statusLabel as transferStatusLabel,
		statusTone,
		type StageSummary,
		type TransferStepId
	} from './stages.js';

	let {
		transfers,
		summary = null,
		busy = false,
		onstep,
		onnote,
		query = null,
		total = null,
		facets,
		custodians = [],
		onquery,
		pageHref,
		links
	}: {
		transfers: ApiTransfer[];
		/** Whole-book stage totals. Absent ⇒ the strip sums the rows it was given. */
		summary?: StageSummary | null;
		busy?: boolean;
		/** The footer action. Absent ⇒ the panel draws no buttons. */
		onstep?: (uid: string, step: TransferStepId) => void;
		/** Save an adviser note. Absent ⇒ the note is read-only. */
		onnote?: (uid: string, note: string) => void;
		/** SERVER-DRIVEN when these are supplied: `transfers` is one page, the search and filters
		 *  are applied by the API, and this component only ASKS for changes via `onquery`. The app
		 *  owns the navigation, so a filtered view lives in the URL and survives a reload.
		 *
		 *  Absent ⇒ the original local mode, still right for a complete list. */
		query?: ListingQuery | null;
		total?: number | null;
		facets?: Record<string, string[]>;
		/** Custodians this firm has switched on. The filter is offered only when there are two. */
		custodians?: { code: string; name: string }[];
		onquery?: (next: ListingQuery) => void;
		/** Builds the URL for a 1-indexed page, so the pager renders real links (shareable,
		 *  middle-clickable, back-button friendly). Injected because only the app knows its own
		 *  route and which unrelated params must survive — the same reason `links` is injected. */
		pageHref?: (page: number) => string;
		/** Routes to the two records a transfer row NAMES. Injected, never built here: only the app
		 *  knows its own routes, and the header comment's waterfall is the rule.
		 *
		 *  BOTH are optional, unlike the contributions queue where `client` is required — this
		 *  workbench is also mounted read-only by the admin portal, which has neither route in that
		 *  context, and there both names stay exactly the plain text they are today. */
		links?: {
			/** A client's record. Takes the contact UID: `contact_id` on the row is a serial, and the
			 *  client record resolves by uid. */
			client?: (uid: string) => string;
			account?: (uid: string) => string;
		};
	} = $props();

	const server = $derived(!!query && !!onquery);

	// There is deliberately no "New transfer" entry point here. A transfer is raised from the
	// account it is transferring INTO — that account is what determines the custodian, the base
	// currency and the wrapper, so starting from the workbench means asking for three things
	// that are already known and can be got wrong.

	let selectedUid = $state<string | null>(null);
	const selected = $derived(
		selectedUid === null ? null : (transfers.find((t) => t.uid === selectedUid) ?? null)
	);

	let root = $state<HTMLElement | null>(null);

	function close(): void {
		selectedUid = null;
	}

	// Opening the panel narrows the queue and folds its columns, so the row that was clicked can
	// reflow out of the scrolled window. Bring it back once layout has settled.
	$effect(() => {
		if (!selectedUid) return;
		void tick().then(() =>
			requestAnimationFrame(() =>
				root?.querySelector('tr.transfer-selected')?.scrollIntoView({ block: 'nearest' })
			)
		);
	});

	/** Esc closes the panel, unless it belongs to something else first: a popover, menu or dialog
	 *  layered on top, or a field being typed in (the note composer lives in the panel, and losing
	 *  a half-typed note to a stray Esc would lose what was typed). */
	function onkeydown(event: KeyboardEvent): void {
		if (event.key !== 'Escape' || !selectedUid || event.defaultPrevented) return;
		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
		if (
			document.querySelector(
				'[role="dialog"][data-state="open"], [role="menu"][data-state="open"], [role="listbox"]'
			)
		) {
			return;
		}
		close();
	}

	let localQuery = $state('');
	// NULL — the table's own left column (Raised, descending), as everywhere else.
	let localSort = $state<DataTableSort | null>(null);
	/** In SERVER mode the sort mirrors the query — the API ordered, so the rows arrive ready. */
	const sort = $derived<DataTableSort | null>(
		server && query ? (query.sort ? { key: query.sort, dir: query.dir } : null) : localSort
	);

	/** Hand a column click to the SERVER: ordering the page in hand reorders what is on screen and
	 *  calls it "oldest first", while the transfer that has been waiting longest may be pages away. */
	function chooseSort(next: DataTableSort | null): void {
		if (!server || !query || !onquery) {
			localSort = next;
			return;
		}
		onquery({ ...query, sort: next?.key ?? null, dir: next?.dir ?? 'asc', offset: 0 });
	}

	/** Search text: the server's query when server-driven, local state otherwise. */
	const term = $derived(query ? query.q : localQuery);

	// Options come from the server's facets when server-driven. Derived from the rows on screen
	// they could only ever offer values already visible, so paging would change what you are
	// allowed to filter by.
	const statusLabel = (status: string) =>
		TRANSFER_STATUS_LABEL[status as ApiTransferStatus] ?? humanise(status);
	const routeLabel = (route: string) => ROUTE_LABEL[route] ?? humanise(route);
	const typeLabel = (basis: string) =>
		TRANSFER_BASIS_OPTIONS.find((option) => option.value === basis)?.label ?? humanise(basis);

	const statusOptions = $derived(
		server
			? optionsFromFacet(facets, 'status', statusLabel)
			: optionsFrom(transfers, (t) => t.status, statusLabel)
	);
	const adviserOptions = $derived(
		server ? optionsFromFacet(facets, 'adviser') : optionsFrom(transfers, (t) => t.adviser ?? '')
	);
	const typeOptions = $derived(
		server ? optionsFromFacet(facets, 'basis', typeLabel) : optionsFrom(transfers, basisLabel)
	);
	const routeOptions = $derived(
		server
			? optionsFromFacet(facets, 'route', routeLabel)
			: optionsFrom(transfers, (t) => t.route ?? '', routeLabel)
	);
	const custodianOptions = $derived(custodianFilterOptions(custodians));

	// Local-mode filter state. Unused when server-driven.
	let statusFilter = $state(ALL);
	let adviserFilter = $state(ALL);
	let typeFilter = $state(ALL);
	let routeFilter = $state(ALL);
	let custodianFilter = $state(ALL);

	const BAR_KEYS = $derived(
		custodianOptions
			? (['status', 'route', 'basis', 'adviser', 'custodian'] as const)
			: (['status', 'route', 'basis', 'adviser'] as const)
	);

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'status', label: 'Stage', options: statusOptions },
		{ key: 'route', label: 'Route', options: routeOptions },
		{ key: 'basis', label: 'Type', options: typeOptions },
		{
			key: 'adviser',
			label: 'Adviser',
			options: adviserOptions,
			searchPlaceholder: 'Search advisers…'
		},
		...(custodianOptions
			? [{ key: 'custodian', label: 'Custodian', options: custodianOptions }]
			: [])
	]);

	const dirty = $derived(
		query
			? isFiltered(query)
			: statusFilter !== ALL ||
					adviserFilter !== ALL ||
					typeFilter !== ALL ||
					routeFilter !== ALL ||
					custodianFilter !== ALL
	);

	function clear() {
		if (query && onquery) {
			onquery({ ...query, q: '', filters: {}, offset: 0 });
			return;
		}
		statusFilter = ALL;
		adviserFilter = ALL;
		typeFilter = ALL;
		routeFilter = ALL;
		custodianFilter = ALL;
	}

	const rows = $derived(
		server
			? transfers
			: transfers.filter(
					(t) =>
						matches(statusFilter, t.status) &&
						matches(adviserFilter, t.adviser ?? '') &&
						matches(typeFilter, basisLabel(t)) &&
						matches(routeFilter, t.route ?? '') &&
						matches(custodianFilter, t.custodian ?? '') &&
						search(
							localQuery,
							t.client_name ?? '',
							t.adviser ?? '',
							t.ceding_provider,
							t.policy_number ?? '',
							t.account_name ?? '',
							t.uid
						)
				)
	);

	/** The secondary line under Client is a transfer-side reference, never the client's id. Name
	 *  whichever identifier won the fallback so a policy number cannot be mistaken for a person. */
	const referenceFor = (transfer: ApiTransfer): { label: string; value: string } =>
		transfer.policy_number
			? { label: 'Policy', value: transfer.policy_number }
			: transfer.transaction_reference
				? { label: 'Transaction', value: transfer.transaction_reference }
				: { label: 'Transfer', value: transfer.uid };

	// THE TABLE FOLDS BY ITS OWN WIDTH, not the viewport's. It is full width until a row is picked,
	// then the panel pushes in and takes ~400px, so one screen can hold a 1100px table or a 700px
	// one. The content-sized columns (date, adviser, type, value, age, status) have fixed widths;
	// Client, Account and Ceding provider share what is left. Three rungs:
	//   below 64rem (@5xl): Adviser and Age step out; the age goes under the Raised date.
	//   below 56rem (@4xl): Account folds under the client (in place of the reference), Type
	//                       folds under the value.
	//   below 42rem (@2xl): Ceding provider folds under the status.
	// Raised never folds: it is the first cell, which carries the selected row's leading rule.
	const columns: DataTableColumn<ApiTransfer>[] = [
		{
			key: 'created',
			header: 'Raised',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (t) => new Date(t.created_at),
			headerStyle: 'width: 6.5rem'
		},
		{
			key: 'client',
			header: 'Client',
			sortable: true,
			sortValue: (t) => t.client_name
		},
		{
			key: 'account',
			header: 'Account',
			sortable: true,
			sortValue: (t) => t.account_name,
			hideBelowWidth: '4xl'
		},
		{
			key: 'adviser',
			header: 'Adviser',
			sortable: true,
			hideBelowWidth: '5xl',
			headerStyle: 'width: 7rem'
		},
		{
			key: 'type',
			header: 'Type',
			sortable: true,
			sortValue: basisLabel,
			hideBelowWidth: '4xl',
			headerStyle: 'width: 5.75rem'
		},
		{
			key: 'provider',
			header: 'Ceding provider',
			sortable: true,
			sortValue: (t) => t.ceding_provider,
			hideBelowWidth: '2xl'
		},
		{
			key: 'value',
			header: 'Value',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: transferValue,
			headerStyle: 'width: 7rem'
		},
		{
			key: 'age',
			header: 'Age',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			// Sort on days, not the label — otherwise "2w" collates before "3d".
			sortValue: (t) => ageInDays(t),
			hideBelowWidth: '5xl',
			headerStyle: 'width: 3.5rem'
		},
		// Local mode only: a live listing is ordered by the API (`sort=status` uses the label).
		// 9.25rem holds the longest stage word ("At ceding scheme") in its badge with the cell's own
		// padding; a longer one cuts off inside the badge rather than widening the column.
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			sortValue: (t) => TRANSFER_STATUS_LABEL[t.status] ?? t.status,
			headerStyle: 'width: 9.25rem'
		}
	];

	/** The classes that show a stand-in exactly while its column is folded away. */
	const colOf = (key: string) => columns.find((c) => c.key === key);
	const ageFolded = visibleWhenHidden(colOf('age'));
	const accountFolded = visibleWhenHidden(colOf('account'));
	const typeFolded = visibleWhenHidden(colOf('type'));
	const providerFolded = visibleWhenHidden(colOf('provider'));

	/** "3w ago", for the line under the date once the Age column has stepped out. */
	const ageAgo = (transfer: ApiTransfer) => {
		const label = ageLabel(transfer);
		return label === 'today' || label === '—' ? label : `${label} ago`;
	};

	// Server mode: ordered by the API. Local mode: ordered here, because passing `onSort` puts
	// `DataTable` in server-sorted mode and it no longer orders the rows itself.
	const displayRows = $derived(server ? rows : sortRows(rows, columns, localSort));
</script>

<svelte:window {onkeydown} />

<!-- ONE SCREEN, the same frame the book listings use: the LIST is the only thing that scrolls, so
     the saga strip, the toolbar and the pager all stay put. The topbar crumb names the page. -->
<div
	bind:this={root}
	class="-m-4 flex min-w-0 flex-col gap-4 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<!-- The strip and the toolbar render even with nothing in the book. An empty workbench that
	     shows only a sentence looks like it failed to load; a strip of zeros says "this surface is
	     working, there is no work" — and it is where the counts appear the moment a transfer is
	     raised. The empty message belongs in the table, which is where the rows would be. -->
	<!-- The sentence moved INTO the table (see its `empty`), because this Alert and the table's own
	     message were two answers to one question and the lower one blamed a filter nobody had set. -->

	<div class="shrink-0">
		<KpiStrip rows={transfers} {summary} />
	</div>

	{#snippet clearFilters()}
		<Button variant="outline" size="sm" onclick={clear}>Clear filters</Button>
	{/snippet}

	{#snippet noMatches()}
		<EmptyState
			compact
			title="No transfers match your filters"
			description="Clear them to see the whole book."
			action={clearFilters}
		/>
	{/snippet}

	{#snippet noTransfers()}
		<EmptyState
			compact
			title="No transfers yet"
			description="Open the account it is transferring into and choose Transfer in."
		/>
	{/snippet}

	<!-- THE PANEL OPENS ON DEMAND. It used to hold a fixed 420px column whether or not anything was
	     selected, so with nothing picked a third of a laptop screen said "Select a transfer", and the
	     queue beside it printed its badges over the next column. Now the queue is full width until a
	     row is picked; picking one PUSHES the panel in beside it. Push, not a drawer: a drawer covers
	     the queue, and an adviser working down a list of transfers loses their place on every one.
	     The X and Esc put it back. Below `xl` there is no room for both, so the panel takes the
	     queue's place until it is closed; the queue stays mounted under it, so its scroll position
	     and filters survive. -->
	<div class="flex min-h-0 flex-1 gap-4 overflow-hidden">
		<div
			class={cn(
				'flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden',
				selected && 'max-xl:hidden'
			)}
		>
			<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
				<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
					<ListingToolbar
						class="shrink-0"
						query={term}
						placeholder="Search client, adviser, ceding provider, policy or reference"
						{dirty}
						onclear={clear}
						onsearch={query && onquery
							? (next) => onquery({ ...query, q: next, offset: 0 })
							: undefined}
						{filterDefs}
						filterValue={barValueFrom(query, BAR_KEYS)}
						onfilters={(next) => query && onquery && onquery(withBarValue(query, BAR_KEYS, next))}
					>
						{#snippet filters()}
							{#if !server}
								<FilterSelect
									bind:value={statusFilter}
									options={statusOptions}
									allLabel="All active statuses"
									label="Filter by status"
								/>
								<FilterSelect
									bind:value={adviserFilter}
									options={adviserOptions}
									allLabel="All advisers"
									label="Filter by adviser"
								/>
								<FilterSelect
									bind:value={typeFilter}
									options={typeOptions}
									allLabel="All types"
									label="Filter by type"
								/>
								<FilterSelect
									bind:value={routeFilter}
									options={routeOptions}
									allLabel="All routes"
									label="Filter by route"
								/>
								{#if custodianOptions}
									<FilterSelect
										bind:value={custodianFilter}
										options={custodianOptions}
										allLabel="All custodians"
										label="Filter by custodian"
									/>
								{/if}
							{/if}
						{/snippet}
					</ListingToolbar>

					<!-- WHICH NOTHING, and the way out: this said "No transfers match your filters."
					     unconditionally, beneath the workbench's own "No transfers yet" Alert. That Alert's
					     guidance is in the empty state now, where the rows would have been. -->
					<DataTable
						{columns}
						rows={displayRows}
						{sort}
						onSort={chooseSort}
						scroll
						fixed
						getKey={(t) => t.uid}
						empty={dirty ? noMatches : noTransfers}
						onRowClick={(t) => (selectedUid = t.uid)}
						rowClass={(t) =>
							// The tint, plus the sidebar's active rule down the leading edge, so the selected
							// row still reads as THE row after the panel opens and the table reflows around it.
							// `transfer-selected` carries no style: it is how the workbench finds the row to
							// scroll back into view.
							t.uid === selectedUid
								? 'transfer-selected bg-accent/60 [&>td:first-child]:shadow-[inset_2px_0_0_0_var(--primary)]'
								: undefined}
					>
						{#snippet cell(transfer, column)}
							{#if column.key === 'created'}
								<span class="whitespace-nowrap tabular-nums">{formatDate(transfer.created_at)}</span
								>
								{#if ageFolded}
									<!-- Age folded away: how long it has been waiting, under the day it was raised. -->
									<span class="text-muted-foreground text-[10.5px] tabular-nums {ageFolded}"
										>{ageAgo(transfer)}</span
									>
								{/if}
							{:else if column.key === 'client'}
								{@const reference = referenceFor(transfer)}
								{@const client = clipName(transfer.client_name ?? 'Unknown client')}
								{@const account = clipName(transfer.account_name ?? '—')}
								<span class="flex w-full min-w-0 flex-col items-start">
									<!-- A transfer row names two records, and "who is this?" used to mean leaving the
									     workbench. Linked only when the mount injected the route AND the row carries the
									     uid: a detected feed row has a client name but no contact join behind it, and a
									     link that 404s is worse than a name. The reference is `w-full` so a long
									     transaction id truncates inside the column instead of spilling into Account. -->
									<TruncatedText
										text={client.text}
										full={client.full}
										href={links?.client && transfer.contact_uid
											? links.client(transfer.contact_uid)
											: undefined}
										class="w-fit max-w-full font-medium"
									/>
									{#if accountFolded}
										<!-- While the Account column is folded away the account takes this line, and the
										     reference gives way to it: the table is narrow because the panel is open, and
										     the panel shows the reference. -->
										<TruncatedText
											text={account.text}
											full={account.full}
											href={links?.account && transfer.account_uid && transfer.account_name
												? links.account(transfer.account_uid)
												: undefined}
											class="text-muted-foreground w-fit max-w-full text-[10.5px] {accountFolded}"
										/>
										<span class="hidden w-full min-w-0 @4xl:block">
											<RecordId label={reference.label} value={reference.value} class="w-full" />
										</span>
									{:else}
										<RecordId label={reference.label} value={reference.value} class="w-full" />
									{/if}
								</span>
							{:else if column.key === 'adviser'}
								<AdviserStack names={[transfer.adviser]} />
							{:else if column.key === 'type'}
								{@const basis = basisLabel(transfer)}
								{#if basis !== '—'}
									<!-- Capped at the column, as on Accounts: a long word cuts off with the whole of it on hover, rather
									     than printing over the next column (FB-20260921-09). -->
									<Badge variant="outline" class="max-w-full min-w-0 font-normal"
										><TruncatedText text={basis} /></Badge
									>
								{:else}
									<span class="text-muted-foreground">—</span>
								{/if}
							{:else if column.key === 'account'}
								<!-- The dash is never linked: an account with no name is nothing to click, and an
								     anchor around "—" is a target you cannot read. -->
								{@const account = clipName(transfer.account_name ?? '—')}
								<TruncatedText
									text={account.text}
									full={account.full}
									href={links?.account && transfer.account_uid && transfer.account_name
										? links.account(transfer.account_uid)
										: undefined}
									class="text-muted-foreground w-fit max-w-full"
								/>
							{:else if column.key === 'provider'}
								{@const provider = clipName(transfer.ceding_provider)}
								<TruncatedText text={provider.text} full={provider.full} class="w-fit max-w-full" />
							{:else if column.key === 'value'}
								{#if transferValue(transfer) === null}
									<!-- A transfer can be raised before the ceding scheme says what it is worth.
									     Rendering £0.00 would read as a nil transfer rather than an unknown one. -->
									<span class="text-muted-foreground block truncate">Not yet advised</span>
								{:else}
									<Money amount={transferValue(transfer) ?? 0} class="font-medium" />
								{/if}
								{#if typeFolded && basisLabel(transfer) !== '—'}
									<!-- Type folded away: Cash or In-specie under the figure it qualifies. -->
									<span class="text-muted-foreground text-[10.5px] {typeFolded}"
										>{basisLabel(transfer)}</span
									>
								{/if}
							{:else if column.key === 'age'}
								<span class="whitespace-nowrap tabular-nums">{ageLabel(transfer)}</span>
							{:else if column.key === 'status'}
								<span class="flex w-full min-w-0 flex-col items-start gap-0.5">
									<StatusBadge tone={statusTone(transfer)} class="max-w-full min-w-0"
										><TruncatedText
											text={transferStatusLabel(transfer)}
											class="min-w-0"
										/></StatusBadge
									>
									{#if providerFolded}
										{@const provider = clipName(transfer.ceding_provider)}
										<!-- Ceding provider folded away: who the transfer is coming from, under where it is. -->
										<TruncatedText
											text={`from ${provider.text}`}
											full={provider.full ? `from ${provider.full}` : undefined}
											class="text-muted-foreground w-fit max-w-full text-[10.5px] {providerFolded}"
										/>
									{/if}
								</span>
							{/if}
						{/snippet}
					</DataTable>

					{#if query && onquery && total !== null}
						<div class="shrink-0">
							<Pagination {query} {total} shown={rows.length} href={pageHref} />
						</div>
					{/if}
				</CardContent>
			</Card>
		</div>
		{#if selected}
			<!-- A flex sibling of a fixed width, so the queue gives up exactly this much and keeps every
			     row it had on screen. The panel scrolls inside itself, so a long detail cannot push the
			     queue off-screen. It slides in from the edge it sits on (`panel-motion.ts`); picking
			     another row brings in only its contents (`motion-item-enter`). -->
			<aside
				aria-label="Selected transfer"
				class="flex min-h-0 min-w-0 flex-1 flex-col xl:w-[380px] xl:flex-none 2xl:w-[420px]"
				in:panelSlide
				out:panelSlide
			>
				{#key selected.uid}
					<div class="motion-item-enter flex min-h-0 flex-1 flex-col">
						<DetailPanel transfer={selected} {busy} {links} {onstep} {onnote} onclose={close} />
					</div>
				{/key}
			</aside>
		{/if}
	</div>
</div>
