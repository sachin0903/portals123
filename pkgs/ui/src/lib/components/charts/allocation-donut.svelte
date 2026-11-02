<script lang="ts" module>
	export interface AllocationSlice {
		label: string;
		value: number;
		/** Any CSS colour, e.g. 'var(--brand)'. */
		color: string;
	}
</script>

<script lang="ts">
	import { pie as d3pie, arc as d3arc, type PieArcDatum } from 'd3-shape';
	import { Tween, prefersReducedMotion } from 'svelte/motion';
	import { cubicOut } from 'svelte/easing';
	import { allocationDisplay, type AllocationDisplaySlice } from './percentages.js';
	import { Money } from '../ui/money/index.js';
	import { formatGBP, formatGBPCompact } from '../../money.js';
	import { cn } from '../../utils.js';

	let {
		data,
		/** Portfolio value — when given, slices show £ amounts and the centre shows the total. */
		total,
		size = 200,
		class: className,
		interactive = true
	}: {
		data: AllocationSlice[];
		total?: number;
		size?: number;
		class?: string;
		/** False renders the final, inert state — no draw-in, legend rows disabled (previews). */
		interactive?: boolean;
	} = $props();

	let active = $state<number | null>(null);
	let root: HTMLDivElement | undefined = $state();
	// A click pins a slice so the centre keeps its £ figure. Hover never sticks on a touch
	// screen, and below @md the legend no longer prints the amount, so the ring is the only
	// place it lives. Hover still previews while nothing is pinned; a second click, or a
	// press outside the block, returns the centre to the total.
	let pinned = $state<number | null>(null);

	const radius = $derived(size / 2);

	// Shared with the bars variant (charts/percentages.ts): percentages are largest-remainder
	// rounded so the legend always sums to 100, and NEGATIVE slices (overdrawn cash is real data)
	// are excluded from the geometry — d3pie fed a negative value emits overlapping/garbage arcs —
	// and disclosed in the footnote below the legend instead.
	const display = $derived(allocationDisplay(data, total));
	const slices = $derived(display.slices);

	const arcs = $derived(
		d3pie<AllocationDisplaySlice>()
			.value((d) => d.value)
			.sort(null)(slices)
	);
	const arcGen = $derived(
		d3arc<PieArcDatum<AllocationDisplaySlice>>()
			.innerRadius(radius * 0.64)
			.outerRadius(radius)
			.padAngle(0.018)
			.cornerRadius(4)
	);

	// Sweep the donut in on mount (clockwise). SSR-safe by construction: the server renders the
	// FINAL state (`animating` only flips inside $effect, which never runs during SSR) so the
	// static HTML shows a complete donut and hydration has nothing to flash — the old
	// Tween-from-zero rendered EMPTY arcs into the SSR payload. Reduced-motion and inert
	// (preview) renders simply never start the sweep and stay on the final state.
	const sweep = new Tween(0, { easing: cubicOut });
	let animating = $state(false);
	$effect(() => {
		if (prefersReducedMotion.current || !interactive) return;
		animating = true;
		sweep.set(1, { duration: 650 });
	});
	const k = $derived(animating ? sweep.current : 1);

	// The centre label lives inside the ~0.64r hole — '£130,549,201.00' overflows it and clips.
	// From £10m the figure switches to compact notation ('£130.5m'); below that the exact figure
	// fits and stays exact, through Money. (Compacting only above the threshold is local to the
	// donut on purpose: tables and legends keep the full figure.)
	const COMPACT_FROM = 10_000_000;

	function show(i: number) {
		if (pinned !== null) return;
		active = i;
	}
	function clear() {
		if (pinned !== null) return;
		active = null;
	}
	function toggle(i: number) {
		pinned = pinned === i ? null : i;
		active = pinned;
	}

	$effect(() => {
		if (pinned === null) return;
		const onPointer = (event: PointerEvent) => {
			const target = event.target;
			if (root && target instanceof Node && root.contains(target)) return;
			pinned = null;
			active = null;
		};
		window.addEventListener('pointerdown', onPointer);
		return () => window.removeEventListener('pointerdown', onPointer);
	});
</script>

