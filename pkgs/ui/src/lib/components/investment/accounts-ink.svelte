<script lang="ts">
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import { Money } from '../ui/money/index.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import { accountStatusChip } from './account-status.js';
	import { groupWrappers, type GroupAxis } from './grouping.js';
	import { fallbackHref } from './account-stack.svelte';
	import type { Wrapper } from './types.js';

	// The accounts 'ink' look — pure typography, the ledger hero's register
	// (portfolio-header-ledger.svelte): zero backgrounds, zero radius, hairline-ruled rows on the
	// page ground. Each row: a small-caps product-type prefix column, name with the owner set
	// small beneath it, and the value right-aligned in the display numeral face. This is the
	// accounts block's chrome-less signature — what 'auto' picks under the ledger finish.
	let {
		wrappers,
		groupBy = 'none'
	}: {
		wrappers: Wrapper[];
		/** 'wrapper'/'owner' breaks the register into sections: a heavier rule, a small-caps group
		 * label and a right-aligned subtotal per group (grouping.ts buckets). */
		groupBy?: GroupAxis;
	} = $props();

	const sections = $derived(groupBy === 'none' ? null : groupWrappers(wrappers, groupBy));

	// The display numeral face, with the ledger hero's exact fallback chain: --money-display is
	// set by the ledger finish (→ var(--font-display)); when a firm picks this look on a CARD
	// portal the token is unset, and the fallback keeps the figures on the body face rather than
	// dropping to the UA serif.
	const MONEY_FACE = 'font-family:var(--money-display, var(--font-sans))';
</script>

{#snippet row(w: Wrapper)}
	{@const chip = accountStatusChip(w.status)}
	<a
		href={w.href ?? fallbackHref(w.type)}
		class="group grid grid-cols-[3.5rem_minmax(0,1fr)_auto] items-baseline gap-x-4 py-3"
	>
		<span class="text-muted-foreground text-[0.65rem] font-semibold tracking-[0.12em] uppercase">
			{w.type}
		</span>
		<span class="min-w-0">
			<span class="block truncate text-sm font-medium group-hover:underline">
				{w.name}{#if w.delegated}<span class="text-muted-foreground font-normal">
						· delegated</span
					>{/if}{#if chip}<!-- The register's status marker is INK, not a badge — this look
					allows itself no fills. Lowercased into the row's own grammar, like
					`· delegated`; active accounts stay unmarked (account-status.ts).
				--><span
						class="text-muted-foreground font-normal lowercase"
						data-account-status
					>
						· {chip.label}</span
					>{/if}
			</span>
			<span class="text-muted-foreground block truncate text-xs">{w.owner}</span>
		</span>
		<span class="text-sm font-medium tabular-nums" style={MONEY_FACE}>
			<Money amount={w.value} />
		</span>
	</a>
{/snippet}

{#snippet register(rows: Wrapper[])}
	<!-- divide-y hairlines only — no background, no radius, no shadow. The ruled register. -->
	<div class="divide-border divide-y">
		{#each rows as w, i (w.href ?? `${w.type}·${w.owner}·${w.name}·${i}`)}
			{@render row(w)}
		{/each}
	</div>
{/snippet}

<div class="flex h-full flex-col" aria-label="Accounts" role="group">
	<!-- Block eyebrow + top rule, exactly the ledger hero's opening: this is the look's title
	     treatment — a small-caps eyebrow, never a card header. -->
	<p class="text-muted-foreground text-xs font-medium tracking-[0.18em] uppercase">Accounts</p>
	<div class="border-border mt-3 border-t"></div>

	{#if wrappers.length === 0}
		<EmptyState
			icon={WalletIcon}
			title="Your accounts will appear here"
			description="Once your accounts are open, you’ll see their values and details in this space."
			compact
			chromeless
			class="flex flex-1 flex-col justify-center"
		/>
	{:else if sections === null}
		{@render register(wrappers)}
	{:else}
		{#each sections as section (section.label)}
			<section>
				<!-- Group header: a HEAVIER rule than the row hairlines (the register's hierarchy is
				     drawn in rule weight, not boxes) + small-caps label with the subtotal engraved
				     right, in the same display face as the row values. -->
				<div
					class="border-foreground/25 mt-5 flex items-baseline justify-between gap-4 border-b-2 pb-1.5 first:mt-0"
				>
					<h3
						class="text-muted-foreground min-w-0 truncate text-xs font-semibold tracking-[0.12em] uppercase"
					>
						{section.label}
					</h3>
					<span class="shrink-0 text-sm font-medium tabular-nums" style={MONEY_FACE}>
						<Money amount={section.subtotal} />
					</span>
				</div>
				{@render register(section.wrappers)}
			</section>
		{/each}
	{/if}
</div>
