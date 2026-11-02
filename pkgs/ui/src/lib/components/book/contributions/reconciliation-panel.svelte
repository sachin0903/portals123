<script lang="ts">
	// The three-way compare: what we booked, what we told the custodian to expect, what landed.
	//
	// Derived on read (`app/matching.py::reconcile`), not stored. `docs/contributions.md` §7 lists
	// `platform.reconciliation_breaks` as unbuilt and this deliberately does not build it: a break
	// here is a statement about the current rows, so a stored copy goes stale the moment either
	// side is corrected, and then the breaks table needs reconciling against the ledger it exists
	// to reconcile. What a stored break would add is workflow — assignment, resolution notes —
	// which is worth having once there is someone assigned to work it.
	import CheckIcon from '@lucide/svelte/icons/check';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import { Money } from '../../ui/money/index.js';
	import { cn } from '../../../utils.js';
	import type { Reconciliation } from './types.js';

	let { reconciliation }: { reconciliation: Reconciliation } = $props();

	const TONE = {
		pending: 'text-muted-foreground',
		matched: 'text-success-strong',
		break: 'text-destructive-strong'
	} as const;

	const HEADING = {
		pending: 'Awaiting the money',
		matched: 'Reconciled',
		break: 'Does not reconcile'
	} as const;
</script>

<div class="space-y-2">
	<div class={cn('flex items-center gap-1.5 text-sm font-medium', TONE[reconciliation.status])}>
		{#if reconciliation.status === 'matched'}
			<CheckIcon class="size-4" aria-hidden="true" />
		{:else if reconciliation.status === 'break'}
			<TriangleAlertIcon class="size-4" aria-hidden="true" />
		{:else}
			<ClockIcon class="size-4" aria-hidden="true" />
		{/if}
		{HEADING[reconciliation.status]}
	</div>

	<dl class="divide-border divide-y text-xs">
		{#each reconciliation.legs as leg (leg.key)}
			<div class="flex items-baseline justify-between gap-4 py-1.5">
				<dt class="text-muted-foreground text-xs">{leg.label}</dt>
				<dd class="tabular-nums">
					{#if leg.value === null}
						<!-- An absent leg is "not yet", not zero. Rendering £0.00 would read as a
						     reconciled nil rather than a payment that has not arrived. -->
						<span class="text-muted-foreground">—</span>
					{:else}
						<Money amount={leg.value} />
					{/if}
				</dd>
			</div>
		{/each}
		{#if reconciliation.variance !== null && reconciliation.variance !== 0}
			<div class="flex items-baseline justify-between gap-4 py-1.5 font-medium">
				<dt class="text-destructive-strong text-xs">Variance</dt>
				<dd class="text-destructive-strong tabular-nums">
					<Money amount={reconciliation.variance} />
				</dd>
			</div>
		{/if}
	</dl>

	{#if reconciliation.reason}
		<p class="text-muted-foreground text-xs">{reconciliation.reason}</p>
	{/if}
</div>