{#snippet centre(amount: number)}
	{#if Math.abs(amount) < COMPACT_FROM}<Money {amount} />{:else}{formatGBPCompact(amount)}{/if}
{/snippet}

<!-- justify-center + a max-width on the legend keep the pair together: with the legend on
     flex-1 alone, a full-width block let it swallow the whole row and pinned the donut hard
     left with ~900px of gap inside it.

     Laid out by the CARD's width (`@container`), not the viewport. Below @md (28rem) the pair
     would wrap — a 200px ring stacked on a 5-row list — so instead the ring sits BESIDE the
     legend, sized to the card: 42% of its width, never under 8rem or over 12rem, so a
     phone-width card gives the ring the room the legend does not need. The legend rows stay one line — label and percentage only; the
     £ amount is dropped, because it is the centre readout, shown on hover or on a tap that pins. -->
<div bind:this={root} class="@container w-full">
	<div
		class={cn(
			'flex w-full flex-wrap items-center justify-center gap-6 @max-md:flex-nowrap @max-md:gap-3',
			className
		)}
	>
		<!-- Fluid, capped at `size`: width tracks the container (max-width:100%) and the height
		     rides the square viewBox via aspect-ratio, so the donut shrinks inside a narrow span
		     instead of clipping at a fixed 200px. -->
		<div
			class="relative aspect-square w-full shrink-0 @max-md:w-[clamp(8rem,42cqw,12rem)]"
			style="max-width:min({size}px,100%)"
		>
			<svg
				viewBox="0 0 {size} {size}"
				class="block h-auto w-full"
				role="img"
				aria-label="Asset allocation"
			>
				<!-- The ring is purely presentational: the LEGEND is the one interactive surface (its
				     rows are real buttons with hover AND focus handling), so the arcs carry no role,
				     no tabindex and no handlers — the old per-path role="button" inside a role="img"
				     svg gave screen readers two conflicting stories about the same chart. -->
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

			<!-- The centre readout. In the compact ring the hole is ~77px, so the figure goes
			     compact ('£452.7k') and a size smaller; the legend beside it still carries the
			     exact amounts. -->
			<!-- Constrained to the hole (~0.64 of the ring) so a long label or a full £ figure
			     never paints over the arcs. -->
			<div
				class="pointer-events-none absolute inset-0 flex w-full flex-col items-center justify-center px-[22%] text-center"
			>
				{#if active !== null}
					{@const s = slices[active]}
					{#if s}
						<span class="text-muted-foreground w-full truncate text-xs @max-md:text-[0.65rem]">
							{s.label}
						</span>
						<!-- The compact ring's hole is ~80px, so the exact figure (the one the legend
						     no longer prints) steps down a size rather than switching to £213.5k. -->
						<span class="text-lg font-semibold tracking-tight tabular-nums @max-md:text-[0.7rem]">
							{#if s.amount !== null}
								<span class="@max-md:hidden">{@render centre(s.amount)}</span>
								<span class="hidden @max-md:inline"><Money amount={s.amount} /></span>
							{:else}
								{s.pct}%
							{/if}
						</span>
						<span class="text-muted-foreground text-xs tabular-nums @max-md:text-[0.65rem]">
							{s.pct}%
						</span>
					{/if}
				{:else}
					<span class="text-muted-foreground text-xs @max-md:text-[0.65rem]">
						{total != null ? 'Total' : 'Allocation'}
					</span>
					<span class="text-lg font-semibold tracking-tight tabular-nums @max-md:text-sm">
						{#if total != null}
							<span class="@max-md:hidden">{@render centre(total)}</span>
							<span class="hidden @max-md:inline">{formatGBPCompact(total)}</span>
						{:else}
							100%
						{/if}
					</span>
				{/if}
			</div>
		</div>

		<div class="max-w-sm min-w-44 flex-1 @max-md:min-w-0">
			<!-- 40px rows at a 4px gap: each row's `tap-target` extension adds the 2px either side
			     that makes a 44px hit area, and the gap is exactly what two extensions need not to
			     overlap. On a phone a tap PINS the slice (the £ figure lives in the ring there), so
			     the rows are real targets, not a legend. -->
			<ul class="space-y-1 text-sm">
				{#each slices as s, i (s.label)}
					<li>
						<!-- Hovering/focusing a row swaps the centre readout to this class (back to
						     Total on leave). Inert in previews: disabled, and no hover preview either. -->
						<button
							type="button"
							class="hover:bg-accent tap-target flex min-h-10 w-full items-center gap-2.5 rounded-lg px-2 text-left transition-colors disabled:pointer-events-none @max-md:gap-2 @max-md:px-1.5"
							style="opacity:{active === null || active === i ? 1 : 0.5}"
							aria-pressed={pinned === i}
							disabled={!interactive}
							onmouseenter={() => show(i)}
							onmouseleave={clear}
							onfocus={() => show(i)}
							onblur={clear}
							onclick={() => toggle(i)}
						>
							<span class="size-2.5 shrink-0 rounded-full" style="background:{s.color}"></span>
							<span class="text-muted-foreground min-w-0 flex-1 truncate">{s.label}</span>
							<!-- Below @md the £ amount lives only in the ring (hover or a pinning tap). -->
							{#if s.amount !== null}
								<span class="font-medium tabular-nums @max-md:hidden"
									><Money amount={s.amount} /></span
								>
							{/if}
							<span class="text-muted-foreground w-9 shrink-0 text-right tabular-nums @max-md:w-8">
								{s.pct}%
							</span>
						</button>
					</li>
				{/each}
			</ul>
			{#if display.excludedAny}
				<!-- The excluded-negative disclosure. The £ figure appears only when a portfolio total
				     anchors it (percentages.ts) — never fabricated from unitless weights. -->
				<p class="text-muted-foreground/80 mt-2 px-2 text-xs">
					Excludes negative balances{display.excludedAmount !== null
						? ` (−${formatGBP(Math.abs(display.excludedAmount))})`
						: ''}
				</p>
			{/if}
		</div>
	</div>
</div>
