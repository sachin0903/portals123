<script lang="ts">
	import * as Card from '../ui/card/index.js';
	import Stat from '../data/stat.svelte';
	import { formatGBP } from '../../money.js';
	import WidgetHeader from './widget-header.svelte';
	import { accountsOf } from './provenance.js';
	import type { WrapperGroup } from './types.js';

	// The accounts 'summary' variant — the trust view: a 300-account book collapses to one figure
	// per product type plus the headline count and value, with the full listing a click away.
	// Groups come server-side (wrapper_groups) or derived from the wrappers slice
	// (deriveWrapperGroups in slices.ts) — this component just renders them. It carries the same
	// Card chrome as every sibling block: bare figures on the page background read as a rendering
	// error next to carded neighbours.
	//
	// The figures are `Stat`, NOT `StatTile`. `StatTile` is the CARDED tile, so using it here put a
	// card inside a card: each product type got its own bordered panel, and a block that is one
	// idea ("what this client holds, by wrapper") read as five separate ones stacked in a box. One
	// card, a list of metrics inside it — the nesting was the whole problem.
	let {
		groups,
		totalAccounts,
		awaitingValuation = null,
		showViewAll = true,
		viewAllHref = '/accounts',
		interactive = true
	}: {
		groups: WrapperGroup[];
		/** True account count (wrappers_total when the slice was summarised, else the slice length). */
		totalAccounts: number;
		/** Accounts still awaiting a valuation (slices.ts::awaitingValuation — the value === 0
		 * heuristic, computed only when the slice is the whole book). null/0 renders nothing:
		 * the caption is a factual exception line, never a fabricated claim. */
		awaitingValuation?: number | null;
		/** Hidden on the accounts page itself — a link to where you already are. */
		showViewAll?: boolean;
		/** Where "View all accounts" goes — the firm's accounts page, which need not be `/accounts`. */
		viewAllHref?: string;
		interactive?: boolean;
	} = $props();

	const totalValue = $derived(groups.reduce((sum, g) => sum + g.value, 0));

	// One string, built here rather than in the template, so the copy can't pick up
	// template-whitespace line breaks between the figure and its words.
	const awaitingCaption = $derived(
		awaitingValuation != null && awaitingValuation > 0
			? `${accountsOf(awaitingValuation)} awaiting valuation`
			: null
	);
</script>

<Card.Root glass class="h-full">
	<!-- The one widget heading (widget-header.svelte). Its second item is DATA — the count and
	     the total — not a sentence; the awaiting line is the exception the count quietly
	     includes, only ever rendered when the caller could count honestly (whole-book slice). -->
	<WidgetHeader
		title="Accounts"
		stamp={accountsOf(totalAccounts)}
		stampAmount={totalValue}
		note={awaitingCaption}
		href={showViewAll ? viewAllHref : null}
		{interactive}
	/>
	<Card.Content>
		<!-- Columns key off the CONTAINER width (this block's grid span), not the viewport — a
		     summary in a half-width block on a desktop gets two columns, not three cramped ones.
		     `divide-*` rather than gaps and borders: one hairline between figures is what makes
		     this read as a list, where a border around each made it read as five cards. -->
		<div class="@container">
			<dl
				class="divide-border/60 @lg:grid-cols-2 @lg:divide-y-0 @4xl:grid-cols-3 grid grid-cols-1 divide-y"
			>
				{#each groups as group (group.product_type)}
					<div
						class="@lg:border-border/60 @lg:not-first:border-l py-3 first:pt-0 last:pb-0 @lg:px-4 @lg:py-0 @lg:first:pl-0 @lg:last:pr-0"
					>
						<Stat
							label={group.product_type}
							value={formatGBP(group.value)}
							sub={accountsOf(group.count)}
						/>
					</div>
				{/each}
			</dl>
		</div>
	</Card.Content>
</Card.Root>
