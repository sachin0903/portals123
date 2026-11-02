<script lang="ts">
	import { Money } from '../ui/money/index.js';
	import { groupByDate } from './activity-list.svelte';
	import { activityDate } from './activity.js';
	import type { Activity } from './types.js';

	// The 'timeline' activity look — a considered bank-app feed, not a log. Deliberately
	// chrome-less: no card, no row backgrounds, no type icons. A hairline spine runs down the
	// left, one dot node per DATE GROUP (groupByDate is imported from activity-list so both
	// looks share the exact consecutive-grouping semantics, including its unsorted-input
	// degradation — see the comment there), the date sits beside the node as a small-caps
	// eyebrow, and the entries hang right of the spine. Sits on both the card and ledger page
	// grounds because it paints nothing but border/brand ink.
	let { activity }: { activity: Activity[] } = $props();

	const groups = $derived(groupByDate(activity));
</script>

{#if groups.length > 0}
	<div class="relative">
		<!-- The spine: one continuous hairline behind the dots, inset from both ends so it reads
		     as connecting the nodes rather than bleeding off the block. left-[5px] centres it
		     under the 11px dots (2px ring + 7px core, centre at 5.5px). -->
		<div class="bg-border absolute inset-y-1 left-[5px] w-px" aria-hidden="true"></div>
		<!-- Keyed by index, not date: unsorted input repeats a date group (see groupByDate). -->
		<ol class="space-y-6">
			{#each groups as group, gi (gi)}
				<li class="relative ps-7">
					<!-- The node: a brand ring with a page-ground core, so it masks the spine and
					     stays legible on both finishes without introducing a fill colour. -->
					<span
						data-timeline-node
						class="border-brand bg-background absolute top-0.5 left-0 box-content size-[7px] rounded-full border-2"
						aria-hidden="true"
					></span>
					<p class="text-muted-foreground text-xs font-medium tracking-[0.16em] uppercase">
						{activityDate(group.date)}
					</p>
					<ul class="mt-2.5 space-y-3">
						{#each group.items as item, i (item.description + item.amount + '-' + i)}
							<li class="flex items-center gap-2.5">
								<p class="min-w-0 flex-1 truncate text-sm">{item.description}</p>
								{#if item.wrapper}
									<!-- Hairline chip, not a filled one — the only "surface" this look
									     allows itself is a border. -->
									<span class="text-muted-foreground shrink-0 rounded border px-1.5 py-px text-xs">
										{item.wrapper}
									</span>
								{/if}
								<Money
									amount={item.amount}
									currency={item.currency}
									signed
									tone="money"
									class="shrink-0 text-sm font-medium"
								/>
							</li>
						{/each}
					</ul>
				</li>
			{/each}
		</ol>
	</div>
{/if}
