<script lang="ts">
	import type { AllocationSlice } from '../charts/allocation-donut.svelte';
	import { allocationDisplay } from '../charts/percentages.js';
	import { Money } from '../ui/money/index.js';
	import { formatGBP } from '../../money.js';

	// The allocation 'bars' variant: one horizontal bar row per asset class — label, share of the
	// total and (when the portfolio value is known) the £ amount. The same derivation the donut
	// uses (charts/percentages.ts), in a flat reading some firms prefer to a chart: percentages
	// are largest-remainder rounded so the column always sums to 100, and negative slices
	// (overdrawn cash) are excluded from the bars and disclosed in the footnote — a bar of
	// negative width is not a smaller bar, it's garbage.
	let {
		data,
		/** Portfolio value — when given, rows show £ amounts alongside the percentages. */
		total
	}: {
		data: AllocationSlice[];
		total?: number;
	} = $props();

	const display = $derived(allocationDisplay(data, total));
</script>

<div class="w-full space-y-4">
	{#each display.slices as s (s.label)}
		<div>
			<div class="flex items-baseline justify-between gap-3 text-sm">
				<span class="flex min-w-0 items-center gap-2.5">
					<span class="size-2.5 shrink-0 rounded-full" style="background:{s.color}"></span>
					<span class="text-muted-foreground truncate">{s.label}</span>
				</span>
				<span class="flex shrink-0 items-baseline gap-3">
					{#if s.amount !== null}
						<Money amount={s.amount} class="font-medium" />
					{/if}
					<span class="text-muted-foreground w-9 text-right tabular-nums">{s.pct}%</span>
				</span>
			</div>
			<div class="bg-muted mt-1.5 h-2 overflow-hidden rounded-full">
				<div class="h-full rounded-full" style="width:{s.pct}%;background:{s.color}"></div>
			</div>
		</div>
	{/each}
	{#if display.excludedAny}
		<p class="text-muted-foreground/80 text-xs">
			Excludes negative balances{display.excludedAmount !== null
				? ` (−${formatGBP(Math.abs(display.excludedAmount))})`
				: ''}
		</p>
	{/if}
</div>
