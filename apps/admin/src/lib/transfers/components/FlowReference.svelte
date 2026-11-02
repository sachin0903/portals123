<script lang="ts">
	import { Card, Table } from '@graphene/ui';
	import { ROUTES, STATUSES } from '$lib/transfers/status';
	import type { TransferRoute } from '$lib/transfers/types';
	import StatusBadge from './StatusBadge.svelte';

	const reconChain = [
		['1 · Forecast', 'Graphene', 'Record the expected amount and transfer details'],
		['2 · Instruction', 'Provider', 'Send and track the transfer instruction'],
		['3 · Receipt', 'Custodian', 'Record the cash or assets received'],
		['4 · Match', 'Graphene', 'Match receipts to the expected transfer'],
		['5 · Reconcile', 'Graphene', 'Complete when the difference is no more than £0.01']
	];
	const routes: { route: TransferRoute; legs: string; status: string; note: string }[] = [
		{
			route: 'ml_sei',
			legs: 'External SIPP → Morgan Lloyd → SEI',
			status: 'live',
			note: 'Cross-system pension transfer; three-way reconciliation (the current saga).'
		},
		{
			route: 'intra_seccl',
			legs: 'Seccl a/c → Seccl a/c',
			status: 'next',
			note: 'A transfer between two Seccl accounts.'
		},
		{
			route: 'intra_sei',
			legs: 'SEI a/c → SEI a/c',
			status: 'next',
			note: 'A transfer between two SEI accounts.'
		}
	];
</script>

<div class="space-y-6">
	<div>
		<h4 class="mb-2 text-sm font-semibold">Canonical lifecycle</h4>
		<div class="flex flex-wrap items-center gap-1">
			{#each STATUSES as s, i (s)}
				<StatusBadge status={s} />{#if i < STATUSES.length - 1}<span
						class="text-muted-foreground mx-0.5">→</span
					>{/if}
			{/each}
			<span class="text-muted-foreground mx-0.5">⤷</span>
			<StatusBadge status="manual_intervention" />
		</div>
		<p class="text-muted-foreground mt-2 text-xs">
			Graphene status is authoritative. Provider statuses are mapped to this lifecycle.
		</p>
	</div>

	<div>
		<h4 class="mb-2 text-sm font-semibold">Reconciliation steps</h4>
		<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head>Step</Table.Head>
						<Table.Head>Owner</Table.Head>
						<Table.Head>What happens</Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each reconChain as [step, table, what] (step)}
						<Table.Row>
							<Table.Cell class="text-xs font-medium whitespace-nowrap">{step}</Table.Cell>
							<Table.Cell><code class="text-[11px]">{table}</code></Table.Cell>
							<Table.Cell class="text-muted-foreground text-[11px]">{what}</Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
		</div>
	</div>

	<div>
		<h4 class="mb-2 text-sm font-semibold">One workbench, three systems</h4>
		<div class="grid gap-3 md:grid-cols-3">
			{#each routes as r (r.route)}
				<Card glass class="p-4">
					<div class="flex items-center justify-between">
						<span class="text-xs font-semibold">{ROUTES[r.route].label}</span>
						<span
							class="rounded-full px-2 py-0.5 text-[9.5px] font-semibold tracking-wide uppercase {r.status ===
							'live'
								? 'bg-success-subtle text-success-strong'
								: 'bg-info-subtle text-info-strong'}">{r.status}</span
						>
					</div>
					<div class="text-muted-foreground mt-1.5 font-mono text-[11px]">{r.legs}</div>
					<p class="text-muted-foreground mt-2 text-xs">{r.note}</p>
					<div class="mt-2 flex flex-wrap gap-1">
						{#each ROUTES[r.route].reconLegs as leg (leg)}
							<span class="bg-muted rounded px-1.5 py-0.5 text-[10px]">{leg}</span>
						{/each}
					</div>
				</Card>
			{/each}
		</div>
	</div>
</div>
