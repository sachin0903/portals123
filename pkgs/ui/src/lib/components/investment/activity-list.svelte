<script lang="ts" module>
	import type { Activity } from './types.js';

	/**
	 * Group consecutive entries under a date heading. Consecutive-only ON PURPOSE (a reduce, not
	 * a Map): the input is expected date-sorted, but an unsorted payload (A, B, A) must degrade
	 * to a repeated heading — merging non-adjacent days would silently reorder the ledger. The
	 * repeat is also why the template keys groups by INDEX: keying on `group.date` handed Svelte
	 * duplicate keys for exactly that input, which throws and blanked the whole block.
	 */
	export function groupByDate(activity: Activity[]): { date: string; items: Activity[] }[] {
		return activity.reduce<{ date: string; items: Activity[] }[]>((acc, item) => {
			const last = acc[acc.length - 1];
			if (last && last.date === item.date) last.items.push(item);
			else acc.push({ date: item.date, items: [item] });
			return acc;
		}, []);
	}
</script>

<script lang="ts">
	import type { Component } from 'svelte';
	import ArrowDownLeftIcon from '@lucide/svelte/icons/arrow-down-left';
	import ArrowDownToLineIcon from '@lucide/svelte/icons/arrow-down-to-line';
	import ArrowUpFromLineIcon from '@lucide/svelte/icons/arrow-up-from-line';
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import CoinsIcon from '@lucide/svelte/icons/coins';
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import PercentIcon from '@lucide/svelte/icons/percent';
	import ReceiptIcon from '@lucide/svelte/icons/receipt';
	import ScaleIcon from '@lucide/svelte/icons/scale';
	import SlidersHorizontalIcon from '@lucide/svelte/icons/sliders-horizontal';
	import TrendingUpIcon from '@lucide/svelte/icons/trending-up';
	import TrendingDownIcon from '@lucide/svelte/icons/trending-down';
	import { Money } from '../ui/money/index.js';
	import { activityDate, activityLabel, activityUnits } from './activity.js';
	import { cn } from '../../utils.js';

	let { activity }: { activity: Activity[] } = $props();

	// One icon per canonical kind (`ActivityType`). An unknown string gets the generic one.
	const icons: Record<string, Component> = {
		deposit: ArrowDownLeftIcon,
		withdrawal: ArrowUpRightIcon,
		dividend: CoinsIcon,
		interest: PercentIcon,
		fee: ReceiptIcon,
		tax: LandmarkIcon,
		buy: TrendingUpIcon,
		sell: TrendingDownIcon,
		transfer_in: ArrowDownToLineIcon,
		transfer_out: ArrowUpFromLineIcon,
		corporate_action: ScaleIcon,
		adjustment: SlidersHorizontalIcon
	};
	const groups = $derived(groupByDate(activity));

	// The wide columns are drawn only when SOME row has something to put in them — a feed of
	// cash movements has no units, and a column of blanks is the emptiness this layout exists
	// to fill, not a cure for it.
	const anyDetail = $derived(activity.some((a) => !!a.detail || !!a.isin));
	const anyUnits = $derived(activity.some((a) => activityUnits(a.quantity, a.price) !== null));
</script>

<!-- `@container`: the columns below key off the LIST's own width, not the viewport — the same
     rule the DataTable's `hideBelowWidth` follows. A six-column home block never reaches `@2xl`
     (42rem) and keeps the two-line row; the full-width activity page does, and the account, the
     custodian's words and a trade's units each take a column of their own instead of the row
     being a title and a figure with half a page of nothing between them. -->
<div class="@container space-y-5">
	<!-- Keyed by index, not date: unsorted input repeats a date group (see groupByDate). -->
	{#each groups as group, gi (gi)}
		<div>
			<p class="text-muted-foreground mb-1 text-xs font-medium">{activityDate(group.date)}</p>
			<ul class="divide-y">
				{#each group.items as item, i (item.description + item.amount + '-' + i)}
					{@const Icon = icons[item.type] ?? TrendingUpIcon}
					{@const credit = item.amount != null && item.amount > 0}
					{@const label = activityLabel(item.type)}
					{@const units = activityUnits(item.quantity, item.price, item.currency)}
					<li class="flex items-center gap-3 py-3">
						<span
							class={cn(
								'flex size-9 shrink-0 items-center justify-center rounded-full',
								credit
									? 'bg-positive-subtle text-positive-strong'
									: 'bg-muted text-muted-foreground'
							)}
						>
							<Icon class="size-4" />
						</span>
						<div class="min-w-0 flex-1">
							<p class="truncate font-medium">{item.description}</p>
							<p class="text-muted-foreground mt-0.5 flex items-center gap-1.5 text-xs">
								<!-- A cash movement names no instrument, so the API titles it by its kind —
								     "Payment" — and the kind under it read "Payment · Payment". -->
								{#if label !== item.description}<span>{label}</span>{/if}
								<!-- The account chip sits here on a narrow feed and moves to its own
								     column on a wide one; it is never drawn twice. -->
								{#if item.wrapper}
									<span
										class="bg-muted text-foreground/70 rounded px-1.5 py-px text-[0.65rem] font-medium @2xl:hidden"
									>
										{item.wrapper}
									</span>
								{/if}
							</p>
						</div>
						{#if anyDetail}
							<div class="hidden min-w-0 @2xl:block @2xl:w-[26%]">
								{#if item.detail}
									<p class="text-muted-foreground truncate text-sm" title={item.detail}>
										{item.detail}
									</p>
								{/if}
								{#if item.isin}
									<p
										class="text-muted-foreground/70 truncate font-mono text-[0.7rem] tracking-wide"
									>
										{item.isin}
									</p>
								{/if}
							</div>
						{/if}
						<div class="hidden min-w-0 @2xl:block @2xl:w-[20%]">
							{#if item.wrapper}
								<span
									class="bg-muted text-foreground/70 inline-block max-w-full truncate rounded px-1.5 py-px align-middle text-[0.65rem] font-medium"
									title={item.wrapper}
								>
									{item.wrapper}
								</span>
							{/if}
						</div>
						{#if anyUnits}
							<p
								class="text-muted-foreground hidden shrink-0 text-right text-xs tabular-nums @3xl:block @3xl:w-44"
							>
								{units ?? ''}
							</p>
						{/if}
						<!-- A debit is red, not merely uncoloured: tinting only the credit left a
						     column reading "green, green, plain, green", and the untinted row is taken
						     for another credit. Zero is neither and stays neutral. -->
						<Money
							amount={item.amount}
							currency={item.currency}
							signed
							tone="money"
							class="shrink-0 font-medium @2xl:inline-block @2xl:w-32 @2xl:text-right"
						/>
					</li>
				{/each}
			</ul>
		</div>
	{/each}
</div>
