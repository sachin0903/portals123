<script module lang="ts">
	import type { FilterDef } from '@graphene/ui/book/listing';

	export function transferQueueFilterDefs(input: {
		routeOptions: FilterDef['options'];
		statusOptions: FilterDef['options'];
		wrapperOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			{ key: 'route', label: 'Route', options: input.routeOptions, allLabel: 'All routes' },
			{ key: 'status', label: 'Status', options: input.statusOptions },
			{ key: 'wrapper', label: 'Wrapper', options: input.wrapperOptions }
		];
	}
</script>

<script lang="ts">
	import {
		Button,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		cn,
		formatGBP
	} from '@graphene/ui';
	import { ListingToolbar, matches, optionsFrom } from '@graphene/ui/book/listing';
	import { ROUTE_LABEL, STATUS_LABEL } from '$lib/transfers/status';
	import type { TransfersStore } from '$lib/transfers/store.svelte';
	import type { Transfer, TransferRoute, TransferStatus } from '$lib/transfers/types';
	import StatusBadge from './StatusBadge.svelte';
	import RouteBadge from './RouteBadge.svelte';

	let { wb }: { wb: TransfersStore } = $props();

	const views: { key: 'needs' | 'all'; label: string }[] = [
		{ key: 'needs', label: 'Needs me' },
		{ key: 'all', label: 'All transfers' }
	];
	const custodianLabel = (t: { custodian: string }) => (t.custodian === 'seccl' ? 'Seccl' : 'SEI');

	// Options come from the whole book (wb.transfers), not the already-filtered queue, so picking
	// one filter never shrinks another's option list out from under the operator.
	const routeOptions = $derived(
		optionsFrom(
			wb.transfers,
			(t) => t.route,
			(r) => ROUTE_LABEL[r as TransferRoute]
		)
	);
	const statusOptions = $derived(
		optionsFrom(
			wb.transfers,
			(t) => t.status,
			(s) => STATUS_LABEL[s as TransferStatus]
		)
	);
	const wrapperOptions = $derived(optionsFrom(wb.transfers, (t) => t.wrapper));

	// Route stays on the store (`wb.routeFilter`) — it's shared with `rowMatches`/`visibleQueue` —
	// rather than folding into a local `string[]` here, so the settlement board reads the same
	// narrowing this panel sets.
	let statusFilter = $state<string[]>([]);
	let wrapperFilter = $state<string[]>([]);
	const dirty = $derived(
		wb.routeFilter.length > 0 || statusFilter.length > 0 || wrapperFilter.length > 0
	);
	function clearFilters(): void {
		wb.routeFilter = [];
		statusFilter = [];
		wrapperFilter = [];
	}

	// `wb.visibleQueue` already carries the needs/all view, the settlement board's `stageFilter`,
	// `wb.routeFilter` and `wb.search`. These two narrow further, client-side, same as every other
	// book listing.
	const rows = $derived(
		wb.visibleQueue.filter(
			(t) => matches(statusFilter, t.status) && matches(wrapperFilter, t.wrapper)
		)
	);

	const filterDefs = $derived(
		transferQueueFilterDefs({ routeOptions, statusOptions, wrapperOptions })
	);
	const barValue = $derived<Record<string, string[]>>({
		...(wb.routeFilter.length ? { route: wb.routeFilter } : {}),
		...(statusFilter.length ? { status: statusFilter } : {}),
		...(wrapperFilter.length ? { wrapper: wrapperFilter } : {})
	});
	function setFilters(next: Record<string, string[]>): void {
		wb.routeFilter = (next.route as TransferRoute[] | undefined) ?? [];
		statusFilter = next.status ?? [];
		wrapperFilter = next.wrapper ?? [];
	}

	// `status` sorts on its displayed label, not the raw enum, so the order matches the word the
	// badge shows. `value` is a plain field the default row-read sorts correctly on its own.
	const columns: DataTableColumn<Transfer>[] = [
		{
			key: 'transfer',
			header: 'Transfer',
			sortable: true,
			sortDefault: true,
			sortValue: (t) => t.client
		},
		{
			key: 'value',
			header: 'Value',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			sortValue: (t) => STATUS_LABEL[t.status] ?? t.status
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let sort = $state<DataTableSort | null>(null);
</script>

<div class="border-border/70 bg-card overflow-hidden rounded-xl border">
	<div class="border-border/70 flex flex-wrap items-center gap-2 border-b p-2.5">
		<!-- Not a FilterSelect: like "Needs action" on the contributions queue, this is a view
		     toggle, not a single-field filter — kept exactly as it worked before the migration. -->
		{#each views as v (v.key)}
			<Button
				variant={wb.view === v.key ? 'default' : 'outline'}
				size="sm"
				onclick={() => (wb.view = v.key)}>{v.label}</Button
			>
		{/each}
		<ListingToolbar
			bind:query={wb.search}
			placeholder="Search transfer id, provider, client…"
			{dirty}
			onclear={clearFilters}
			class="flex-1"
			{filterDefs}
			filterValue={barValue}
			onfilters={setFilters}
		/>
	</div>
	<DataTable
		{columns}
		{rows}
		bind:sort
		getKey={(t) => t.id}
		label="Transfers"
		empty="No transfers match."
		onRowClick={(t) => wb.select(t.id)}
		rowClass={(t) =>
			cn(
				t.id === wb.selectedId && 'bg-accent/60',
				t.status === 'manual_intervention' && 'bg-destructive/10'
			)}
	>
		{#snippet cell(t, column)}
			{#if column.key === 'transfer'}
				<div class="flex items-center gap-2">
					<span class="font-mono text-[11px]">{t.id}</span>
					<span class="font-medium">{t.client}</span>
					<RouteBadge route={t.route} />
				</div>
				<div class="text-muted-foreground mt-0.5 text-[11px]">
					{t.provider} → {custodianLabel(t)} · {t.wrapper} ·
					{t.basis === 'cash' ? 'Cash' : 'In-specie'} · {t.scope}
				</div>
			{:else if column.key === 'value'}
				{formatGBP(t.value)}
			{:else if column.key === 'status'}
				<StatusBadge status={t.status} />
			{/if}
		{/snippet}
	</DataTable>
</div>
