<script module lang="ts">
	import type { FilterDef } from '@graphene/ui/book/listing';

	/** DATE used to be the standalone `DateRangeFilter` in the toolbar row, which left it off the
	 *  funnel's count, off the applied bar and untouched by Reset. */
	export function auditLogFilterDefs(input: {
		showFirm: boolean;
		firmOptions: FilterDef['options'];
		actorOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			...(input.showFirm
				? [
						{
							key: 'tenant',
							label: 'Firm',
							options: input.firmOptions,
							allLabel: 'All firms'
						} satisfies FilterDef
					]
				: []),
			{
				key: 'actor',
				label: 'Actor',
				options: input.actorOptions,
				allLabel: 'All actors'
			},
			{ key: 'date', label: 'Date', options: [], kind: 'daterange' }
		];
	}
</script>

<script lang="ts">
	// The DML record-change trail (audit.log) as a master-detail surface — the same treatment as the
	// Events stream (a scanning table of rows on the left, the full before/after evidence on the right),
	// but a DML-native row + detail, because audit.log rows have a different shape than audit.actions
	// (no event_type/status/route/entities — just action `schema.table.op` + a changed/row `detail`).
	// Selection is client-side; "Load more" appends the next keyset page in place. The page owns
	// navigation + data loading; this component owns the layout, toolbar and debounced filter apply.
	import { untrack } from 'svelte';
	import ScrollTextIcon from '@lucide/svelte/icons/scroll-text';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import {
		actorSuggestions,
		Button,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		EmptyState
	} from '@graphene/ui';
	import { ListingToolbar } from '@graphene/ui/book/listing';
	import AuditLogDetail from './audit-log-detail.svelte';
	import type { AuditEvent, AuditFilters, AuditTenant } from './types';

	let {
		events,
		showFirm = true,
		tenants = [],
		filters,
		hasMore = false,
		onFiltersChange,
		onLoadMore
	}: {
		events: AuditEvent[];
		/** False on the per-tenant view — every row names the same firm, so the column/filter is dropped. */
		showFirm?: boolean;
		tenants?: AuditTenant[];
		filters: AuditFilters;
		hasMore?: boolean;
		onFiltersChange: (filters: AuditFilters) => void;
		/** Fetch the page of rows before `beforeId` (keyset on id). Returns them + whether more remain,
		 *  or null on failure. The component appends them in place — no navigation, no URL change. */
		onLoadMore?: (beforeId: number) => Promise<{ events: AuditEvent[]; hasMore: boolean } | null>;
	} = $props();

	// Client-side selection + in-place accumulation, identical to EventStream so the two surfaces
	// behave the same. `active` is the selected row or the newest (so the pane is never empty).
	let selectedId = $state<number | null>(null);
	let appended = $state<AuditEvent[]>([]);
	let more = $state(untrack(() => hasMore));
	let loadingMore = $state(false);
	let seenPage = untrack(() => events);
	$effect(() => {
		if (events !== seenPage) {
			seenPage = events;
			untrack(() => {
				appended = [];
				more = hasMore;
			});
		}
	});
	const allEvents = $derived([...events, ...appended]);
	// Actor options for the Firm-style FilterSelect dropdown — the same shared derivation the
	// action-stream EventStream uses (distinct loaded actors; FilterSelect adds its own "All actors").
	const actorOptions = $derived(actorSuggestions(allEvents));
	const MAX_EVENTS = 1000;
	const atCap = $derived(allEvents.length >= MAX_EVENTS);
	const active = $derived(allEvents.find((e) => e.id === selectedId) ?? allEvents[0] ?? null);

	// Compact, locale-stable timestamp (the API returns ISO strings).
	function when(iso: string): string {
		const d = new Date(iso);
		return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('en-GB');
	}
	function actorLabel(event: AuditEvent): string {
		return event.actor_email ?? 'System';
	}

	// The heading row's shared look, applied per column (DataTable has no row-level header class):
	// the same muted band + compact caps the Events stream's table carries.
	const HEAD_CLASS = 'bg-muted/40 h-9 text-[11px] tracking-wide';
	// Every heading but the leading glyph is clickable. Actor is DERIVED (the fallback to "System"
	// when there is no email), so it needs its own `sortValue`; Action, Firm and When are plain
	// fields the cell only reformats for display, so a bare field read already matches what's shown.
	// `Firm` is included only `showFirm` — the per-tenant view already names one firm on every row.
	const auditColumns = $derived<DataTableColumn<AuditEvent>[]>([
		{ key: 'category', header: '', headerClass: `${HEAD_CLASS} w-9`, cellClass: 'relative' },
		{
			key: 'actor',
			header: 'Actor',
			sortable: true,
			headerClass: `${HEAD_CLASS} w-[11rem]`,
			cellClass: 'truncate text-sm',
			sortValue: actorLabel
		},
		{
			key: 'action',
			header: 'Action',
			sortable: true,
			headerClass: HEAD_CLASS,
			cellClass: 'min-w-0'
		},
		...(showFirm
			? [
					{
						key: 'tenant_name',
						header: 'Firm',
						sortable: true,
						headerClass: `${HEAD_CLASS} w-[9rem]`,
						cellClass: 'truncate text-sm'
					} satisfies DataTableColumn<AuditEvent>
				]
			: []),
		{
			key: 'created_at',
			header: 'When',
			sortable: true,
			defaultDir: 'desc',
			headerClass: `${HEAD_CLASS} w-[11rem]`,
			cellClass: 'text-muted-foreground whitespace-nowrap'
		}
	]);
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in the
	// template is recreated on every update, stamping the reader's chosen column back to the default.
	let auditSort = $state<DataTableSort | null>(null);

	async function loadMore() {
		const beforeId = allEvents.at(-1)?.id;
		if (beforeId == null || loadingMore) return;
		loadingMore = true;
		try {
			const page = await onLoadMore?.(beforeId);
			if (page) {
				appended = [...appended, ...page.events];
				more = page.hasMore;
			}
		} finally {
			loadingMore = false;
		}
	}

	// Toolbar control state, seeded once (untrack: props are reactive, re-seeding would fight the
	// operator mid-edit). The main query box drives the `action` filter (`schema.table.op`).
	let query = $state(untrack(() => filters.action));
	let actor = $state<string[]>(untrack(() => filters.actor));
	let firm = $state<string[]>(untrack(() => filters.tenantId));
	let since = $state(untrack(() => filters.since));
	let until = $state(untrack(() => filters.until));
	const firmOptions = $derived(tenants.map((t) => ({ value: String(t.id), label: t.name })));

	function sameList(a: readonly string[], b: readonly string[]): boolean {
		return a.length === b.length && a.every((v, i) => v === b[i]);
	}

	function snapshot(): AuditFilters {
		return {
			action: query.trim(),
			actor,
			tenantId: firm,
			since,
			until
		};
	}
	function same(a: AuditFilters, b: AuditFilters): boolean {
		return (
			a.action === b.action &&
			sameList(a.actor, b.actor) &&
			sameList(a.tenantId, b.tenantId) &&
			a.since === b.since &&
			a.until === b.until
		);
	}
	const EMPTY: AuditFilters = { action: '', actor: [], tenantId: [], since: '', until: '' };
	const dirty = $derived(!same(snapshot(), EMPTY));

	const filterDefs = $derived(auditLogFilterDefs({ showFirm, firmOptions, actorOptions }));

	const barValue = $derived<Record<string, string[]>>({
		...(showFirm && firm.length ? { tenant: firm } : {}),
		...(actor.length ? { actor } : {}),
		...(since || until ? { date: [since, until] } : {})
	});

	/** One writer, because Save/Reset can change several filters at once. */
	function setFilters(next: Record<string, string[]>): void {
		firm = next.tenant ?? [];
		actor = next.actor ?? [];
		since = next.date?.[0] ?? '';
		until = next.date?.[1] ?? '';
	}

	// Live application, debounced: when the local state drifts from the APPLIED filters (the prop),
	// tell the page. The prop read makes this self-settling.
	let timer: ReturnType<typeof setTimeout> | undefined;
	$effect(() => {
		const next = snapshot();
		if (same(next, filters)) return;
		clearTimeout(timer);
		timer = setTimeout(() => onFiltersChange(next), 350);
		return () => clearTimeout(timer);
	});

	function clearAll() {
		query = '';
		actor = [];
		firm = [];
		since = '';
		until = '';
	}
