<script lang="ts">
	import ChartPieIcon from '@lucide/svelte/icons/chart-pie';
	import type { AllocationSlice } from '../charts/allocation-donut.svelte';
	import { allocationDisplay } from '../charts/percentages.js';
	import { formatGBP } from '../../money.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';

	// The allocation 'spectrum' look: ONE full-width proportional band — the whole portfolio as a
	// single flat stripe of categorical colour, rounded at its outer ends only. Dramatic and
	// chrome-less: no card, no ring, no per-class rows; the eye reads the split as widths.
	//
	// Deliberately NO heading: the band is the statement, and a label above a full-bleed stripe
	// would frame it back into a widget. The per-segment labels beneath name the classes.
	//
	// Derivation is shared with the donut/bars (charts/percentages.ts): largest-remainder
	// percentages that sum to exactly 100, negatives excluded from the geometry (a segment of
	// negative width is not a smaller segment) and disclosed in the footnote.
	let {
		data,
		total
	}: {
		data: AllocationSlice[];
		/** Portfolio value — when given, the excluded-negatives footnote can carry a £ figure. */
		total?: number;
	} = $props();

	const display = $derived(allocationDisplay(data, total));
	const slices = $derived(display.slices);

	// A segment earns its under-band label from 10% — narrower and the label outruns the segment
	// it names, which mis-attributes it to the neighbour. Smaller slices collapse into the
	// trailing "+N more" legend line instead of being silently dropped.
	const LABEL_MIN_PCT = 10;
	const collapsed = $derived(slices.filter((s) => s.pct < LABEL_MIN_PCT));

	// One accessible sentence for the whole band — the segments are presentational widths.
	const summary = $derived(
		`Asset allocation: ${slices.map((s) => `${s.label} ${s.pct}%`).join(', ')}`
	);
</script>

<div class="@container flex h-full w-full flex-col justify-center">
	{#if slices.length === 0}
		<EmptyState
			icon={ChartPieIcon}
			title="Your allocation will appear here"
			description="Once your money is invested, you’ll see how it is spread across asset classes."
			compact
			chromeless
		/>
	{:else}
		<!-- The band. flex-grow (not width) sizes the segments, so the hairline gaps between them
		     never push the last segment past the rounded clip. -->
		<div
			class="flex h-12 w-full gap-px overflow-hidden rounded-full"
			role="img"
			aria-label={summary}
		>
			{#each slices as s (s.label)}
				<div style="flex:{s.pct} {s.pct} 0%;background:{s.color}"></div>
			{/each}
		</div>

		<!-- Wide containers: labels aligned UNDER their segments (the same flex proportions, so a
		     label sits beneath the colour it names); slices under the threshold keep their spacer
		     cell but collapse into the trailing legend line. -->
		<div class="mt-2 hidden gap-px @md:flex" aria-hidden="true">
			{#each slices as s (s.label)}
				<div class="min-w-0" style="flex:{s.pct} {s.pct} 0%">
					{#if s.pct >= LABEL_MIN_PCT}
						<p class="truncate text-sm font-medium">{s.label}</p>
						<p class="text-muted-foreground text-xs tabular-nums">{s.pct}%</p>
					{/if}
				</div>
			{/each}
		</div>
		{#if collapsed.length > 0}
			<p
				class="text-muted-foreground mt-1.5 hidden flex-wrap items-center gap-x-3 text-xs @md:flex"
			>
				<span>+{collapsed.length} more</span>
				{#each collapsed as s (s.label)}
					<!-- `min-w-0 max-w-full` + `truncate`: a legend entry is ONE flex item, so a long
					     instrument name ("...Developed World ex-UK Equity Index Fund") cannot wrap and ran
					     straight past the card's edge. The under-segment labels above were already
					     constrained this way; these two legends were not. The percentage is `shrink-0` so
					     the NAME gives way rather than the number the entry exists to report, and the full
					     name stays available on hover. -->
					<span class="flex min-w-0 max-w-full items-center gap-1.5" title={s.label}>
						<span class="size-1.5 shrink-0 rounded-full" style="background:{s.color}"></span>
						<span class="truncate">{s.label}</span>
						<span class="shrink-0 tabular-nums">{s.pct}%</span>
					</span>
				{/each}
			</p>
		{/if}

		<!-- Narrow containers (the block in a slim span): under-segment labels would overlap, so
		     the whole legend wraps as one line instead — container-aware, both variants SSR'd. -->
		<p
			class="text-muted-foreground mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs @md:hidden"
		>
			{#each slices as s (s.label)}
				<!-- `min-w-0 max-w-full` + `truncate`: a legend entry is ONE flex item, so a long
				     instrument name ("...Developed World ex-UK Equity Index Fund") cannot wrap and ran
				     straight past the card's edge. The under-segment labels above were already
				     constrained this way; these two legends were not. The percentage is `shrink-0` so
				     the NAME gives way rather than the number the entry exists to report, and the full
				     name stays available on hover. -->
				<span class="flex min-w-0 max-w-full items-center gap-1.5" title={s.label}>
					<span class="size-1.5 shrink-0 rounded-full" style="background:{s.color}"></span>
					<span class="truncate">{s.label}</span>
					<span class="shrink-0 tabular-nums">{s.pct}%</span>
				</span>
			{/each}
		</p>

		{#if display.excludedAny}
			<p class="text-muted-foreground/80 mt-2 text-xs">
				Excludes negative balances{display.excludedAmount !== null
					? ` (−${formatGBP(Math.abs(display.excludedAmount))})`
					: ''}
			</p>
		{/if}
	{/if}
</div>
