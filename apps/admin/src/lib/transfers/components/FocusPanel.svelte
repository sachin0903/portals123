<script lang="ts">
	import { Separator, formatGBP } from '@graphene/ui';
	import type { TransfersStore } from '$lib/transfers/store.svelte';
	import RouteBadge from './RouteBadge.svelte';
	import StatusBadge from './StatusBadge.svelte';
	import StageRail from './StageRail.svelte';
	import ChainOfCustody from './ChainOfCustody.svelte';
	import AssetLines from './AssetLines.svelte';
	import ReconPanel from './ReconPanel.svelte';
	import OpsRegistry from './OpsRegistry.svelte';
	import ForceStepForm from './ForceStepForm.svelte';
	import BreakResolveForm from './BreakResolveForm.svelte';
	import EventTrail from './EventTrail.svelte';

	let { wb }: { wb: TransfersStore } = $props();
</script>

<div class="border-border/70 bg-card flex h-full flex-col rounded-xl border">
	<div class="border-border/70 flex items-center justify-between border-b px-4 py-3">
		<h3 class="font-semibold">Transfer detail</h3>
		{#if wb.selected}<StatusBadge status={wb.selected.status} />{/if}
	</div>

	{#if !wb.selected}
		<div
			class="text-muted-foreground flex flex-1 flex-col items-center justify-center gap-2 p-10 text-center text-sm"
		>
			<p>Select a transfer to inspect its chain of custody, reconciliation, and act on it.</p>
		</div>
	{:else}
		{@const t = wb.selected}
		<div class="space-y-4 overflow-y-auto p-4">
			<div>
				<div class="flex items-center gap-2">
					<span class="text-lg font-semibold">{t.client}</span>
					<RouteBadge route={t.route} />
				</div>
				<div class="text-muted-foreground mt-1 font-mono text-[11px]">
					{t.id} · {formatGBP(t.value)} · {t.basis === 'cash' ? 'Cash' : 'In-specie'} · {t.scope}
				</div>
			</div>

			<StageRail transfer={t} />
			<Separator />

			<div>
				<h4 class="text-muted-foreground mb-2 text-xs font-semibold">Chain of custody</h4>
				<ChainOfCustody transfer={t} />
			</div>

			{#if t.basis === 'in_specie' && t.assets && t.assets.length}
				<div>
					<h4 class="text-muted-foreground mb-2 text-xs font-semibold">
						In-specie assets · CREST re-registration
					</h4>
					<AssetLines {wb} />
				</div>
			{/if}

			<div>
				<h4 class="text-muted-foreground mb-2 text-xs font-semibold">Reconciliation</h4>
				<ReconPanel transfer={t} />
			</div>

			{#if t.status === 'manual_intervention'}
				<BreakResolveForm {wb} />
			{/if}

			<div>
				<h4 class="text-muted-foreground mb-2 text-xs font-semibold">Manage transfer</h4>
				<OpsRegistry {wb} />
			</div>

			<details class="group">
				<summary class="text-muted-foreground cursor-pointer text-xs font-semibold"
					>Force step (admin)</summary
				>
				<div class="pt-2"><ForceStepForm {wb} /></div>
			</details>

			<Separator />
			<div>
				<h4 class="text-muted-foreground mb-2 text-xs font-semibold">Event trail</h4>
				<EventTrail transfer={t} />
			</div>
		</div>
	{/if}
</div>
