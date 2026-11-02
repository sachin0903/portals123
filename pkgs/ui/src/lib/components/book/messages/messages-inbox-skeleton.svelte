<script lang="ts">
	/**
	 * The messages inbox while the directory is in flight — a 72-wide conversation list, the
	 * thread (tab strip + bubbles + composer) and the 80-wide rail, in the same frame. A table
	 * plus an activity rail was the previous placeholder, and that is not this surface at all.
	 */
	import { Skeleton } from '../../ui/skeleton/index.js';
	import { cn } from '../../../utils.js';

	const listRows = Array.from({ length: 10 }, (_, i) => i);
	const bands = ['Notes', 'People'] as const;
</script>

<div
	class="-m-4 flex h-[calc(100svh-var(--topbar-h,4rem))] flex-col gap-4 p-4 md:-m-6 md:p-6 lg:flex-row"
	role="status"
	aria-label="Loading"
>
	<aside
		class="bg-card flex min-h-0 flex-col overflow-hidden rounded-xl shadow-xs lg:w-72 lg:shrink-0"
	>
		<div class="px-4 py-3" aria-hidden="true">
			<Skeleton class="h-9 w-full" />
			<Skeleton class="mt-2 h-6 w-24 rounded-full" />
		</div>
		<div class="flex-1 overflow-hidden px-2 py-2">
			{#each listRows as row (row)}
				<div class="flex w-full items-center gap-3 rounded-lg px-2 py-2.5" aria-hidden="true">
					<Skeleton class="size-9 shrink-0 rounded-full" />
					<div class="min-w-0 flex-1 space-y-1.5">
						<Skeleton class="h-4 w-3/4" />
						<Skeleton class="h-3 w-full" />
					</div>
					<Skeleton class="h-3 w-8 shrink-0" />
				</div>
			{/each}
		</div>
	</aside>

	<div
		class="bg-card flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-xl shadow-xs lg:flex-row"
	>
		<section class="relative flex min-w-0 flex-1 flex-col">
			<div class="flex shrink-0 items-center gap-1 px-2 py-1.5" aria-hidden="true">
				<Skeleton class="h-7 w-16 rounded-md" />
				<Skeleton class="h-7 w-16 rounded-md" />
				<Skeleton class="ms-1 size-7 rounded-md" />
			</div>
			<!-- Bottom-aligned bubbles: the thread they stand in for grows from the composer. -->
			<div class="flex min-h-0 flex-1 flex-col overflow-hidden px-4 py-5" aria-hidden="true">
				<div class="mt-auto w-full space-y-5">
					{#each [0, 1, 2] as i (i)}
						<div class={cn('flex items-start gap-3', i === 1 && 'flex-row-reverse')}>
							<Skeleton class="size-7 shrink-0 rounded-full" />
							<div class={cn('flex min-w-0 flex-col gap-1.5', i === 1 && 'items-end')}>
								<Skeleton class="h-3 w-24" />
								<Skeleton class={cn('h-10 rounded-xl', i === 1 ? 'w-52' : 'w-64')} />
							</div>
						</div>
					{/each}
				</div>
			</div>
			<div class="shrink-0 p-4" aria-hidden="true">
				<div class="border-input rounded-xl border p-2 sm:p-3">
					<Skeleton class="min-h-12 w-full" />
					<div class="mt-1 flex items-center gap-2">
						<Skeleton class="size-8" />
						<Skeleton class="ml-auto size-9 rounded-full" />
					</div>
				</div>
			</div>
		</section>

		<aside
			class="flex min-h-0 w-full flex-col border-t lg:w-80 lg:shrink-0 lg:border-t-0 lg:border-l"
		>
			<section class="shrink-0 pb-2" aria-hidden="true">
				<div class="flex items-center gap-2.5 px-4 pt-3 pb-2.5">
					<Skeleton class="size-8 shrink-0 rounded-full" />
					<div class="min-w-0 flex-1 space-y-1.5">
						<Skeleton class="h-4 w-36" />
						<Skeleton class="h-3 w-24" />
					</div>
				</div>
			</section>
			<div class="min-h-0 flex-1 overflow-hidden">
				{#each bands as band (band)}
					<div class="flex w-full items-center gap-2 px-4 py-2.5" aria-hidden="true">
						<Skeleton class="size-4 shrink-0" />
						<span class="text-[0.8125rem] font-semibold">{band}</span>
					</div>
					<div class="space-y-2 px-4 pb-3" aria-hidden="true">
						<Skeleton class="h-3 w-full" />
						<Skeleton class="h-3 w-2/3" />
					</div>
				{/each}
			</div>
		</aside>
	</div>
</div>
