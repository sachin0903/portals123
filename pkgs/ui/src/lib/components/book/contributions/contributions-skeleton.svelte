<script lang="ts">
	/**
	 * The contributions workbench while the book is in flight — the one-row KPI strip and the
	 * full-width queue, in the same frame. No classify panel: it opens only when a row is picked, so
	 * first paint never has one. A generic listing card was the previous placeholder, and the page
	 * jumped when the strip landed.
	 */
	import { Card, CardContent } from '../../ui/card/index.js';
	import { Skeleton } from '../../ui/skeleton/index.js';
	import { METRIC_STAGES } from './status.js';

	const rows = Array.from({ length: 12 }, (_, i) => i);
	const columns = Array.from({ length: 7 }, (_, i) => i);

	const ACCENT: Record<string, string> = {
		awaiting_classification: 'border-t-warning',
		with_administrator: 'border-t-info',
		relief_claimed: 'border-t-success',
		relief_in_transit: 'border-t-success',
		relief_credited: 'border-t-success',
		not_lodged: 'border-t-success'
	};
</script>

<div
	class="-m-4 flex min-w-0 flex-col gap-4 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
	role="status"
	aria-label="Loading"
>
	<div class="grid shrink-0 grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-hidden="true">
		{#each METRIC_STAGES as stage (stage.key)}
			<Card
				glass
				class="min-w-0 gap-0 border-t-4 {ACCENT[stage.key] ?? 'border-t-brand'} px-3 py-2"
			>
				<div class="text-muted-foreground truncate text-[11px] leading-4 font-medium">
					{stage.label}
				</div>
				<Skeleton class="my-0.5 h-5 w-20" />
				<Skeleton class="mt-0.5 h-3 w-24" />
			</Card>
		{/each}
	</div>

	<div class="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden">
		<div class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
			<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
				<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
					<div class="flex shrink-0 flex-wrap items-center gap-2" aria-hidden="true">
						<Skeleton class="h-9 w-full sm:w-96" />
						<Skeleton class="ml-auto size-9" />
					</div>
					<div class="flex min-h-0 flex-col md:flex-1 md:overflow-hidden">
						<div class="flex h-9 shrink-0 items-center gap-3 border-b" aria-hidden="true">
							{#each columns as cell (cell)}
								<Skeleton class="h-3 flex-1" />
							{/each}
						</div>
						<div class="flex flex-col md:min-h-0 md:flex-1 md:overflow-hidden">
							{#each rows as row (row)}
								<div
									class="flex h-11 shrink-0 items-center border-b last:border-b-0"
									aria-hidden="true"
								>
									<Skeleton class="h-5 w-full" />
								</div>
							{/each}
						</div>
					</div>
					<div class="-mx-4 flex shrink-0 items-center gap-3 border-t px-4 pt-3" aria-hidden="true">
						<Skeleton class="h-3 w-24" />
						<div class="ml-auto flex items-center gap-1">
							<Skeleton class="size-8" />
							<Skeleton class="size-8" />
							<Skeleton class="size-8" />
						</div>
						<Skeleton class="h-8 w-20" />
					</div>
				</CardContent>
			</Card>
		</div>
	</div>
</div>
