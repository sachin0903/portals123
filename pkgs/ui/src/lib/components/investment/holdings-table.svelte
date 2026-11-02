<script lang="ts">
	// A client's or an account's holdings.
	//
	// SORTED, and sortable. This was a bare `{#each holdings}` over whatever order the loader
	// happened to pass — so the column headers were labels with no affordance, and "which of these
	// is the biggest position" could only be answered by reading every row. The wide variant is a
	// `DataTable` now, which brings the house sort affordances, the sticky header and the internal
	// scroll with it; the narrow variant keeps its stacked layout and follows the same sort.
	//
	// The default is VALUE, DESCENDING. A holdings table is read to find out where the money is, so
	// the biggest position belongs at the top — an alphabetical or feed-order list buries it.
	import { Badge } from '../ui/badge/index.js';
	import { CurrencyBadge } from '../ui/currency-badge/index.js';
	import { DataTable } from '../ui/data-table/index.js';
	import type { DataTableSort } from '../ui/data-table/index.js';
	import { sortRows } from '../ui/data-table/sort.js';
	import { formatMoney, formatPct, formatPctPlain, gainTone, PCT_DIGITS } from '../../money.js';
	import { Money } from '../ui/money/index.js';
	import { allocationShare } from './allocation.js';
	import { holdingColumns } from './holdings-view.js';
	import { cn } from '../../utils.js';
	import InstrumentIcon from './instrument-icon.svelte';
	import LookThroughButton from './look-through-button.svelte';
	import type { Holding } from './types.js';
	import { LINK_RECORD } from '../../text-shell.js';

	// Money (pounds full weight, pence smaller + muted) rather than formatGBP full-pence — the
	// sibling variants (accounts table/cards, the full holdings table) all render figures through
	// Money, and this compact look was the one hold-out.
	let {
		holdings,
		/**
		 * The owning-account pill on every row. Right on a CLIENT's holdings, where the rows come
		 * from several accounts and the pill is what tells them apart — and pointless on one
		 * ACCOUNT's own holdings band, where it repeats that account's name down the whole column
		 * and pushes the figures over for nothing.
		 */
		showWrapper = true,
		/**
		 * The total these holdings are a share OF, which turns the Allocation column on.
		 *
		 * Supplied rather than summed here, and off by default, because this table is routinely
		 * handed a slice: the account record pages 25 rows at a time and the client portal's widget
		 * shows the top five, so a weight computed from the rows in hand would be a weight of the
		 * page. A caller that knows the real total passes it; one that does not gets no column
		 * rather than a plausible wrong number (`allocation.ts`).
		 */
		allocationBase = null,
		/** Scroll the rows inside the table rather than the page (see DataTable's `scroll`). */
		scroll = false,
		/** Shown when there are no rows. The caller knows what "none" means here — an account with
		 *  no positions and a client with no book read differently. */
		empty = 'No holdings yet.',
		/**
		 * The active sort. BINDABLE, so a caller that offers an export link can carry today's order
		 * onto the file — this table's sort is local state with nothing in the URL, so without a
		 * binding the export has no way to learn it. Defaults to VALUE, DESCENDING; see the file
		 * banner for why.
		 */
		sort = $bindable<DataTableSort | null>({ key: 'value', dir: 'desc' })
	}: {
		holdings: Holding[];
		showWrapper?: boolean;
		allocationBase?: number | null;
		scroll?: boolean;
		empty?: string;
		sort?: DataTableSort | null;
	} = $props();

	const share = (h: Holding) => allocationShare(h.value, allocationBase);

	// Investment · Account · Units · Price · Value · Allocation · Return — the order of the
	// hand-rolled table this replaced, with Allocation added beside Value and only where a caller
	// supplies the base to divide by. Nothing is dropped and nothing collapses at a narrower width
	// that did not collapse before (the narrow case is the stacked variant below, which is why this
	// table needs no `hideBelow`). The one omission is `wrapper`, and only when the caller asks.
	// `auto`, not `fixed`: `wrapper` and `allocation` are conditional, so the column count varies.
	const columns = $derived(holdingColumns({ showWrapper, allocationBase }));

	// The stacked variant shows the same rows in the same order. It has no headers to click, so it
	// simply follows whatever the table's sort is (the default, until a wide viewer changes it) —
	// which is still a decided order rather than the feed's.
	const ordered = $derived(sortRows(holdings, columns, sort));

	/**
	 * One stable key per ROW OBJECT, assigned once per `holdings` change.
	 *
	 * Both `{#each}`es here are keyed, and DataTable's is keyed on `getKey(row)` alone — no index —
	 * so a key that repeats does not merely look wrong, it throws. Identity is name + ISIN +
	 * account, which the conformed feed makes unique (one row per account per asset); the counter
	 * is the belt for a feed that ever sends two rows a caller cannot tell apart. Keyed on the
	 * object rather than recomputed per render, so a re-sort moves rows instead of replacing them.
	 */
	const keys = $derived.by(() => {
		const map = new Map<Holding, string>();
		const seen = new Map<string, number>();
		for (const h of holdings) {
			const identity = `${h.name}|${h.ticker ?? ''}|${h.wrapper}`;
			const n = seen.get(identity) ?? 0;
			seen.set(identity, n + 1);
			map.set(h, n ? `${identity}#${n}` : identity);
		}
		return map;
	});
	const keyOf = (h: Holding) => keys.get(h) ?? h.name;
	const code = (h: Holding) => h.currency || 'GBP';
</script>

