<script lang="ts" module>
	import type { Wrapper } from './types.js';

	/**
	 * Deck each-key. The href alone (or the bare type as its stand-in) is NOT unique — a family
	 * with two ISAs and two JISAs and no hrefs used to hand Svelte duplicate keys, which throws
	 * and blanked the whole block (and stack is the auto pick for ≤6 accounts, so that was the
	 * default family view). The index disambiguates repeats; the href/type prefix still lets a
	 * reorder of distinct cards move state with the card.
	 */
	export function cardKey(w: Wrapper, i: number): string {
		return `${w.href ?? w.type}-${i}`;
	}

	/** Fallback card link for a wrapper without an href. Real product types are free text, so
	 * the path segment must be encoded — "Offshore Bond" would otherwise emit a broken URL. */
	export function fallbackHref(type: string): string {
		return `/accounts/${encodeURIComponent(type)}`;
	}

	/** Whether a click should expand the deck instead of navigating: only a genuinely stacked
	 * deck (2+ cards) has anything to fan open — a "deck" of one must navigate on first click. */
	export function interceptsClick(count: number, expanded: boolean): boolean {
		return count > 1 && !expanded;
	}
</script>

<script lang="ts">
	import Sparkline from '../charts/sparkline.svelte';
	import { accountFace } from '../../categorical.js';
	import { useCardDesigns } from '../../card-designs.js';
	import { changeDirection } from './periods.js';
	import { groupLabel, groupWrappers, type GroupAxis } from './grouping.js';
	import { Money } from '../ui/money/index.js';
	import { formatGBP, formatPct, formatPctPlain, sanePct } from '../../money.js';
	import { cn } from '../../utils.js';

	let {
		wrappers,
		groupBy = 'none',
		showSparklines = true
	}: {
		wrappers: Wrapper[];
		/** The block's group_by setting. The deck can't afford real section headers (they'd break
		 * the overlap mechanics), so grouping here is the cheap form: the deck is re-ordered
		 * grouped-adjacent (stable within each group) and each card wears its group as a tiny
		 * eyebrow on the badge row. */
		groupBy?: GroupAxis;
		/** False hides the trend sparkline zone — the detail row shows the allowance bar or
		 * nothing (the zone keeps its fixed height so the overlap cut stays in the gap). */
		showSparklines?: boolean;
	} = $props();

	// Grouped-adjacent ordering via groupWrappers (first-appearance group order, original order
	// within a group) — a stable permutation, so cardKey's index disambiguation still holds.
	const deck = $derived(
		groupBy === 'none' ? wrappers : groupWrappers(wrappers, groupBy).flatMap((s) => s.wrappers)
	);

	// Click to fan the deck open; a second click on a card then navigates to it.
	let expanded = $state(false);
	const expandable = $derived(wrappers.length > 1);
	function onCardClick(e: MouseEvent) {
		if (interceptsClick(wrappers.length, expanded)) {
			e.preventDefault();
			expanded = true;
		}
	}

	/**
	 * Per-wrapper card face: the product type's FINISH (`lib/categorical.ts`) — the firm's card
	 * design for that type, else the product default. One map, shared with the chip cluster and the
	 * account cards, so one account wears one face wherever it is painted.
	 */
	const cardDesigns = useCardDesigns();
	const faceFor = (type: string) => accountFace(type, cardDesigns());

	const allowancePct = (w: Wrapper) =>
		w.allowanceLimit
			? Math.min(100, Math.round(((w.allowanceUsed ?? 0) / w.allowanceLimit) * 100))
			: null;
</script>

<!--
	Monzo-style deck. Collapsed, each card shows its summary (type / value / name);
	the detail zone (allowance or sparkline) is the card's FOOT — it bleeds past the padding to
	the card's bottom and side edges, so a sparkline rises from the bottom edge and runs edge to
	edge rather than sitting in a box inside the card. It is a fixed-height zone the next card
	overlaps exactly, so the cut always lands in the gap above it — never through text.
	Click fans the deck open. Overlap = gap (0.75rem) + detail (2.5rem) = 3.25rem.
