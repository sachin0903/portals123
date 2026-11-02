<script lang="ts">
	import { formatGBP } from '@graphene/ui';
	import { grossOf, reliefOf } from '$lib/contributions/ras';
	import type { Contribution } from '$lib/contributions/types';

	let { contribution }: { contribution: Contribution } = $props();

	const gross = $derived(grossOf(contribution));
	const relief = $derived(reliefOf(contribution));
</script>

{#if contribution.type === 'personal'}
	<div class="border-border/70 bg-muted/40 space-y-1.5 rounded-lg border p-3 text-sm">
		<div class="flex items-baseline justify-between">
			<span class="text-muted-foreground">Net received (member pays)</span>
			<span class="font-medium tabular-nums">{formatGBP(contribution.net)}</span>
		</div>
		<div class="text-success-strong flex items-baseline justify-between">
			<span>Basic-rate relief reclaimed (20%)</span>
			<span class="font-medium tabular-nums">+ {formatGBP(relief)}</span>
		</div>
		<div class="border-border/70 mt-1 flex items-baseline justify-between border-t pt-2">
			<span class="font-semibold">Gross contribution invested</span>
			<span class="text-base font-semibold tabular-nums">{formatGBP(gross)}</span>
		</div>
		<p class="text-muted-foreground pt-1 text-xs">
			gross = net ÷ 0.80 · relief = gross − net · reclaimed from HMRC via relief at source
		</p>
	</div>
{:else}
	<div class="border-border/70 bg-muted/40 space-y-1 rounded-lg border p-3 text-sm">
		<div class="flex items-baseline justify-between">
			<span class="text-muted-foreground">Gross contribution (employer, paid gross)</span>
			<span class="font-medium tabular-nums">{formatGBP(contribution.net)}</span>
		</div>
		<p class="text-muted-foreground pt-1 text-xs">Employer contribution. No relief is reclaimed.</p>
	</div>
{/if}
