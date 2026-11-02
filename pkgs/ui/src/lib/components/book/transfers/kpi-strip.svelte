<script lang="ts">
	// Amount first, count underneath. The reconciled stage and cancelled are filters, not cards —
	// the same rule as the contributions strip. Totals come from the API over the whole book; the
	// rows on screen are only the fallback when a mount has no summary.
	import { METRIC_STAGES, type StageSummary } from './stages.js';
	import { transferValue, type ApiTransfer } from './api.js';
	import { Card } from '../../ui/card/index.js';
	import { Money } from '../../ui/money/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';

	let {
		rows,
		summary = null
	}: {
		rows: ApiTransfer[];
		summary?: StageSummary | null;
	} = $props();

	const ACCENT: Record<string, string> = {
		requested: 'border-t-warning',
		submitted: 'border-t-info',
		in_progress: 'border-t-info',
		assets_received: 'border-t-info',
		funds_sent: 'border-t-success',
		manual_intervention: 'border-t-destructive'
	};

	const kpis = $derived(
		METRIC_STAGES.map((stage) => {
			const onScreen = rows.filter((row) => stage.statuses.includes(row.status));
			const book = summary?.[stage.key];
			const amount = book
				? book.amount
				: onScreen.reduce((total, row) => total + (transferValue(row) ?? 0), 0);
			const count = book ? book.count : onScreen.length;
			return {
				key: stage.key,
				label: stage.label,
				amount,
				count: `${count} transfer${count === 1 ? '' : 's'}`,
				actor: stage.actor,
				accent:
					stage.key === 'manual_intervention'
						? count > 0
							? 'border-t-destructive'
							: 'border-t-success'
						: (ACCENT[stage.key] ?? 'border-t-brand')
			};
		})
	);
</script>

<!-- ONE ROW, from `lg`, the same strip as Contributions. It was an `auto-fit, minmax(12rem, 1fr)`
     grid, which at 1440 with the sidebar open fits five tiles and drops the sixth to a second row:
     a band of the screen spent before the queue starts, and the queue is what the page is for. Six
     fixed columns instead, the figure one step down (text-lg), and the label in sentence case,
     because "AT ADMINISTRATOR" in capitals is wider than a tile at 1280. Below `lg` it is three by
     two rather than a ragged wrap. -->
<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
	{#each kpis as kpi (kpi.key)}
		<Card glass class="min-w-0 gap-0 border-t-4 {kpi.accent} px-3 py-2">
			<!-- Truncates on the narrowest tile, with the whole label on hover and focus. -->
			<TruncatedText
				text={kpi.label}
				class="text-muted-foreground text-[11px] leading-4 font-medium"
			/>
			<div class="text-lg leading-tight font-semibold tracking-tight whitespace-nowrap">
				<Money amount={kpi.amount} />
			</div>
			<div class="flex min-w-0 items-baseline justify-between gap-2 text-[11px] leading-4">
				<span class="text-muted-foreground shrink-0 tabular-nums">{kpi.count}</span>
				<!-- Who has it. It shares the count's line where a tile has room for both (2xl), and is
				     read out below that. -->
				<span class="text-muted-foreground/70 hidden truncate text-[10px] font-medium 2xl:block">
					{kpi.actor}
				</span>
				<span class="sr-only 2xl:hidden">{kpi.actor}</span>
			</div>
		</Card>
	{/each}
</div>
