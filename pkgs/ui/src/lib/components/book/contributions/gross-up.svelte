<script lang="ts">
	// The number that actually lands in the pension, which is not the number that arrived.
	// Showing it is what makes the choice of category feel consequential rather than clerical.
	import { grossOf, reliefOf, RELIEF_RATE } from './relief';
	import type { ContributionCategory } from './types';
	import { Money } from '../../ui/money/index.js';

	let {
		amount,
		category
	}: {
		amount: number;
		category: ContributionCategory;
	} = $props();

	const input = $derived({ amount, category });
	const gross = $derived(grossOf(input));
	const relief = $derived(reliefOf(input));
	const rate = `${Math.round(RELIEF_RATE * 100)}%`;
</script>

<div class="border-border/70 bg-muted/40 space-y-1.5 rounded-lg border p-3 text-xs">
	{#if category === 'personal_contribution' || category === 'third_party_contribution'}
		<div class="flex items-baseline justify-between">
			<span class="text-muted-foreground">Net received (member pays)</span>
			<Money {amount} class="font-medium" />
		</div>
		<div class="text-success-strong flex items-baseline justify-between">
			<span>Basic-rate relief reclaimed ({rate})</span>
			<Money amount={relief} signed tone="directional" class="font-medium" />
		</div>
		<div class="border-border/70 mt-1 flex items-baseline justify-between border-t pt-2">
			<span class="font-semibold">Gross contribution invested</span>
			<Money amount={gross} class="text-base font-semibold" />
		</div>
		<p class="text-muted-foreground pt-1 text-xs">
			gross = net ÷ {1 - RELIEF_RATE} · relief = gross − net · reclaimed from HMRC via relief at source
		</p>
	{:else if category === 'employer_contribution'}
		<div class="flex items-baseline justify-between">
			<span class="text-muted-foreground">Gross contribution (paid gross)</span>
			<Money {amount} class="font-medium" />
		</div>
		<p class="text-muted-foreground pt-1 text-xs">
			Employer contribution — no relief reclaimed; the employer takes the corporation-tax deduction
			instead.
		</p>
	{:else}
		<div class="flex items-baseline justify-between">
			<span class="text-muted-foreground">Amount</span>
			<Money {amount} class="font-medium" />
		</div>
		<p class="text-muted-foreground pt-1 text-xs">
			No relief is due on this category and it uses no annual allowance.
		</p>
	{/if}
</div>
