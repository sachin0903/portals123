<script lang="ts">
	/**
	 * The asset-class ring, and the legend beside it.
	 *
	 * ONE definition, because both records draw it: the client's overview for a whole portfolio,
	 * the account's for one wrapper. They were two copies of the same thirty-five lines of SVG, and
	 * the copies had already drifted — one printed a class's share to one decimal place in the
	 * foreground colour, the other to none in the muted one, so the same account read "62.5%" on
	 * one record and "63%" on the other.
	 *
	 * The SEGMENTS are computed by `allocationSegments`, which caps at five classes plus a real
	 * "Other" and owns the "the percentages add up to 100" claim. This component draws them.
	 */
	import type { AllocationSegment } from '../book/clients/allocation.js';
	import { WRAP_NAME } from '../../text-shell.js';
	import { cn } from '../../utils.js';
	import { Money } from '../ui/money/index.js';

	let {
		segments,
		value,
		amount,
		currency = 'GBP',
		size = 'sm'
	}: {
		segments: AllocationSegment[];
		/** Prints each class's money beside its share — the account record states it, the client's
		 *  rail has not the width. Absent ⇒ the share alone. */
		value?: (segment: AllocationSegment) => string;
		/** As `value`, but the figure renders through `Money` (pounds full weight, pence muted).
		 *  Wins over `value` when both are given. */
		amount?: (segment: AllocationSegment) => number | null;
		/** Currency for `amount`. */
		currency?: string | null;
		/** `sm` for the client's 22rem rail, `lg` for a card that owns half a record's width. */
		size?: 'sm' | 'lg';
	} = $props();

	/** Hover lights one class on the ring AND its legend row. The arcs are stroke-dashed
	 *  circles stacked on one hole, so `pointer-events: stroke` is what lets the pointer
	 *  hit the painted dash rather than the topmost full circle. */
	let hovered = $state<string | null>(null);

	function light(label: string): number {
		return hovered == null || hovered === label ? 1 : 0.35;
	}
</script>

<div
	class={cn(
		'flex',
		segments.length ? 'items-center gap-4' : 'flex-col items-center justify-center gap-2'
	)}
>
	<svg
		viewBox="0 0 42 42"
		class={cn('shrink-0', size === 'lg' ? 'size-24' : 'size-20')}
		role="img"
		aria-label="Asset allocation"
	>
		<circle cx="21" cy="21" r="15.915" fill="none" stroke="var(--muted)" stroke-width="5" />
		{#each segments as seg (seg.label)}
			<circle
				cx="21"
				cy="21"
				r="15.915"
				fill="none"
				stroke={seg.color}
				stroke-width={hovered === seg.label ? 6 : 5}
				stroke-dasharray="{seg.pct} {100 - seg.pct}"
				stroke-dashoffset={seg.offset}
				role="img"
				aria-label="{seg.label} {seg.pct.toFixed(1)}%"
				class="cursor-pointer transition-opacity"
				style="opacity: {light(seg.label)}; pointer-events: stroke"
				onmouseenter={() => (hovered = seg.label)}
				onmouseleave={() => (hovered = null)}
			>
				<title>{seg.label} {seg.pct.toFixed(1)}%</title>
			</circle>
		{/each}
	</svg>
	{#if segments.length}
		<!-- NAME WRAPS IN THE MIDDLE COLUMN. Share and amount sit on the right of each
		     row so a two-word class cannot be clipped to make room for the figures. -->
		<ul class="min-w-0 flex-1 space-y-1 text-xs">
			{#each segments as seg (seg.label)}
				<li>
					<button
						type="button"
						class={cn(
							'grid w-full grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-2 rounded-sm px-1 py-0.5 text-left transition-opacity',
							hovered === seg.label && 'bg-muted/70'
						)}
						style="opacity: {light(seg.label)}"
						onmouseenter={() => (hovered = seg.label)}
						onmouseleave={() => (hovered = null)}
						onfocus={() => (hovered = seg.label)}
						onblur={() => (hovered = null)}
					>
						<span class="size-2.5 shrink-0 self-center rounded-sm" style="background:{seg.color}"
						></span>
						<span class="min-w-0 {WRAP_NAME}">{seg.label}</span>
						<span class="shrink-0 text-right tabular-nums">
							{seg.pct.toFixed(1)}%
							{#if amount}
								<span class="text-muted-foreground mt-0.5 block">
									<Money amount={amount(seg)} currency={currency || 'GBP'} />
								</span>
							{:else if value}
								<span class="text-muted-foreground mt-0.5 block">{value(seg)}</span>
							{/if}
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{:else}
		<!-- THE RING IS THE EMPTY STATE. A title-and-paragraph beside it read as a
		     second widget fighting the hole for the same half of the card. -->
		<p class="text-muted-foreground text-center text-xs">No allocation yet</p>
	{/if}
</div>
