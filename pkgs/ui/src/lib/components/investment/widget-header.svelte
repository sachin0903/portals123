<script lang="ts">
	import type { Snippet } from 'svelte';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { Money } from '../ui/money/index.js';
	import * as Card from '../ui/card/index.js';
	import { cn } from '../../utils.js';

	/**
	 * The one heading a client-portal widget wears: a single quiet row — the section's name on
	 * the left in the `SectionHeading` register (sentence case, 13px semibold), the way out on
	 * the right ("View all ›"), and nothing else.
	 *
	 * No description. Every card used to carry a bold title over a sentence that paraphrased it
	 * ("Allocation / Asset-class split across your portfolio"), which told the client nothing the
	 * title and the content under it had not, and read as written once and never looked at
	 * again. The second thing on the row, when there is one, is DATA — the valuation stamp, a
	 * count, a total — because data changes and so earns its place; a sentence does not.
	 *
	 * Built on Card.Header/Card.Title so the ledger finish's title register (the small-caps
	 * eyebrow via `--card-title-*` tokens) still reaches it, and the header padding stays the
	 * card's. `bare` renders the same row without the card chrome, for a block whose body is
	 * itself a set of surfaces (the account cards grid) and so has no card to put a header in —
	 * the heading still has to exist, or the page opens on an unlabelled cluster of tiles.
	 */
	let {
		title,
		stamp = null,
		stampAmount = null,
		href = null,
		action = 'View all',
		interactive = true,
		note = null,
		bare = false,
		actions,
		class: className
	}: {
		title: string;
		/** No Card.Header: a plain row, for a chrome-less block. */
		bare?: boolean;
		/** Data beside the title, muted: "as at 28 Sept 2026", "3 · £452,728.25". Never prose. */
		stamp?: string | null;
		/** A GBP total that follows `stamp` after a " · " — rendered through Money so its pence
		 *  step back like every other figure, which a plain string cannot do. */
		stampAmount?: number | null;
		/** The listing's own page. Null when the firm's page set has none — then no link at all. */
		href?: string | null;
		action?: string;
		/** False renders the link as inert text (the studio preview). */
		interactive?: boolean;
		/** A one-line muted exception under the row — again data ("2 awaiting valuation"). */
		note?: string | null;
		/** Anything else that belongs on the right of the row, before the link. */
		actions?: Snippet;
		class?: string;
	} = $props();
</script>

{#snippet stampText()}
	{stamp}{#if stampAmount != null}{stamp ? ' · ' : ''}<Money amount={stampAmount} />{/if}
{/snippet}

{#snippet heading()}
	<div class="min-w-0 flex-1">
		{#if bare}
			<!-- The same register as Card.Title, by hand: no card to size it. The row's `px-4` (below)
			     lines the text up with the content edge of the surfaces under it, which are `p-4`. -->
			<h2
				data-slot="card-title"
				class="flex min-w-0 items-baseline gap-x-2 text-[0.8125rem] leading-5 font-semibold"
			>
				<span class="truncate">{title}</span>
				{#if stamp || stampAmount != null}
					<span class="text-muted-foreground/70 truncate font-normal tabular-nums"
						>{@render stampText()}</span
					>
				{/if}
			</h2>
		{:else}
			<Card.Title
				class="flex min-w-0 items-baseline gap-x-2 text-[0.8125rem] leading-5 font-semibold"
			>
				<span class="truncate">{title}</span>
				{#if stamp || stampAmount != null}
					<span class="text-muted-foreground/70 truncate font-normal tabular-nums"
						>{@render stampText()}</span
					>
				{/if}
			</Card.Title>
		{/if}
		{#if note}
			<p class="text-muted-foreground/80 mt-0.5 text-xs">{note}</p>
		{/if}
	</div>
{/snippet}

{#snippet trailing()}
	{#if actions || href}
		<div class="flex shrink-0 items-center gap-2">
			{@render actions?.()}
			{#if href}
				{#if interactive}
					<a
						{href}
						class="text-muted-foreground hover:text-foreground tap-target -mr-1.5 inline-flex items-center gap-0.5 rounded-md py-1 pr-1 pl-1.5 text-xs font-medium whitespace-nowrap transition-colors"
					>
						{action}
						<ChevronRightIcon class="size-3.5" aria-hidden="true" />
					</a>
				{:else}
					<span
						class="text-muted-foreground inline-flex items-center gap-0.5 text-xs font-medium whitespace-nowrap"
					>
						{action}
						<ChevronRightIcon class="size-3.5" aria-hidden="true" />
					</span>
				{/if}
			{/if}
		</div>
	{/if}
{/snippet}

{#if bare}
	<div class={cn('flex flex-row items-center justify-between gap-3 px-4', className)}>
		{@render heading()}
		{@render trailing()}
	</div>
{:else}
	<Card.Header class={cn('flex flex-row items-center justify-between gap-3 space-y-0', className)}>
		{@render heading()}
		{@render trailing()}
	</Card.Header>
{/if}
