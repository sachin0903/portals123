<script lang="ts">
	import { rankedBreakdown, normaliseRatio } from './visuals.js';
	import { series } from '../../../categorical.js';
	import type { LookthroughBreakdown } from './types.js';

	let {
		title,
		rows,
		step = 0
	}: {
		title: string;
		rows: LookthroughBreakdown[];
		/** Which step of the brand ramp (`series`) the bars take — so three breakdowns side by
		 *  side read as three, in the firm's colours, instead of one primary repeated. */
		step?: number;
	} = $props();

	const ranked = $derived(rankedBreakdown(rows));
	const displayed = $derived(ranked.slice(0, 8));
	const colour = $derived(series(step));
	const pct = (value: number) =>
		normaliseRatio(value).toLocaleString('en-GB', {
			style: 'percent',
			minimumFractionDigits: 0,
			maximumFractionDigits: 1
		});
</script>

<section aria-labelledby={`breakdown-${title.toLowerCase().replace(/\s+/g, '-')}`}>
	<h3
		id={`breakdown-${title.toLowerCase().replace(/\s+/g, '-')}`}
		class="mb-3 flex items-center gap-2 text-sm font-semibold"
	>
		<span class="size-2.5 shrink-0 rounded-full" style:background={colour} aria-hidden="true"
		></span>
		{title}
	</h3>
	{#if displayed.length === 0}
		<p class="text-muted-foreground text-sm">No breakdown available.</p>
	{:else}
		<ol class="space-y-2.5">
			{#each displayed as row, index (row.label)}
				<li>
					<div class="mb-1 flex items-baseline justify-between gap-3 text-xs">
						<span class="min-w-0 truncate font-medium" title={row.label}>{row.label}</span>
						<span class="text-muted-foreground shrink-0 tabular-nums">{pct(row.value)}</span>
					</div>
					<div class="bg-muted h-1.5 overflow-hidden rounded-full">
						<!-- The leader is solid; the rest fade down the list, so the eye lands on it. -->
						<div
							class="h-full rounded-full"
							style:width={`${normaliseRatio(row.value) * 100}%`}
							style:background={colour}
							style:opacity={index === 0 ? 1 : Math.max(0.45, 1 - index * 0.09)}
						></div>
					</div>
				</li>
			{/each}
		</ol>
	{/if}
</section>
