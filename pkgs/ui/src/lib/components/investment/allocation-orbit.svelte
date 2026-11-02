<script lang="ts">
	import ChartPieIcon from '@lucide/svelte/icons/chart-pie';
	import { pie as d3pie, arc as d3arc, type PieArcDatum } from 'd3-shape';
	import { Tween, prefersReducedMotion } from 'svelte/motion';
	import { cubicOut } from 'svelte/easing';
	import type { AllocationSlice } from '../charts/allocation-donut.svelte';
	import { allocationDisplay, type AllocationDisplaySlice } from '../charts/percentages.js';
	import { Money } from '../ui/money/index.js';
	import { formatGBP, formatGBPCompact } from '../../money.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';

	// The allocation 'orbit' look: the donut FREED from its card — larger, centred on the page
	// ground, the portfolio total set in the display face at its centre, and the legend as
	// wrapping pill chips beneath. Shares the donut's derivation (charts/percentages.ts:
	// largest-remainder percentages that sum to 100, negative slices excluded + disclosed) and its
	// interaction semantics (draw-in sweep on mount, hover/focus a chip → the centre swaps to that
	// class), but composes them into a different object: a free-standing instrument, not a card
	// content area. Kept as its own component rather than a prop on allocation-donut because the
	// legend geometry (chips vs rows) and the centre typography are the design, not a variant.
	let {
		data,
		total,
		interactive = true
	}: {
		data: AllocationSlice[];
		/** Portfolio value — when given, the centre shows the £ total and chips reveal £ amounts. */
		total?: number;
		/** False renders the final, inert state — no draw-in, chips disabled (previews). */
		interactive?: boolean;
	} = $props();

	let active = $state<number | null>(null);

	// Larger than the carded donut (200): the orbit owns its span, so it can afford the presence.
	const SIZE = 280;
	const radius = SIZE / 2;

	const display = $derived(allocationDisplay(data, total));
	const slices = $derived(display.slices);

	const arcs = $derived(
		d3pie<AllocationDisplaySlice>()
			.value((d) => d.value)
			.sort(null)(slices)
	);
	// A slightly thinner ring than the carded donut (0.7r vs 0.64r): the centre carries the
	// display-face total, which needs the room.
	const arcGen = $derived(
		d3arc<PieArcDatum<AllocationDisplaySlice>>()
			.innerRadius(radius * 0.7)
			.outerRadius(radius)
			.padAngle(0.018)
			.cornerRadius(4)
	);

	// The donut's draw-in, verbatim: SSR renders the FINAL state ($effect never runs server-side),
	// reduced-motion and inert renders never start the sweep.
	const sweep = new Tween(0, { easing: cubicOut });
	let animating = $state(false);
	$effect(() => {
		if (prefersReducedMotion.current || !interactive) return;
		animating = true;
		sweep.set(1, { duration: 650 });
	});
	const k = $derived(animating ? sweep.current : 1);

	// Compact centre figure above £10m — same threshold as the donut (the display-face centre
	// has even less room than the carded one's). Below it the figure is exact and renders through
	// Money; from it the figure is compact text ("£130.5m"), which has no pence to split.
	const COMPACT_FROM = 10_000_000;

	function show(i: number) {
		active = i;
	}
	function clear() {
		active = null;
	}
</script>

{#snippet centre(amount: number)}
	{#if Math.abs(amount) < COMPACT_FROM}<Money {amount} />{:else}{formatGBPCompact(amount)}{/if}
{/snippet}

<div class="flex h-full w-full flex-col items-center justify-center">
	<!-- The look's title treatment: a centred small-caps eyebrow — the ring is the object, the
	     label just names it quietly. -->
	<p class="text-muted-foreground text-xs font-medium tracking-[0.18em] uppercase">Allocation</p>

	{#if slices.length === 0}
		<EmptyState
			icon={ChartPieIcon}
			title="Your allocation will appear here"
			description="Once your money is invested, you’ll see how it is spread across asset classes."
			compact
			chromeless
			class="flex flex-1 flex-col justify-center"
		/>
	{:else}
		<div class="relative mt-4 aspect-square w-full" style="max-width:min({SIZE}px,100%)">
			<svg
				viewBox="0 0 {SIZE} {SIZE}"
				class="block h-auto w-full"
				role="img"
				aria-label="Asset allocation"
			>
				<!-- Presentational ring; the CHIPS are the interactive surface (real buttons), same
				     accessibility split as the carded donut. -->
				<g transform="translate({radius},{radius})">
					{#each arcs as slice, i (slice.data.label)}
						<path
							d={arcGen({
								...slice,
								startAngle: slice.startAngle * k,
								endAngle: slice.endAngle * k
							}) ?? ''}
							fill={slice.data.color}
							class="transition-opacity duration-200"
							style="opacity:{active === null || active === i ? 1 : 0.3}"
						/>
					{/each}
				</g>
			</svg>

			<div
				class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center"
			>
				{#if active !== null}
					{@const s = slices[active]}
					{#if s}
						<span class="text-muted-foreground text-xs">{s.label}</span>
						<span
							class="text-xl font-medium tracking-tight tabular-nums"
							style="font-family: var(--font-display, var(--font-sans))"
						>
							{#if s.amount === null}{s.pct}%{:else}{@render centre(s.amount)}{/if}
						</span>
						<span class="text-muted-foreground text-xs tabular-nums">{s.pct}%</span>
					{/if}
				{:else}
					<span class="text-muted-foreground text-xs">{total != null ? 'Total' : 'Allocation'}</span
					>
					<!-- The total in the display face — the same fallback chain as the ledger hero, so
					     an unthemed tenant gets the body face, never the UA serif. -->
					<span
						class="text-3xl font-medium tracking-tight tabular-nums"
						style="font-family: var(--font-display, var(--font-sans))"
					>
						{#if total != null}{@render centre(total)}{:else}100%{/if}
					</span>
				{/if}
			</div>
		</div>

		<!-- The legend as pill chips: swatch dot + label + %. Hover/focus swaps the centre readout
		     to that class (back on leave) — the donut's semantics in chip form. -->
		<ul class="mt-5 flex flex-wrap justify-center gap-2" aria-label="Asset classes">
			{#each slices as s, i (s.label)}
				<li>
					<button
						type="button"
						class="border-border hover:border-ring/40 flex items-center gap-2 rounded-full border px-3 py-1 text-sm transition-colors disabled:pointer-events-none"
						style="opacity:{active === null || active === i ? 1 : 0.5}"
						disabled={!interactive}
						onmouseenter={() => show(i)}
						onmouseleave={clear}
						onfocus={() => show(i)}
						onblur={clear}
					>
						<span class="size-2 shrink-0 rounded-full" style="background:{s.color}"></span>
						<span class="text-muted-foreground">{s.label}</span>
						<span class="tabular-nums">{s.pct}%</span>
					</button>
				</li>
			{/each}
		</ul>
		{#if display.excludedAny}
			<!-- The excluded-negative disclosure (percentages.ts): the £ figure only when a
			     portfolio total anchors it — never fabricated from unitless weights. -->
			<p class="text-muted-foreground/80 mt-3 text-xs">
				Excludes negative balances{display.excludedAmount !== null
					? ` (−${formatGBP(Math.abs(display.excludedAmount))})`
					: ''}
			</p>
		{/if}
	{/if}
</div>
