<script lang="ts">
	/**
	 * The documents library while the book is in flight — the list card (toolbar and two-line `py-3`
	 * rows, no pager) and the Recent activity rail, in the same frame. `ListingSkeleton`'s rail is
	 * close, but its rows are a single `h-11` bar and the rail has no title, so the landing jumped.
	 */
	import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card/index.js';
	import { Skeleton } from '../../ui/skeleton/index.js';

	let {
		activity = true,
		actions = false
	}: {
		/** Draw the Recent activity rail. Off when the reader lacks `activity:read`. */
		activity?: boolean;
		/** Draw the listing kebab on the right of the toolbar. */
		actions?: boolean;
	} = $props();

	const rows = Array.from({ length: 12 }, (_, i) => i);
	const events = Array.from({ length: 6 }, (_, i) => i);
</script>

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
	role="status"
	aria-label="Loading"
>
	<div
		class="grid min-w-0 gap-5 md:min-h-0 md:flex-1 md:overflow-hidden {activity
			? 'lg:grid-cols-[minmax(0,1fr)_22rem]'
			: ''}"
	>
		{#if activity}
			<div class="min-h-0 lg:order-2 lg:flex lg:flex-col">
				<Card glass class="lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
					<CardHeader class="lg:shrink-0">
						<CardTitle class="text-base">Recent activity</CardTitle>
					</CardHeader>
					<CardContent class="flex flex-col gap-3 lg:min-h-0 lg:flex-1" aria-hidden="true">
						{#each events as item (item)}
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
		{/if}

		<div class="min-h-0 lg:order-1 lg:flex lg:min-h-0 lg:flex-col">
			<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
				<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
					<div class="flex shrink-0 flex-wrap items-center gap-2" aria-hidden="true">
						<Skeleton class="h-9 w-full sm:w-96" />
						<div class="ml-auto flex h-9 items-center gap-2">
							{#if actions}<Skeleton class="size-9" />{/if}
							<Skeleton class="size-9" />
						</div>
					</div>
					<div class="overflow-x-auto md:min-h-0 md:flex-1 md:overflow-y-auto">
						<div class="flex h-9 shrink-0 items-center gap-3 border-b px-2" aria-hidden="true">
							<Skeleton class="size-4 shrink-0" />
							<Skeleton class="h-3 flex-1" />
							<Skeleton class="h-3 w-20" />
							<Skeleton class="h-3 w-16" />
							<Skeleton class="h-3 w-20" />
							<Skeleton class="h-3 w-16" />
							<Skeleton class="h-3 w-12" />
							<Skeleton class="size-4 shrink-0" />
						</div>
						{#each rows as row (row)}
							<div class="flex items-center gap-3 border-b px-2 last:border-b-0" aria-hidden="true">
								<Skeleton class="size-4 shrink-0" />
								<div class="flex min-w-0 flex-1 items-center gap-2 py-3">
									<Skeleton class="size-4 shrink-0" />
									<Skeleton class="h-3 w-48 max-w-full" />
								</div>
								<Skeleton class="h-3 w-24" />
								<Skeleton class="h-3 w-16" />
								<Skeleton class="h-3 w-20" />
								<Skeleton class="h-3 w-16" />
								<Skeleton class="h-3 w-10" />
								<Skeleton class="size-4 shrink-0" />
							</div>
						{/each}
					</div>
				</CardContent>
			</Card>
		</div>
	</div>
</div>
