<script lang="ts">
	// Runs per day as columns, with last-run / next-scheduled beneath.
	//
	// Plain divs, not SVG: it's a fixed-bucket bar chart with no axes or interaction beyond a
	// tooltip, and the existing chart components (d3 scales, paths) would be more machinery than
	// the shape needs. Failed runs are stacked in the destructive colour so a bad night is visible
	// without reading the Runs list.
	import { Badge } from '@graphene/ui';
	import type { PlatformMetrics } from '../types';

	let { metrics }: { metrics: PlatformMetrics } = $props();

	const days = $derived(metrics.runsPerDay);
	const peak = $derived(Math.max(1, ...days.map((d) => d.runs)));
	const total = $derived(days.reduce((sum, d) => sum + d.runs, 0));

	const dayFormat = new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short' });
	const stampFormat = new Intl.DateTimeFormat('en-GB', {
		day: '2-digit',
		month: 'short',
		hour: '2-digit',
		minute: '2-digit'
	});
	const asStamp = (iso: string | null) => {
		if (!iso) return '—';
		const parsed = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z');
		return Number.isNaN(parsed.getTime()) ? '—' : stampFormat.format(parsed);
	};
</script>

<div class="space-y-2">
	<div class="flex items-baseline justify-between gap-2">
		<span class="text-2xl font-semibold tabular-nums">{total}</span>
		<span class="text-muted-foreground text-xs">last {days.length} days</span>
	</div>

	{#if days.length}
		<div class="flex h-16 items-end gap-0.5">
			{#each days as day (day.date)}
				{@const height = (day.runs / peak) * 100}
				{@const ok = day.runs - day.failed}
				<div
					class="group relative flex-1"
					title="{dayFormat.format(new Date(day.date))}: {day.runs} run{day.runs === 1
						? ''
						: 's'}{day.failed ? `, ${day.failed} failed` : ''}"
				>
					{#if day.runs === 0}
						<!-- An idle day still gets a baseline tick, so gaps read as "nothing ran" rather
						     than as missing data. -->
						<div class="bg-muted h-0.5 w-full rounded-sm"></div>
					{:else}
						<div class="flex w-full flex-col justify-end" style="height: {Math.max(height, 6)}%">
							{#if day.failed}
								<div
									class="bg-destructive w-full rounded-t-sm"
									style="flex: {day.failed} 0 0"
								></div>
							{/if}
							{#if ok}
								<div
									class="w-full {day.failed ? '' : 'rounded-t-sm'}"
									style="flex: {ok} 0 0; background: var(--brand)"
								></div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}

	<dl class="space-y-0.5 text-xs">
		<div class="flex justify-between gap-2">
			<dt class="text-muted-foreground">Last run</dt>
			<dd class="tabular-nums">{asStamp(metrics.lastRunAt)}</dd>
		</div>
		<div class="flex justify-between gap-2">
			<dt class="text-muted-foreground">Next scheduled</dt>
			<dd class="tabular-nums">
				{#if metrics.nextRunAt}
					{asStamp(metrics.nextRunAt)}
				{:else}
					<Badge variant="outline" class="font-normal">no schedule</Badge>
				{/if}
			</dd>
		</div>
	</dl>
</div>
