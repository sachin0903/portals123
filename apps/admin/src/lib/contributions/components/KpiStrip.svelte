<script lang="ts">
	import { Card } from '@graphene/ui';
	import type { WorkbenchStore } from '$lib/contributions/store.svelte';

	let { wb }: { wb: WorkbenchStore } = $props();

	// Each KPI's top accent is the meaning of the number: money in → brand, needs Ops → warning,
	// mid-pipeline → hairline, in transit → info, terminal → success, off-ramp → destructive.
	//
	// The accent is a theme CSS var applied inline, not a `border-t-*` utility: `.glass-panel`
	// sets the `border` shorthand after Tailwind's utility layer, so `border-t-4 border-t-x` on a
	// glass Card is silently overridden to the 1px glass hairline. Inline wins the cascade and the
	// value still comes from the branded token. See the same pattern in $lib/platforms.
	const kpis = $derived([
		{
			label: 'Received this month',
			value: '£287,440',
			sub: '24 contributions · net',
			accent: '--brand'
		},
		{
			label: 'Awaiting classification',
			value: String(wb.countForStage(['awaiting_confirmation'])),
			sub: 'needs Ops action',
			accent: '--warning'
		},
		{
			label: 'Recorded to ML',
			value: String(wb.countForStage(['ml_recorded'])),
			sub: 'recorded on the ML scheme',
			// Deliberately neutral — this step needs no attention. --border is too faint to read as
			// a 4px accent, so the neutral ink token carries it.
			accent: '--muted-foreground'
		},
		{
			label: 'Relief in transit',
			value: '£14,210',
			sub: 'HMRC → ML → SEI',
			accent: '--info'
		},
		{
			label: 'Reconciled',
			value: String(wb.countForStage(['reconciled'])),
			sub: '3-way matched',
			accent: '--success'
		},
		{
			label: 'Manual intervention',
			value: String(wb.countForStage(['manual_intervention'])),
			sub: 'see Reconciliation',
			accent: '--destructive'
		}
	]);
</script>

<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
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
