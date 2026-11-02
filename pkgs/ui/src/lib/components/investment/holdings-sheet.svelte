<script lang="ts">
	import { Money } from '../ui/money/index.js';
	import { formatPct, gainTone, PCT_DIGITS } from '../../money.js';
	import { asAt } from './provenance.js';
	import { cn } from '../../utils.js';
	import type { Holding } from './types.js';

	// The holdings 'sheet' look — the statement sheet: a table straight on the page ground, in the
	// ledger register. A strong top rule under heavier small-caps column heads, hairline rows,
	// names at medium weight, money in the display numeral face. What a printed valuation
	// statement looks like, not a data grid in a card.
	let {
		holdings,
		asOf = null
	}: {
		holdings: Holding[];
		/** Valuation stamp (ISO date), rendered quietly right-aligned in the head ONLY when the
		 * payload carried one — never fabricated (provenance.ts). */
		asOf?: string | null;
	} = $props();

	const stamp = $derived(asAt(asOf));

	// The display numeral face with the ledger fallback chain (see accounts-ink): the ledger
	// finish points --money-display at the firm's display face; unset, figures stay on the body
	// face.
	const MONEY_FACE = 'font-family:var(--money-display, var(--font-sans))';

	// Head cell style: heavier small-caps than the muted DataTable heads — the sheet's hierarchy
	// is typographic, so the heads carry more ink.
	const HEAD =
		'text-foreground/70 pb-2 text-left text-[0.65rem] font-semibold tracking-[0.12em] uppercase';
</script>

<div class="@container">
	<!-- The look's title treatment: small-caps eyebrow left, the honest "as at" stamp right. -->
	<div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
		<p class="text-muted-foreground text-xs font-medium tracking-[0.18em] uppercase">Holdings</p>
		{#if stamp}
			<p class="text-muted-foreground text-xs">{stamp}</p>
		{/if}
	</div>

	{#if holdings.length === 0}
		<div class="border-border mt-3 border-t"></div>
		<p class="text-muted-foreground py-6 text-sm">Your funds and positions will appear here.</p>
	{:else}
		<!-- Columns shed by CONTAINER width (the block's own span), lowest priority first —
		     matching the carded tables' collapse order: name + value survive any span; price is
		     the first luxury back, then units, then account, then return. -->
		<table class="border-foreground/60 mt-3 w-full border-t-2" aria-label="Holdings">
			<thead>
				<tr class="border-border border-b">
					<th class={cn(HEAD, 'w-full pt-2.5')}>Investment</th>
					<th class={cn(HEAD, 'hidden pt-2.5 @md:table-cell')}>Account</th>
					<th class={cn(HEAD, 'hidden pt-2.5 text-right @2xl:table-cell')}>Units</th>
					<th class={cn(HEAD, 'hidden pt-2.5 text-right @3xl:table-cell')}>Price</th>
					<th class={cn(HEAD, 'pt-2.5 text-right')}>Value</th>
					<th class={cn(HEAD, 'hidden pt-2.5 text-right @lg:table-cell')}>Return</th>
				</tr>
			</thead>
			<tbody class="divide-border divide-y">
				<!-- Index-keyed: (name, wrapper) can repeat in a real book. -->
				{#each holdings as h, i (h.name + h.wrapper + '-' + i)}
					<tr>
						<td class="max-w-0 py-3 pe-4">
							<span class="block truncate text-sm font-medium">{h.name}</span>
							{#if h.ticker}
								<span class="text-muted-foreground block text-xs">{h.ticker}</span>
							{/if}
						</td>
						<!-- Capped and truncated, not `whitespace-nowrap` alone: an account named at
						     length ("TPOFFSHORE Bond …") set the column's width and the table ran past
						     the sheet. The title carries the full name. -->
						<td class="text-muted-foreground hidden py-3 pe-4 text-sm @md:table-cell">
							<span class="block max-w-48 truncate" title={h.wrapper}>{h.wrapper}</span>
						</td>
						<td class="hidden py-3 pe-4 text-right text-sm tabular-nums @2xl:table-cell"
							>{#if h.cash}<span class="text-muted-foreground">—</span
								>{:else}{h.units.toLocaleString('en-GB')}{/if}</td
						>
						<td
							class="hidden py-3 pe-4 text-right text-sm tabular-nums @3xl:table-cell"
							style={MONEY_FACE}
							>{#if h.cash}<span class="text-muted-foreground">—</span>{:else}<Money
									amount={h.price}
								/>{/if}</td
						>
						<td class="py-3 text-right text-sm font-medium tabular-nums" style={MONEY_FACE}>
							<Money amount={h.value} />
						</td>
						<td
							class={cn(
								'hidden py-3 ps-4 text-right text-sm tabular-nums @lg:table-cell',
								gainTone(h.gainPct, { digits: PCT_DIGITS })
							)}>{h.gainPct == null ? '—' : formatPct(h.gainPct)}</td
						>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>
