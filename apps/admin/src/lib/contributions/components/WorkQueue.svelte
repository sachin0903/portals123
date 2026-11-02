<script module lang="ts">
	import type { FilterDef } from '@graphene/ui/book/listing';

	/** `type` stays the toolbar-row Segmented, not one of these defs: a closed two-value switch
	 *  reads better as an always-visible pill than a control hidden behind the funnel. */
	export function contributionQueueFilterDefs(input: {
		statusOptions: FilterDef['options'];
		sourceOptions: FilterDef['options'];
	}): FilterDef[] {
		return [
			{ key: 'status', label: 'Status', options: input.statusOptions },
			{ key: 'source', label: 'Source', options: input.sourceOptions }
		];
	}
</script>

<script lang="ts">
	import {
		Button,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		RecordId,
		Segmented,
		cn,
		formatGBP,
		humanise
	} from '@graphene/ui';
	import { ALL, ListingToolbar, matches, optionsFrom } from '@graphene/ui/book/listing';
	import { grossOf } from '$lib/contributions/ras';
	import { STATUS_LABEL } from '$lib/contributions/status';
	import type { Contribution, ContributionStatus } from '$lib/contributions/types';
	import type { WorkbenchStore } from '$lib/contributions/store.svelte';
	import StatusBadge from './StatusBadge.svelte';
	import MatchBadge from './MatchBadge.svelte';

	let { wb }: { wb: WorkbenchStore } = $props();

	// Options come from the whole book (wb.contributions), not the already-filtered queue, so
	// picking one filter never shrinks another's option list out from under the operator.
	const statusOptions = $derived(
		optionsFrom(
			wb.contributions,
			(c) => c.status,
			(s) => STATUS_LABEL[s as ContributionStatus]
		)
	);
	// `type` is a closed two-value vocabulary (personal/employer), not data-derived like the others —
	// a Segmented switch that reads as "All | Personal | Employer" rather than a dropdown that hides
	// the second option behind a click. Fixed, not built from the rows on screen, so both segments
	// stay reachable even when the current queue happens to hold only one type.
	const typeOptions = [
		{ value: ALL, label: 'All' },
		{ value: 'personal', label: 'Personal' },
		{ value: 'employer', label: 'Employer' }
	];
	const sourceOptions = $derived(optionsFrom(wb.contributions, (c) => c.source));

	let statusFilter = $state<string[]>([]);
	let typeFilter = $state(ALL);
	let sourceFilter = $state<string[]>([]);
	const dirty = $derived(statusFilter.length > 0 || typeFilter !== ALL || sourceFilter.length > 0);
	function clearFilters(): void {
		statusFilter = [];
		typeFilter = ALL;
		sourceFilter = [];
	}

	// `wb.visibleQueue` already carries the pipeline strip's `stageFilter`, the "Needs action"
	// toggle (`wb.filter`) and `wb.search` — those are shared store state other surfaces drive too.
	// These three selects narrow further, client-side, same as every other book listing.
	const rows = $derived(
		wb.visibleQueue.filter(
			(c) =>
				matches(statusFilter, c.status) &&
				matches(typeFilter, c.type) &&
				matches(sourceFilter, c.source)
		)
	);

	const filterDefs = $derived(contributionQueueFilterDefs({ statusOptions, sourceOptions }));
	const barValue = $derived<Record<string, string[]>>({
		...(statusFilter.length ? { status: statusFilter } : {}),
		...(sourceFilter.length ? { source: sourceFilter } : {})
	});
	function setFilters(next: Record<string, string[]>): void {
		statusFilter = next.status ?? [];
		sourceFilter = next.source ?? [];
	}

	const MONTHS = [
		'Jan',
		'Feb',
		'Mar',
		'Apr',
		'May',
		'Jun',
		'Jul',
		'Aug',
		'Sep',
		'Oct',
		'Nov',
		'Dec'
	];
	// The mock feed carries a display-only "24 Jun" date with no year — sorting the raw string
	// would put "5 Jan" after "24 Jun", so this is the closest thing to a real timestamp available
	// to sort chronologically rather than alphabetically.
	function parseShortDate(d: string): number {
		const [day, mon] = d.split(' ');
		const month = MONTHS.indexOf(mon);
		return month === -1 ? 0 : new Date(new Date().getFullYear(), month, Number(day)).getTime();
	}

	// `type`/`matched`/`status` are all derived — humanised, store-looked-up or badge-mapped — so
	// each carries its own `sortValue` reading the same thing the cell renders.
	const columns: DataTableColumn<Contribution>[] = [
		{ key: 'member', header: 'Member / account', sortable: true, sortDefault: true },
		{
			key: 'date',
			header: 'Received',
			cellClass: 'text-muted-foreground whitespace-nowrap',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (c) => parseShortDate(c.date)
		},
		{
			key: 'net',
			header: 'Net received',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		},
		{ key: 'type', header: 'Type', sortable: true, sortValue: (c) => humanise(c.type) },
		{ key: 'source', header: 'Source', cellClass: 'whitespace-nowrap', sortable: true },
		{
			key: 'matched',
			header: 'Matched',
			sortable: true,
			sortValue: (c) => wb.matchFor(c.id)?.status ?? ''
		},
		{
			// The contribution's own lifecycle stage, distinct from Matched.
			key: 'status',
			header: 'Contribution status',
			sortable: true,
			sortValue: (c) => STATUS_LABEL[c.status] ?? c.status
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let sort = $state<DataTableSort | null>(null);
</script>

<div class="border-border/70 bg-card overflow-hidden rounded-xl border">
	<div class="border-border/70 border-b p-2.5">
		<ListingToolbar
			bind:query={wb.search}
			placeholder="Search member, account, scheme…"
			{dirty}
			onclear={clearFilters}
			{filterDefs}
			filterValue={barValue}
			onfilters={setFilters}
		>
			{#snippet filters()}
				<!-- Not a FilterSelect: this maps to a SET of statuses (identified, awaiting_confirmation),
				     not one field value, so it stays a standalone toggle rather than joining the selects. -->
				<Button
					variant={wb.filter === 'action' ? 'default' : 'outline'}
					size="sm"
					onclick={() => (wb.filter = wb.filter === 'action' ? 'all' : 'action')}
				>
					Needs action
				</Button>
				<Segmented bind:value={typeFilter} options={typeOptions} size="sm" label="Filter by type" />
			{/snippet}
		</ListingToolbar>
	</div>
	<DataTable
		{columns}
		{rows}
		bind:sort
		getKey={(c) => c.id}
		label="Contributions"
		empty="No contributions match."
		onRowClick={(c) => wb.select(c.id)}
		rowClass={(c) =>
			cn(
				c.id === wb.selectedId && 'bg-accent/60',
				c.status === 'manual_intervention' && 'bg-destructive/10'
			)}
	>
		{#snippet cell(c, column)}
			{@const match = wb.matchFor(c.id)}
			{#if column.key === 'member'}
				<span class="font-medium">{c.member}</span>
				<RecordId value={`${c.seiAccount} · ${c.scheme}`} class="text-[10.5px]" />
			{:else if column.key === 'date'}
				{c.date}
			{:else if column.key === 'net'}
				{formatGBP(c.net)}
				<span class="text-muted-foreground block text-[10.5px]">gross {formatGBP(grossOf(c))}</span>
			{:else if column.key === 'type'}
				<span>{humanise(c.type)}</span>
				<span class="text-muted-foreground block text-[11px]"
					>{c.freq === 'one_off' ? 'Single' : 'Regular'}</span
				>
			{:else if column.key === 'source'}
				{c.source}
			{:else if column.key === 'matched'}
				{#if match}
					<MatchBadge status={match.status} />
				{:else}
					<span class="text-muted-foreground">—</span>
				{/if}
			{:else if column.key === 'status'}
				<StatusBadge status={c.status} />
			{/if}
		{/snippet}
	</DataTable>
</div>