</script>

{#snippet clearAuditFilters()}
	<Button variant="outline" size="sm" onclick={clearAll}>Clear filters</Button>
{/snippet}

<!-- One card envelopes the toolbar (in a bordered header) + the master/detail below — the same
     frame the Events stream and the book listings use. -->
<div class="bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-xl border shadow-xs">
	<div class="border-b p-3">
		<ListingToolbar
			bind:query
			placeholder="Filter by action (auth.users.update)"
			{dirty}
			onclear={clearAll}
			{filterDefs}
			filterValue={barValue}
			onfilters={setFilters}
		/>
	</div>

	{#if allEvents.length === 0}
		<div class="flex-1 p-4">
			<EmptyState
				compact
				icon={ScrollTextIcon}
				title={dirty ? 'No changes match these filters' : 'No audit events yet'}
				description={dirty
					? 'Try a wider date range, or clear the filters to see the whole trail.'
					: 'Recorded changes will appear here.'}
				action={dirty ? clearAuditFilters : undefined}
			/>
		</div>
	{:else}
		<div class="flex min-w-0 flex-col md:min-h-0 md:flex-1 md:overflow-hidden lg:flex-row">
			<div class="flex min-w-0 flex-col md:min-h-0 md:flex-1 lg:w-[58%] lg:flex-none lg:border-r">
				<div class="overflow-x-auto md:min-h-0 md:flex-1 md:overflow-y-auto">
					<DataTable
						columns={auditColumns}
						rows={allEvents}
						bind:sort={auditSort}
						getKey={(e) => e.id}
						label="Audit log"
						fixed
						onRowClick={(e) => (selectedId = e.id)}
						rowClass={(e) => (active?.id === e.id ? 'bg-muted/60' : undefined)}
						empty="No changes match these filters."
					>
						{#snippet cell(e, column)}
							{#if column.key === 'category'}
								{#if active?.id === e.id}
									<span class="bg-primary absolute inset-y-0 left-0 w-0.5" aria-hidden="true"
									></span>
								{/if}
								<span
									class="bg-muted text-muted-foreground flex size-6 items-center justify-center rounded-full"
									aria-hidden="true"
								>
									<ScrollTextIcon class="size-3.5" />
								</span>
							{:else if column.key === 'actor'}
								{#if e.actor_email}
									<span class="truncate" title={e.actor_name ?? e.actor_email}>
										{actorLabel(e)}
									</span>
								{:else}
									<!-- A DML write from a background/worker process carries no principal; say so
									     rather than a dash, which reads as "unknown person". -->
									<span class="text-muted-foreground text-xs">System</span>
								{/if}
							{:else if column.key === 'action'}
								<code
									class="text-muted-foreground block truncate font-mono text-xs"
									title={e.action}
								>
									{e.action}
								</code>
							{:else if column.key === 'tenant_name'}
								<span class="truncate">{e.tenant_name ?? '—'}</span>
							{:else if column.key === 'created_at'}
								<span class="tabular-nums" title={e.created_at}>{when(e.created_at)}</span>
							{/if}
						{/snippet}
					</DataTable>
					<!-- Load-more / end-marker INSIDE the scroll, after the last row (the place the next
					     row would be) — reached by scrolling to the bottom, appended in place. -->
					{#if more && !atCap}
						<div class="flex justify-center border-t px-4 py-3">
							<Button variant="secondary" size="sm" disabled={loadingMore} onclick={loadMore}>
								{loadingMore ? 'Loading…' : 'Load more'}
								<ChevronDownIcon class="size-4" />
							</Button>
						</div>
					{:else if more && atCap}
						<div class="text-muted-foreground border-t px-4 py-3 text-center text-xs">
							Showing the most recent {MAX_EVENTS.toLocaleString()} changes. Narrow the filters to reach
							older ones.
						</div>
					{:else}
						<div
							class="text-muted-foreground flex items-center justify-center gap-3 border-t px-4 py-3 text-xs"
						>
							<span class="bg-border h-px w-8" aria-hidden="true"></span>
							End of the trail
							<span class="bg-border h-px w-8" aria-hidden="true"></span>
						</div>
					{/if}
				</div>
			</div>
			<div class="min-h-0 flex-1 overflow-y-auto border-t lg:border-t-0">
				{#if active}
					<AuditLogDetail event={active} />
				{/if}
			</div>
		</div>
	{/if}
</div>
