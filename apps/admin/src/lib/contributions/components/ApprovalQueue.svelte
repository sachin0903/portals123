<script lang="ts">
	import {
		Button,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		formatGBP
	} from '@graphene/ui';
	import type { WorkbenchStore } from '$lib/contributions/store.svelte';
	import type { Match } from '$lib/contributions/types';

	let { wb }: { wb: WorkbenchStore } = $props();

	const txFor = (id: string) => wb.seiPool.find((t) => t.id === id) ?? null;

	// `contribution`/`net` are store lookups, not fields on `Match` itself, so each carries an
	// explicit `sortValue` reading the same lookup the cell renders. `origin` is a hardcoded label
	// today (every row reads "on creation") and `decision` is the action pair — neither orders.
	const columns: DataTableColumn<Match>[] = [
		{
			key: 'contribution',
			header: 'Contribution',
			cellClass: 'font-medium',
			sortable: true,
			sortDefault: true,
			sortValue: (m) => wb.contributionFor(m.id)?.member ?? ''
		},
		{
			key: 'net',
			header: 'Net',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (m) => wb.contributionFor(m.id)?.net ?? 0
		},
		{
			key: 'proposedTxn',
			header: 'Proposed SEI txn',
			cellClass: 'font-mono text-xs',
			sortable: true,
			sortValue: (m) => m.seiTransactionId
		},
		{ key: 'origin', header: 'Origin', cellClass: 'text-muted-foreground' },
		{ key: 'decision', header: 'Decision', align: 'right', cellClass: 'whitespace-nowrap' }
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let sort = $state<DataTableSort | null>(null);
</script>

<div class="space-y-3">
	<p class="text-muted-foreground text-sm">
		Match-on-creation requests from advisers. Approving applies the SEI credit to the contribution;
		rejecting returns the credit to the pool.
	</p>
	<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
		<DataTable
			{columns}
			rows={wb.pendingApprovals}
			bind:sort
			getKey={(m) => m.id}
			label="Pending approvals"
			empty="No matches awaiting approval."
		>
			{#snippet cell(m, column)}
				{@const c = wb.contributionFor(m.id)}
				{@const tx = txFor(m.seiTransactionId)}
				{#if column.key === 'contribution'}
					{c?.member ?? '—'}
				{:else if column.key === 'net'}
					{c ? formatGBP(c.net) : '—'}
				{:else if column.key === 'proposedTxn'}
					{m.seiTransactionId}{tx ? ` · ${formatGBP(tx.amount)}` : ''}
				{:else if column.key === 'origin'}
					on creation
				{:else if column.key === 'decision'}
					<Button size="sm" onclick={() => wb.approveMatch(m.id)}>Approve</Button>
					<Button size="sm" variant="outline" onclick={() => wb.rejectMatch(m.id)}>Reject</Button>
				{/if}
			{/snippet}
		</DataTable>
	</div>
</div>
