<script lang="ts">
	import type { FundLookthroughData } from './types.js';
	import { Money } from '../../ui/money/index.js';
	import { series } from '../../../categorical.js';

	let {
		coverage,
		cash
	}: {
		coverage: number;
		cash: FundLookthroughData['cash'];
	} = $props();

	const cashWeight = $derived(cash.net_weight);
	const other = $derived(cash.other_residual);
	const positiveTotal = $derived(
		Math.max(coverage, 0) + Math.max(cashWeight, 0) + Math.max(other, 0) || 1
	);
	const width = (value: number) => `${(Math.max(value, 0) / positiveTotal) * 100}%`;
	const legend = $derived<[string, number, number][]>([
		['Securities', coverage, 0],
		['Net cash', cashWeight, 1],
		['Other NAV items', other, 3]
	]);
	const pct = (value: number) =>
		value.toLocaleString('en-GB', {
			style: 'percent',
			signDisplay: value < 0 ? 'always' : 'auto',
			maximumFractionDigits: 2
		});
</script>

<div>
	<div>
		<h2 class="font-semibold">Cash and residual composition</h2>
		<p class="text-muted-foreground mt-1 text-xs">
			How the disclosed securities, net currency cash and remaining NAV items reconcile.
		</p>
	</div>

	<div
		class="bg-muted mt-5 flex h-3 overflow-hidden rounded-full"
		role="img"
		aria-label={`Disclosed securities ${pct(coverage)}, net cash ${pct(cashWeight)}, other NAV items ${pct(other)}`}
	>
		<!-- Three steps of the brand ramp (`series`), the same scale the allocation donut uses, so
		     the segments are told apart by the firm's colours and the legend below. -->
		<div class="h-full" style:width={width(coverage)} style:background={series(0)}></div>
		<div class="h-full" style:width={width(cashWeight)} style:background={series(1)}></div>
		<div class="h-full" style:width={width(other)} style:background={series(3)}></div>
	</div>
	<div class="mt-3 grid grid-cols-3 gap-3">
		{#each legend as [label, value, step] (label)}
			<div>
				<p class="text-muted-foreground flex items-center gap-1.5 text-xs">
					<span
						class="size-2 shrink-0 rounded-full"
						style:background={series(step)}
						aria-hidden="true"
					></span>
					{label}
				</p>
				<p class="mt-0.5 font-semibold tabular-nums">{pct(value)}</p>
			</div>
		{/each}
	</div>

	{#if cash.balances.length}
		<ul class="mt-5 divide-y border-t" aria-label="Cash balances by currency">
			{#each cash.balances as balance, index (`${balance.currency}-${balance.name}-${index}`)}
				<li class="flex items-center justify-between gap-4 py-2 text-xs">
					<div class="min-w-0">
						<span class="font-medium">{balance.currency}</span>
						<span class="text-muted-foreground ml-2 truncate text-xs">{balance.name}</span>
					</div>
					<div class="shrink-0 text-right">
						<p><Money amount={balance.base_value} /></p>
						<p class="text-muted-foreground text-xs tabular-nums">{pct(balance.weight)}</p>
					</div>
				</li>
			{/each}
		</ul>
	{:else}
		<p class="text-muted-foreground mt-5 text-xs">No currency cash rows were supplied.</p>
	{/if}
</div>
