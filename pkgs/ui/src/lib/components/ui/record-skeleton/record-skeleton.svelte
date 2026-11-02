<script lang="ts">
	import { Card, CardContent } from '../card/index.js';
	import { Skeleton } from '../skeleton/index.js';
	import { cn } from '../../../utils.js';

	/**
	 * The SHAPE of a record (or a page that is not a book listing) while its body is in flight.
	 *
	 * A header of the same weight as `PageHeader`, optional KPI tiles, and one or more glass cards
	 * of row-height bars. A short stack of generic bars was the previous instinct, and the page
	 * jumped when the real record replaced it. Shown on the FIRST load only: `Streamed` keeps the
	 * last answer on screen while a later navigation fetches.
	 */
	let {
		kpis = 0,
		cards = 2,
		header = true,
		rows = 6,
		fill = false,
		class: className
	}: {
		/** How many stat tiles to draw above the cards. Four matches the client/account overview. */
		kpis?: number;
		/** How many body cards to draw. */
		cards?: number;
		/** Draw page heading + description lines. On by default: record sections still have one. */
		header?: boolean;
		/** Row bars inside each card. */
		rows?: number;
		/** Fill the viewport under the topbar, the way a focused record's Messages pane does. */
		fill?: boolean;
		class?: string;
	} = $props();

	const keys = (count: number) => Array.from({ length: Math.max(count, 0) }, (_, i) => i);

	const FILL_FRAME =
		'-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6';
</script>

<div
	class={cn(fill ? FILL_FRAME : 'flex min-w-0 flex-col gap-5', className)}
	role="status"
	aria-label="Loading"
>
	{#if header}
		<div class="flex flex-col gap-2" aria-hidden="true">
			<Skeleton class="h-7 w-40" />
			<Skeleton class="h-4 w-80 max-w-full" />
		</div>
	{/if}

	{#if kpis > 0}
		<div
			class="grid shrink-0 gap-4"
			style:grid-template-columns="repeat({Math.min(kpis, 4)}, minmax(0, 1fr))"
			aria-hidden="true"
		>
			{#each keys(kpis) as tile (tile)}
				<Card glass class="py-5">
					<CardContent class="flex flex-col gap-2">
						<Skeleton class="h-3 w-16" />
						<Skeleton class="h-7 w-24" />
					</CardContent>
				</Card>
			{/each}
		</div>
	{/if}

	{#each keys(cards) as card (card)}
		<Card glass class="py-5">
			<CardContent class="flex flex-col gap-3" aria-hidden="true">
				<Skeleton class="h-4 w-32" />
				{#each keys(rows) as row (row)}
					<Skeleton class="h-5 w-full" />
				{/each}
			</CardContent>
		</Card>
	{/each}
</div>
