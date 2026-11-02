<script lang="ts">
	// The work queue above a transactions table: items that can't be filed away because someone
	// has to make a call on them.
	//
	// One line per item, and the line leads with the ACTION, not the data — "Categorise
	// contribution" is what the adviser is being asked to do, and it's the thing that will differ
	// once unmatched transfers and failed payments join the queue.
	//
	// Each line is a LINK into /contributions, not a drawer. Classifying a payment determines the
	// relief claimed and the allowance used, and it is normally worked in bulk — a drawer over a
	// transactions table loses the adviser's place on every item and gives them none of the
	// pipeline context. This strip's job is to say "there is work"; the workbench is where it
	// gets done. Being a real link also means it can be middle-clicked and bookmarked.
	import { Badge, Card, CardContent, formatDayMonth, Money } from '@graphene/ui';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
	import type { Transaction } from '@graphene/ui';

	let {
		items,
		/** Hide the client name when the panel is already inside one client's page. */
		showClient = true
	}: {
		items: Transaction[];
		showClient?: boolean;
	} = $props();

	/**
	 * How many lines the strip shows before it stops and offers the rest.
	 *
	 * SIX, because this is a NOTICE and not the work. A client with a monthly standing order and no
	 * categorisation for two years has twenty-odd items, and the strip rendered every one — pushing
	 * the client's own record off the screen. The count in the header already says how much there
	 * is; past the first handful the lines stop telling you anything new.
	 */
	const VISIBLE = 6;

	/**
	 * How many rows are on screen. Grows in steps of `VISIBLE` when the reader asks for more.
	 *
	 * CLIENT-SIDE, deliberately: this strip is handed an array the page has already loaded, so
	 * fetching would be inventing a round trip to re-read what is in memory.
	 */
	let limit = $state(VISIBLE);

	// Clamp when the queue SHRINKS. Classifying an item removes it, and a `limit` left at 18 against
	// four remaining items would keep the control on screen with nothing left to reveal.
	$effect(() => {
		if (limit > items.length) limit = Math.max(VISIBLE, items.length);
	});

	const shown = $derived(items.slice(0, limit));
	const hidden = $derived(Math.max(0, items.length - limit));
	/** The next step, so the button can say what it will actually do rather than "more". */
	const step = $derived(Math.min(VISIBLE, hidden));

	/**
	 * What the queue is asking for. One case today (an uncategorised pension payment in), but read
	 * per item so unmatched transfers and failed payments can join with their own instruction.
	 */
	function actionLabel(transaction: Transaction): string {
		return transaction.needs_categorisation ? 'Categorise contribution' : 'Review';
	}
</script>

{#if items.length > 0}
	<Card class="bg-warning-subtle/45">
		<details class="group" open={items.length <= 2}>
			<summary
				class="focus-visible:ring-ring flex cursor-pointer list-none items-center gap-2 rounded-lg px-4 py-3 focus-visible:ring-2 focus-visible:outline-none [&::-webkit-details-marker]:hidden"
			>
				<CircleAlertIcon class="text-warning size-4 shrink-0" />
				<span class="text-sm font-medium">
					{items.length}
					{items.length === 1 ? 'action needs' : 'actions need'} attention
				</span>
				<Badge variant="outline" class="ml-auto font-normal">{items.length}</Badge>
				<ChevronDownIcon
					class="text-muted-foreground size-4 shrink-0 transition-transform group-open:rotate-180"
				/>
			</summary>
			<CardContent class="space-y-2">
				<ul class="divide-border divide-y">
					{#each shown as t (t.uid)}
						<li>
							<a
								href="/contributions?focus={encodeURIComponent(t.uid)}"
								class="hover:bg-muted/40 focus-visible:ring-ring -mx-2 flex w-[calc(100%+1rem)] items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors focus-visible:ring-2 focus-visible:outline-none"
							>
								<span class="min-w-0 flex-1">
									<span class="block truncate text-xs font-medium">{actionLabel(t)}</span>
									<span class="text-muted-foreground block truncate text-[11px]">
										{#if showClient}{t.client_name} ·
										{/if}{t.account_name} · {formatDayMonth(t.date)}
									</span>
								</span>
								<Money
									amount={t.amount}
									currency={t.currency}
									class="shrink-0 text-xs font-medium"
								/>
								<ChevronRightIcon class="text-muted-foreground size-3.5 shrink-0" />
							</a>
						</li>
					{/each}
				</ul>
				{#if hidden > 0}
					<button
						type="button"
						onclick={() => (limit += VISIBLE)}
						class="text-warning-strong hover:bg-warning/10 focus-visible:ring-ring -mx-2 flex w-[calc(100%+1rem)] items-center justify-center gap-1 rounded-md px-2 py-2 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:outline-none"
					>
						Show {step} more <span class="text-muted-foreground">({hidden} left)</span>
						<ChevronDownIcon class="size-3.5 shrink-0" />
					</button>
				{/if}
				<div class="flex justify-end pt-1">
					<a
						href="/contributions"
						class="text-brand shrink-0 text-xs font-medium underline-offset-2 hover:underline"
					>
						Open workbench
					</a>
				</div>
			</CardContent>
		</details>
	</Card>
{/if}
