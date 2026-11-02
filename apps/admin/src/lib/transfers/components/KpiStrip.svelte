<script lang="ts">
	import { Card, formatGBP } from '@graphene/ui';
	import type { TransfersStore } from '$lib/transfers/store.svelte';

	let { wb }: { wb: TransfersStore } = $props();

	// The top accent carries the KPI's meaning — in motion → info, break → destructive, at risk →
	// warning, landing → success — off branded theme tokens, so a firm's palette drives the strip
	// and dark mode follows from the same definition.
	//
	// Applied inline rather than via `border-t-*`: `.glass-panel` sets the `border` shorthand after
	// Tailwind's utility layer, so a `border-t-4 border-t-x` accent on a glass Card is silently
	// flattened to the 1px glass hairline. Inline wins, and the value is still the token.
	const kpis = $derived([
		{
			label: 'In flight',
			value: formatGBP(wb.figures.inFlightValue),
			sub: `${wb.figures.inFlightCount} transfers in motion`,
			accent: '--info'
		},
		{
			label: 'Reconciliation breaks',
			value: String(wb.figures.breakCount),
			sub: `Δ ${formatGBP(wb.figures.breakDelta)} out of tolerance`,
			accent: '--destructive'
		},
		{
			label: 'Stalled',
			value: String(wb.figures.stalled),
			sub: 'no movement / at risk',
			accent: '--warning'
		},
		{
			label: 'Settling today',
			value: String(wb.figures.settlingToday),
			sub: 'settlement date = today',
			accent: '--success'
		}
	]);
</script>

<div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
	{#each kpis as kpi (kpi.label)}
		<Card glass size="sm" class="p-3.5" style="border-top: 4px solid var({kpi.accent})">
			<div class="text-muted-foreground text-[11.5px] font-semibold">
				{kpi.label}
			</div>
			<div class="mt-1 text-2xl font-semibold tracking-tight tabular-nums">{kpi.value}</div>
			<div class="text-muted-foreground mt-0.5 text-xs">{kpi.sub}</div>
		</Card>
	{/each}
</div>
