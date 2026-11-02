<script lang="ts">
	import { Money } from '../ui/money/index.js';
	import type { Holding } from './types.js';

	// The holdings 'bars' look: every holding a hairline-ruled row — name and value — with a
	// proportional value bar beneath, so the RELATIVE sizes read instantly (the biggest position
	// fills the track; everything else scales against it). A ranked reading of the book, not a
	// listing: no columns, no units/price, chrome-less — top rule + hairlines only.
	let { holdings }: { holdings: Holding[] } = $props();

	// The longest bar is the largest holding; a zero/negative "value" (an unpriced position)
	// renders an empty track rather than a lying sliver. Guard max ≤ 0: an all-zero book must not
	// divide by zero into NaN widths.
	const max = $derived(Math.max(0, ...holdings.map((h) => h.value)));
	const widthPct = (v: number): number => (max > 0 && v > 0 ? (v / max) * 100 : 0);

	// Display numeral face with the ledger fallback chain (see accounts-ink).
	const MONEY_FACE = 'font-family:var(--money-display, var(--font-sans))';
</script>

<div aria-label="Holdings" role="group">
	<!-- Title treatment: the register's small-caps eyebrow over the top rule. -->
	<p class="text-muted-foreground text-xs font-medium tracking-[0.18em] uppercase">Holdings</p>
	<div class="border-border mt-3 border-t"></div>

	{#if holdings.length === 0}
		<p class="text-muted-foreground py-6 text-sm">Your funds and positions will appear here.</p>
	{:else}
		<div class="divide-border divide-y">
			<!-- Index-keyed: (name, wrapper) can repeat in a real book. -->
			{#each holdings as h, i (h.name + h.wrapper + '-' + i)}
				<div class="py-3">
					<div class="flex items-baseline justify-between gap-4">
						<span class="min-w-0 truncate text-sm font-medium">
							{h.name}
							{#if h.ticker}<span class="text-muted-foreground text-xs font-normal">{h.ticker}</span
								>{/if}
						</span>
						<span class="shrink-0 text-sm font-medium tabular-nums" style={MONEY_FACE}>
							<Money amount={h.value} />
						</span>
					</div>
					<!-- The proportional bar: track in the brand softened to a wash, fill in the brand
					     itself — width is this holding's share of the LARGEST holding, so the column
					     of bars is a ranked chart the eye scans in one pass. -->
					<div
						class="mt-1.5 h-1.5 overflow-hidden rounded-full"
						style="background:color-mix(in oklab, var(--brand) 14%, transparent)"
					>
						<div
							class="h-full rounded-full"
							style="width:{widthPct(h.value)}%;background:var(--brand)"
						></div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
