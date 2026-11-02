<script lang="ts">
	import ArrowDownLeftIcon from '@lucide/svelte/icons/arrow-down-left';
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import type { FundLookthroughData } from './types.js';
	import { Money } from '../../ui/money/index.js';

	let {
		contracts
	}: {
		contracts: FundLookthroughData['currency_contracts'];
	} = $props();

	const pct = (value: number | null) =>
		value == null
			? '—'
			: value.toLocaleString('en-GB', {
					style: 'percent',
					signDisplay: 'always',
					maximumFractionDigits: 2
				});
	const date = (value: string | null) =>
		value
			? new Date(`${value}T00:00:00`).toLocaleDateString('en-GB', {
					day: 'numeric',
					month: 'short',
					year: 'numeric'
				})
			: 'No delivery date';
</script>

<div>
	<div>
		<h2 class="font-semibold">Currency hedging</h2>
		<p class="text-muted-foreground mt-1 text-xs">
			Open currency contracts and their reported effect on the fund’s NAV.
		</p>
	</div>

	{#if contracts.length}
		<ul class="mt-4 divide-y" aria-label="Open currency contracts">
			{#each contracts.slice(0, 8) as contract, index (`${contract.name}-${index}`)}
				<li class="grid gap-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center">
					<div class="min-w-0">
						<p class="flex items-center gap-1.5 truncate text-xs font-medium">
							{#if (contract.unrealised_gain_loss ?? 0) >= 0}
								<ArrowUpRightIcon class="text-success size-3.5 shrink-0" />
							{:else}
								<ArrowDownLeftIcon class="text-destructive size-3.5 shrink-0" />
							{/if}
							{contract.name}
						</p>
						<p class="text-muted-foreground mt-0.5 text-xs">
							{contract.bought_sold ?? contract.long_short ?? 'Open'} · {date(
								contract.delivery_date
							)}
						</p>
					</div>
					<div class="sm:text-right">
						<p class="text-muted-foreground text-xs">Contract</p>
						<p class="text-xs">
							<Money amount={contract.amount} currency={contract.currency ?? 'GBP'} />
						</p>
					</div>
					<div class="sm:min-w-24 sm:text-right">
						<p class="text-muted-foreground text-xs">NAV impact</p>
						<p class="text-xs font-medium tabular-nums">{pct(contract.nav_impact)}</p>
					</div>
				</li>
			{/each}
		</ul>
		{#if contracts.length > 8}
			<p class="text-muted-foreground mt-2 text-xs">
				Showing the eight largest NAV impacts from {contracts.length} open contracts.
			</p>
		{/if}
	{:else}
		<p class="text-muted-foreground py-10 text-center text-xs">
			No open currency contracts were reported for this share class.
		</p>
	{/if}
</div>
