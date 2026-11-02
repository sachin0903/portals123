<script lang="ts">
	import { Card, CardContent, CardHeader } from '../card/index.js';
	import { Skeleton } from '../skeleton/index.js';
	import { cn } from '../../../utils.js';

	/**
	 * The SHAPE of a listing while its rows are in flight — the same box the real listing occupies.
	 *
	 * Book listings fill the viewport under the topbar (`fill`, default): a glass Card, an `h-9`
	 * search at the toolbar's 24rem, the ghost filter funnel beside it (not a row of chips), optional kebab, a
	 * table whose rows are the height of a name-plus-avatar cell, and the pager strip. A short
	 * stack of `h-8` bars inside a padded card was the previous shape, and the page jumped when
	 * the real listing replaced it.
	 *
	 * Shown on the first load of a listing and again on every PAGE CUT (or sort, or filter pick):
	 * those ask for different rows, and a table that sits unchanged after the click reads as a pager
	 * that does not work. `Streamed` keeps the previous rows on screen only while the reader is
	 * typing — swapping the listing out would take the search box, and the caret in it, with it.
	 */
	let {
		rows,
		filters = 1,
		header = false,
		fill = true,
		actions = false,
		pager = fill,
		rail = false,
		columns = 7,
		spine = false,
		class: className
	}: {
		/** How many row bars to draw. Defaults to enough to fill the card; extras clip. */
		rows?: number;
		/** Present and non-zero ⇒ draw the Filter control. The count is ignored — the real toolbar
		 *  hides its filters behind one funnel, so N chips here would be the wrong shape. */
		filters?: number;
		/** Draw page heading + description lines. Off by default: staff listings have no heading
		 *  (the topbar crumb is the title). Pass true only for a surface that still has one. */
		header?: boolean;
		/** Fill the viewport under the topbar, the way the book listings do. Off for a card that
		 *  sits under a `PageHeader` (team, profiles) or a flowing inbox (tasks). */
		fill?: boolean;
		/** Draw the listing kebab on the right of the toolbar. */
		actions?: boolean;
		/** Draw the pager strip at the card foot. Defaults to on when the skeleton fills the page. */
		pager?: boolean;
		/** Draw the documents-style activity rail beside the table, on the live layouts' 22rem track. */
		rail?: boolean;
		/** How many header cells to hint. Visual weight only — the real columns arrive with the rows. */
		columns?: number;
		/** The listing's spine cell carries a SECOND line (the clients book: the name, then the date
		 *  of birth or the location), so its rows are `h-13`, not `h-11`, and the placeholder row
		 *  draws two bars. Pass it where the real rows are that shape, or the rows land eight pixels
		 *  taller each and the page below the first one moves when they arrive. */
		spine?: boolean;
		class?: string;
	} = $props();

	const keys = (count: number) => Array.from({ length: Math.max(count, 0) }, (_, i) => i);
	const rowCount = $derived(rows ?? (fill ? 16 : 8));
	const showFilter = $derived(filters > 0);
	const headerCells = $derived(keys(columns));

	const FILL_FRAME =
		'-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6';
</script>

{#snippet toolbar()}
	<div class="flex shrink-0 flex-wrap items-center gap-2">
		<!-- Same box as `ListingToolbar`: `h-9` and `sm:w-96` (`SEARCH_BOX_CLASS`), then the ghost
		     funnel hard against it. The named action, when there is one, stays the right-hand anchor. -->
		<Skeleton class="h-9 w-full sm:w-96" />
		{#if showFilter}<Skeleton class="size-9 shrink-0" />{/if}
		{#if actions}
			<div class="ml-auto flex h-9 items-center gap-2">
				<Skeleton class="h-9 w-24" />
			</div>
		{/if}
	</div>
{/snippet}

{#snippet table()}
	<!-- Header is `h-9` (`Table.Head`); body rows are `h-11` — `p-2` plus a `size-7` avatar — or
	     `h-13` when the spine cell stacks a second `text-xs` line under the name (`spine`). -->
	<div class="flex min-h-0 flex-col md:flex-1 md:overflow-hidden">
		<div class="flex h-9 shrink-0 items-center gap-3 border-b" aria-hidden="true">
			{#each headerCells as cell (cell)}
				<Skeleton class="h-3 flex-1" />
			{/each}
		</div>
		<div class="flex flex-col md:min-h-0 md:flex-1 md:overflow-hidden">
			{#each keys(rowCount) as row (row)}
				<div
					class={cn(
						'flex shrink-0 items-center border-b last:border-b-0',
						spine ? 'h-13 gap-2.5' : 'h-11'
					)}
					aria-hidden="true"
				>
					{#if spine}
						<Skeleton class="size-7 shrink-0 rounded-full" />
						<div class="flex min-w-0 flex-1 flex-col gap-1.5">
							<Skeleton class="h-3.5 w-full" />
							<Skeleton class="h-3 w-2/3" />
						</div>
					{:else}
						<Skeleton class="h-5 w-full" />
					{/if}
				</div>
			{/each}
		</div>
	</div>
{/snippet}

{#snippet pagerStrip()}
	<div class="-mx-4 flex shrink-0 items-center gap-3 border-t px-4 pt-3" aria-hidden="true">
		<Skeleton class="h-3 w-24" />
		<div class="ml-auto flex items-center gap-1">
			<Skeleton class="size-8" />
			<Skeleton class="size-8" />
			<Skeleton class="size-8" />
		</div>
		<Skeleton class="h-8 w-20" />
	</div>
{/snippet}

{#snippet book()}
	<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
		<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
			{@render toolbar()}
			{@render table()}
			{#if pager}
				{@render pagerStrip()}
			{/if}
		</CardContent>
	</Card>
{/snippet}

<!-- One `role="status"` for the whole thing (the bars are decorative), so a screen reader is told
     "Loading" once rather than once per bar. -->
<div
	class={cn(fill ? FILL_FRAME : 'flex min-h-0 flex-1 flex-col gap-4', className)}
	role="status"
	aria-label="Loading"
>
	{#if header}
		<div class="flex flex-col gap-2" aria-hidden="true">
			<Skeleton class="h-7 w-40" />
			<Skeleton class="h-4 w-80 max-w-full" />
		</div>
	{/if}

	{#if rail}
		<div
			class="grid min-w-0 gap-5 md:min-h-0 md:flex-1 md:overflow-hidden lg:grid-cols-[minmax(0,1fr)_22rem]"
		>
			<div class="min-h-0 lg:order-2 lg:flex lg:flex-col">
				<Card glass class="lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
					<CardHeader class="lg:shrink-0">
						<Skeleton class="h-4 w-32" />
					</CardHeader>
					<CardContent class="flex flex-col gap-3 lg:min-h-0 lg:flex-1" aria-hidden="true">
						{#each keys(6) as item (item)}
							<div class="flex items-start gap-3">
								<Skeleton class="mt-0.5 size-7 shrink-0 rounded-full" />
								<div class="flex min-w-0 flex-1 flex-col gap-1.5">
									<Skeleton class="h-3 w-3/4" />
									<Skeleton class="h-3 w-1/2" />
								</div>
							</div>
						{/each}
					</CardContent>
				</Card>
			</div>
			<div class="min-h-0 lg:order-1 lg:flex lg:min-h-0 lg:flex-col">{@render book()}</div>
		</div>
	{:else}
		{@render book()}
	{/if}
</div>