-->
<div role="group" aria-label="Accounts">
	{#each deck as w, i (cardKey(w, i))}
		{@const pct = allowancePct(w)}
		{@const gainPct = sanePct(w.gainPct)}
		{@const dir = gainPct === null ? null : changeDirection(gainPct)}
		<a
			href={w.href ?? fallbackHref(w.type)}
			aria-label="{w.name} — {formatGBP(w.value)}{expandable && !expanded
				? '. Expands the account stack.'
				: ''}"
			onclick={onCardClick}
			aria-expanded={expandable ? expanded : undefined}
			class={cn(
				// `hero-surface` carries the ink foreground, the raised shadow and the overflow clip as
				// tokens (--hero-foreground / --hero-shadow); the per-type face below overrides its
				// background, and a pale finish re-points the ink tokens on the card itself. That removed a hardcoded oklch shadow and a literal white ink class
				// (naming the class here would put it back — Tailwind scans comments too).
				// focus-visible:z-10 lifts the focused card above the one overlapping it (later cards
				// paint on top in DOM order), so the keyboard focus ring is never buried in the deck.
				'hero-surface block rounded-xl p-4 transition-[margin] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] focus-visible:z-10 motion-reduce:transition-none',
				i > 0 && !expanded && '-mt-[3.25rem]',
				i > 0 && expanded && 'mt-2.5'
			)}
			style={faceFor(w.type)}
		>
			<!-- specular sheen — the glass highlight token, so it dims with the sheen strength a tenant
			     (or the sharp preset) sets rather than always painting a fixed white glint. -->
			<div
				class="pointer-events-none absolute inset-0"
				style="background-image: radial-gradient(120% 80% at 0% 0%, color-mix(in oklch, var(--glass-highlight) 32%, transparent), transparent 58%);"
			></div>

			<!-- summary — always visible -->
			<div class="relative">
				<div class="flex items-center justify-between gap-3">
					<div class="flex items-center gap-2">
						<span
							class="bg-[var(--hero-foreground)]/15 rounded-full px-2.5 py-0.5 text-xs font-semibold tracking-wide backdrop-blur-sm"
						>
							{w.type}
						</span>
						{#if w.delegated}
							<span
								class="rounded-full px-2 py-0.5 text-[0.65rem] font-medium text-[var(--hero-foreground)]/80 ring-1 ring-[var(--hero-foreground)]/25 ring-inset"
							>
								Delegated
							</span>
						{/if}
						{#if groupBy !== 'none'}
							{@const eyebrow = groupLabel(w, groupBy)}
							<!-- The card's group, as a tiny eyebrow. Skipped when it would only repeat the
							     type badge beside it (the wrapper axis's usual case) — the eyebrow earns
							     its place on the owner axis and on the fallback buckets. -->
							{#if eyebrow !== w.type}
								<span
									class="min-w-0 truncate text-[0.6rem] font-semibold tracking-[0.08em] text-[var(--hero-foreground)]/60 uppercase"
								>
									{eyebrow}
								</span>
							{/if}
						{/if}
					</div>
					<span
						class={cn(
							'text-sm font-medium tabular-nums',
							// The on-hero gain/loss tokens, not a pair of fixed pastel palette
							// classes: --hero-positive
							// and --hero-negative are --positive/--negative lifted toward white so they stay
							// legible on an ink face, and they re-theme with the firm's tokens.
							// sanePct guards junk cost-basis data: an implausible figure (+1,287.0%)
							// renders as an uncoloured em dash rather than as a number. Zero is its own
							// state (changeDirection): a flat account rendered green "+0.0%" claims a
							// gain that never happened — same treatment as the cards and table variants.
							dir === 'up' && 'text-[var(--hero-positive)]',
							dir === 'down' && 'text-[var(--hero-negative)]',
							(dir === 'flat' || dir === null) && 'text-[var(--hero-foreground)]/55'
						)}
					>
						{dir === null ? '—' : dir === 'flat' ? formatPctPlain(0) : formatPct(gainPct ?? 0)}
					</span>
				</div>
				<p class="mt-1.5 text-xl font-semibold tracking-tight tabular-nums">
					<Money amount={w.value} minorClass="text-[var(--hero-foreground)]/55" />
				</p>
				<p class="truncate text-sm">
					<!-- Inherits --hero-foreground from `hero-surface`; no literal white needed. -->
					<!-- The separator only earns its place between two names: an account with no owner
					     (a sole holder, or a provider that never sent one) printed as "· Max Oakden GIA". -->
					{#if w.owner}
						<span class="font-medium">{w.owner}</span>
						<span class="text-[var(--hero-foreground)]/55">· {w.name}</span>
					{:else}
						<span class="text-[var(--hero-foreground)]/80">{w.name}</span>
					{/if}
				</p>
			</div>

			<!-- detail — the card's foot: a fixed 2.5rem zone bleeding to the card's bottom and side
			     edges (the -m* undo `p-4`), tucked under the next card until expanded. -->
			<div class="relative mt-3 -mx-4 -mb-4 h-10">
				{#if pct !== null}
					<div class="flex h-full flex-col justify-end px-4 pb-3.5">
						<div class="flex justify-between text-[0.7rem] text-[var(--hero-foreground)]/70">
							<span>{w.type} allowance</span>
							<span>{pct}%</span>
						</div>
						<div class="bg-[var(--hero-foreground)]/20 mt-1 h-1.5 overflow-hidden rounded-full">
							<div
								class="bg-[var(--hero-foreground)]/80 h-full rounded-full"
								style="width:{pct}%"
							></div>
						</div>
					</div>
				{:else if showSparklines && w.trend.length > 1}
					<!-- Coloured by the account's direction, like the figure above it: the line IS the
					     trend, so a falling account draws in the loss tone. Flat/unknown stays neutral. -->
					<div
						class={cn(
							'absolute inset-0',
							dir === 'up' && 'text-[var(--hero-positive)]',
							dir === 'down' && 'text-[var(--hero-negative)]',
							(dir === 'flat' || dir === null) && 'text-[var(--hero-foreground)]/80'
						)}
					>
						<Sparkline data={w.trend} height={40} bleed class="h-full w-full" />
					</div>
				{/if}
				<!-- show_sparklines off with no allowance bar: the zone stays EMPTY but keeps its
				     fixed height, so the deck's overlap cut still lands in the gap. -->
			</div>
		</a>
	{/each}
</div>
