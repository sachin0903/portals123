<script lang="ts">
	// What a client OWNS, read the way they think about it: under the asset class each holding
	// makes up, in the allocation's own colours, one friendly row per fund — its mark, its name,
	// how many units at what price, what it is worth and how it has done.
	//
	// The alternative this replaced was the full holdings table: sortable headers, a search box
	// and two columns on a phone (name, value), so the units, the price, the growth and the
	// instrument mark were all cut away and what was left read as a back-office export. This is
	// the consumer layout instead — no controls, because a client's book is a dozen or two funds
	// and the useful order is already decided (biggest class first, biggest fund first within
	// it). The table remains for staff and for a firm that picks the 'table' variant.
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { Money } from '../ui/money/index.js';
	import { formatMoney, formatPct, formatPctPlain } from '../../money.js';
	import { series } from '../../categorical.js';
	import { cn } from '../../utils.js';
	import InstrumentIcon from './instrument-icon.svelte';
	import LookThroughButton from './look-through-button.svelte';
	import type { AllocationSlice, Holding } from './types.js';

	let {
		holdings,
		/** The donut's slices: their order and colours key the sections, so the list reads as the
		 *  chart opened up. Absent (an account record, which draws no donut) ⇒ sections by value,
		 *  marked from the categorical series scale. */
		allocations = [],
		/** The owning-account name in each row's detail line — right on a client's whole book,
		 *  where rows come from several accounts; pointless on one account's own page. */
		showWrapper = true,
		empty = 'No holdings yet.'
	}: {
		holdings: Holding[];
		allocations?: AllocationSlice[];
		showWrapper?: boolean;
		empty?: string;
	} = $props();

	const OTHER = 'Other';
	const total = $derived(holdings.reduce((s, h) => s + (h.value ?? 0), 0));
	const colourOf = $derived(new Map(allocations.map((a) => [a.label, a.color])));
	const rankOf = $derived(new Map(allocations.map((a, i) => [a.label, i])));

	const sections = $derived.by(() => {
		const by = new Map<string, Holding[]>();
		for (const h of holdings) {
			const key = h.assetClass?.trim() || OTHER;
			by.set(key, [...(by.get(key) ?? []), h]);
		}
		return [...by.entries()]
			.map(([label, rows]) => ({
				label,
				rows: [...rows].sort((a, b) => b.value - a.value),
				subtotal: rows.reduce((s, h) => s + (h.value ?? 0), 0)
			}))
			.sort(
				(a, b) =>
					(rankOf.get(a.label) ?? Infinity) - (rankOf.get(b.label) ?? Infinity) ||
					b.subtotal - a.subtotal
			);
	});

	const units = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 2 });
	const shareOf = (v: number) => (total > 0 ? v / total : 0);
	const code = (h: Holding) => h.currency || 'GBP';
	const keyOf = (h: Holding, i: number) => `${h.name}|${h.ticker ?? ''}|${h.wrapper}|${i}`;
</script>

{#snippet row(h: Holding)}
	{@const link = h.href ?? h.lookthroughHref}
	{@const opensLookthrough = !h.href && !!h.lookthroughHref}
	<div class="flex items-center gap-3 py-3">
		<InstrumentIcon name={h.name} ticker={h.ticker} size="md" />
		<div class="min-w-0 flex-1">
			<p class="truncate text-sm leading-snug font-medium">{h.name}</p>
			<p class="text-muted-foreground mt-0.5 truncate text-xs tabular-nums">
				{#if showWrapper && h.wrapper}{h.wrapper} ·
				{/if}{#if h.cash || h.units <= 0}Uninvested cash{:else}{units.format(h.units)} units · {formatMoney(
						h.price,
						code(h)
					)}{/if}
			</p>
		</div>
		<div class="shrink-0 text-right">
			<p class="text-sm font-semibold tabular-nums">
				<Money amount={h.value} currency={code(h)} />
			</p>
			<!-- "—", never "+0.0%": no cost basis is not a flat year (types.ts, `gainPct`). -->
			<p
				class={cn(
					'mt-0.5 text-xs font-medium tabular-nums',
					h.gainPct == null
						? 'text-muted-foreground'
						: h.gainPct >= 0
							? 'text-positive'
							: 'text-negative'
				)}
			>
				{h.gainPct == null ? '—' : formatPct(h.gainPct)}
			</p>
		</div>
		{#if opensLookthrough}
			<!-- The last thing on the row, where the chevron would be. The row IS the link (below),
			     so this is the button's look without a second anchor, hidden from the tree. -->
			<LookThroughButton class="shrink-0" />
		{:else if link}
			<ChevronRightIcon class="text-muted-foreground/60 -mr-1 size-4 shrink-0" aria-hidden="true" />
		{/if}
	</div>
{/snippet}

{#if holdings.length === 0}
	<p class="text-muted-foreground py-6 text-center text-sm">{empty}</p>
{:else}
	<div class="space-y-5">
		{#each sections as section, si (section.label)}
			<section aria-label={section.label}>
				<!-- The section is the donut's slice: its colour, its name, its share, its sum. -->
				<div class="flex items-center gap-2 pb-1">
					<span
						class="size-2.5 shrink-0 rounded-full"
						style="background:{colourOf.get(section.label) ?? series(si)}"
						aria-hidden="true"
					></span>
					<h3 class="min-w-0 flex-1 truncate text-sm font-semibold">
						{section.label}
						<span class="text-muted-foreground ms-1 font-normal tabular-nums">
							{formatPctPlain(shareOf(section.subtotal))}
						</span>
					</h3>
					<Money
						amount={section.subtotal}
						class="text-muted-foreground shrink-0 text-sm font-medium tabular-nums"
					/>
				</div>
				<ul class="divide-y">
					{#each section.rows as h, i (keyOf(h, i))}
						{@const link = h.href ?? h.lookthroughHref}
						<li>
							{#if link}
								<a
									href={link}
									class="hover:bg-accent/50 -mx-2 block rounded-lg px-2 transition-colors"
								>
									{@render row(h)}
								</a>
							{:else}
								{@render row(h)}
							{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/each}
	</div>
{/if}