<!-- The stacked-list/table switch keys off this wrapper's CONTAINER width, not the viewport:
     what makes the six-column table unreadable is the component being ~350px wide, and that is
     just as true for a w=4 grid block on a desktop as for a phone. @2xl (672px) is roughly where
     units · price · value · return stop fighting for space. -->
<div
	class={cn(
		'@container',
		scroll && 'flex min-w-0 flex-col md:min-h-0 md:flex-1 md:overflow-hidden'
	)}
>
	<!-- Narrow: stacked rows -->
	<ul
		class={cn('divide-y @2xl:hidden', scroll && 'md:min-h-0 md:flex-1 md:overflow-y-auto')}
		aria-label="Holdings"
	>
		{#each ordered as h (keyOf(h))}
			<li class="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
				<InstrumentIcon name={h.name} ticker={h.ticker} />
				<div class="min-w-0 flex-1">
					<div class="flex items-center gap-2">
						{#if showWrapper}<Badge variant="outline" class="shrink-0" title={h.wrapper}
								>{h.wrapper}</Badge
							>{/if}
						{#if h.href}
							<a href={h.href} class="truncate font-medium {LINK_RECORD}">{h.name}</a>
						{:else}
							<span class="truncate font-medium">{h.name}</span>
						{/if}
					</div>
					<p class="text-muted-foreground mt-1 text-xs tabular-nums">
						{#if h.cash}Uninvested cash{:else}{h.units.toLocaleString('en-GB')} units · {formatMoney(
								h.price,
								code(h)
							)}{/if}
						{#if h.currency}
							<span class="text-muted-foreground"> {h.currency}</span>
						{/if}
						{#if share(h) != null}
							· {formatPctPlain(share(h) ?? 0)} of the total
						{/if}
					</p>
				</div>
				<div class="shrink-0 text-right">
					<p class="font-medium tabular-nums">
						<Money amount={h.value} currency={code(h)} />
					</p>
					<!-- "—", never "+0.0%": no cost basis is not a flat year (types.ts, `gainPct`). -->
					<p class={cn('text-xs tabular-nums', gainTone(h.gainPct, { digits: PCT_DIGITS }))}>
						{h.gainPct == null ? '—' : formatPct(h.gainPct)}
					</p>
					{#if h.lookthroughHref}
						<LookThroughButton href={h.lookthroughHref} class="mt-1.5" />
					{/if}
				</div>
			</li>
		{/each}
	</ul>

	<!-- Wide: full table -->
	<div
		class={cn(
			'hidden @2xl:block',
			scroll && '@2xl:flex min-w-0 flex-col md:min-h-0 md:flex-1 md:overflow-hidden'
		)}
	>
		<DataTable {columns} rows={holdings} bind:sort {scroll} getKey={keyOf} label="Holdings" {empty}>
			{#snippet cell(h, column)}
				{#if column.key === 'name'}
					<div class="flex items-center gap-2.5">
						<InstrumentIcon name={h.name} ticker={h.ticker} />
						<div class="min-w-0">
							{#if h.href}
								<a href={h.href} class="block truncate font-medium {LINK_RECORD}">{h.name}</a>
							{:else}
								<div class="truncate font-medium">{h.name}</div>
							{/if}
						</div>
					</div>
				{:else if column.key === 'wrapper'}
					<Badge variant="outline" class="font-normal" title={h.wrapper}>{h.wrapper}</Badge>
				{:else if column.key === 'isin' || column.key === 'sedol'}
					{@const id = h[column.key]}
					<span class={cn('text-muted-foreground', id && 'font-mono text-xs')}>{id || '—'}</span>
				{:else if column.key === 'units'}
					{#if h.cash}
						<span class="text-muted-foreground">—</span>
					{:else}
						<span class="tabular-nums">{h.units.toLocaleString('en-GB')}</span>
					{/if}
				{:else if column.key === 'currency'}
					{#if h.currency}
						<CurrencyBadge code={h.currency} />
					{:else}
						<span class="text-muted-foreground">—</span>
					{/if}
				{:else if column.key === 'price'}
					{#if h.cash}
						<span class="text-muted-foreground">—</span>
					{:else}
						<Money amount={h.price} currency={code(h)} />
					{/if}
				{:else if column.key === 'bookCost'}
					{#if h.bookCost == null}
						<span class="text-muted-foreground">—</span>
					{:else}
						<Money amount={h.bookCost} currency={code(h)} />
					{/if}
				{:else if column.key === 'value'}
					<Money amount={h.value} currency={code(h)} class="font-medium" />
				{:else if column.key === 'allocation'}
					{@const weight = share(h)}
					<!-- Unsigned (`formatPctPlain`): a weight is a proportion, and `formatPct`'s
					     always-on sign would put a `+` on every row and read as a gain. -->
					<span class="tabular-nums">{weight == null ? '—' : formatPctPlain(weight)}</span>
				{:else if column.key === 'gainPct'}
					<span class="whitespace-nowrap tabular-nums">
						{#if h.unrealisedGain != null}<Money
								amount={h.unrealisedGain}
								currency={code(h)}
								signed
								tone="money"
							/> ·{/if}
						<span class={gainTone(h.gainPct, { digits: PCT_DIGITS })}
							>{h.gainPct == null ? '—' : formatPct(h.gainPct)}</span
						>
					</span>
				{:else if column.key === 'lookthroughHref' && h.lookthroughHref}
					<LookThroughButton href={h.lookthroughHref} />
				{/if}
			{/snippet}
		</DataTable>
	</div>
</div>
