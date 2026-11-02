<script lang="ts">
	// The run log for one automation — every CONSIDERED firing, not just the ones that ran.
	//
	// That distinction is the whole point. `flows.automation_deliveries` records a row for each
	// candidate the consumer evaluated, with the reason it did or didn't dispatch, because the
	// question this tier generates most is "why didn't my automation fire?" — and a log of successes
	// answers exactly none of it. So the outcome column leads, in the firm's words ("Too soon",
	// "No match"), with the machine's own detail behind it.
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import { DataTable, type DataTableColumn } from '../ui/data-table/index.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import { formatWhen, outcomeHint, outcomeLabel, outcomeTone } from './summary.js';
	import type { DeliveryRow } from './types.js';

	let {
		deliveries,
		hasMore = false,
		loading = false,
		onmore
	}: {
		deliveries: DeliveryRow[];
		hasMore?: boolean;
		loading?: boolean;
		/** Absent ⇒ no "load older" control (a static mount, or nothing more to fetch). */
		onmore?: () => void;
	} = $props();

	const contextBits = (d: DeliveryRow) =>
		[
			d.subject_kind && d.subject_id ? `${d.subject_kind} ${d.subject_id}` : '',
			d.case_uid ? `journey ${d.case_uid}` : '',
			// Depth is how you spot an automation chain before it hits the cap.
			d.chain_depth > 0 ? `chain depth ${d.chain_depth}` : '',
			d.attempts > 1 ? `${d.attempts} attempts` : ''
		].filter(Boolean);

	const columns: DataTableColumn<DeliveryRow>[] = [
		{ key: 'outcome', header: 'Outcome', headerClass: 'w-[16%] min-w-28' },
		{ key: 'detail', header: 'What happened', headerClass: 'w-[38%]' },
		{ key: 'context', header: 'Context', hideBelowWidth: 'lg', headerClass: 'w-[18%]' },
		{
			key: 'created_at',
			header: 'When',
			sortable: true,
			sortDefault: true,
			defaultDir: 'desc',
			headerClass: 'w-[14%]'
		},
		{ key: 'retry', header: 'Retry', hideBelowWidth: 'xl', headerClass: 'w-[14%]' }
	];
</script>

{#snippet noRuns()}
	<EmptyState
		icon={HistoryIcon}
		title="No runs recorded yet"
		description="This automation hasn’t been considered for an event yet. Every time an event matches its trigger — whether or not it fires — the outcome lands here."
	/>
{/snippet}

<DataTable
	{columns}
	rows={deliveries}
	getKey={(d) => d.id}
	label="Run history"
	empty={noRuns}
	fixed
>
	{#snippet cell(delivery, column)}
		{#if column.key === 'outcome'}
			<StatusBadge tone={outcomeTone(delivery.outcome)}>
				{outcomeLabel(delivery.outcome)}
			</StatusBadge>
		{:else if column.key === 'detail'}
			<div class="min-w-0">
				<p class="text-sm">{delivery.detail || outcomeHint(delivery.outcome)}</p>
				{#if delivery.error}
					<p class="text-destructive mt-0.5 text-xs">{delivery.error}</p>
				{/if}
			</div>
		{:else if column.key === 'context'}
			{@const bits = contextBits(delivery)}
			{#if bits.length}
				<div class="text-muted-foreground flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
					{#each bits as bit, i (i)}<span>{bit}</span>{/each}
				</div>
			{:else}
				<span class="text-muted-foreground text-xs">—</span>
			{/if}
		{:else if column.key === 'created_at'}
			<span class="text-muted-foreground text-xs">{formatWhen(delivery.created_at)}</span>
		{:else if column.key === 'retry'}
			{#if delivery.retry_after}
				<Badge variant="outline">Retries {formatWhen(delivery.retry_after)}</Badge>
			{:else}
				<span class="text-muted-foreground text-xs">—</span>
			{/if}
		{/if}
	{/snippet}
</DataTable>
{#if deliveries.length > 0 && hasMore && onmore}
	<div class="pt-3">
		<Button variant="outline" size="sm" disabled={loading} onclick={onmore}>
			{loading ? 'Loading…' : 'Load older'}
		</Button>
	</div>
{/if}
