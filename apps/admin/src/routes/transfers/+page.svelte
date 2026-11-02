<script lang="ts">
	import { Alert, AlertDescription, PageHeader, Tabs } from '@graphene/ui';
	import { TransfersStore } from '$lib/transfers/store.svelte';
	import KpiStrip from '$lib/transfers/components/KpiStrip.svelte';
	import SettlementBoard from '$lib/transfers/components/SettlementBoard.svelte';
	import WorkQueue from '$lib/transfers/components/WorkQueue.svelte';
	import FocusPanel from '$lib/transfers/components/FocusPanel.svelte';
	import ReconBreaks from '$lib/transfers/components/ReconBreaks.svelte';
	import StatusMap from '$lib/transfers/components/StatusMap.svelte';
	import FlowReference from '$lib/transfers/components/FlowReference.svelte';

	const wb = new TransfersStore();
</script>

<div class="space-y-5">
	<PageHeader
		eyebrow="Internal operations"
		title="Transfers workbench"
		description="Review transfers across Morgan Lloyd, SEI and Seccl."
	/>

	<Tabs.Root bind:value={wb.tab}>
		<Tabs.List class="flex flex-wrap">
			<Tabs.Trigger value="workbench">Workbench</Tabs.Trigger>
			<Tabs.Trigger value="recon"
				>Reconciliation{wb.breaks.length ? ` (${wb.breaks.length})` : ''}</Tabs.Trigger
			>
			<Tabs.Trigger value="mappings">Status map</Tabs.Trigger>
			<Tabs.Trigger value="flow">Flow &amp; reference</Tabs.Trigger>
		</Tabs.List>

		<Tabs.Content value="workbench" class="space-y-4 pt-4">
			<KpiStrip {wb} />
			<SettlementBoard {wb} />
			<div class="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_400px]">
				<WorkQueue {wb} />
				<FocusPanel {wb} />
			</div>
		</Tabs.Content>

		<Tabs.Content value="recon" class="space-y-3 pt-4">
			<Alert>
				<AlertDescription class="text-xs">
					Each transfer reconciles its advised value against the realised custody credit (±£0.01).
					Morgan Lloyd and SEI transfers use three-way reconciliation; single-custodian routes use
					two-way reconciliation. Any difference is flagged for review.
				</AlertDescription>
			</Alert>
			<ReconBreaks {wb} />
		</Tabs.Content>

		<Tabs.Content value="mappings" class="pt-4"><StatusMap /></Tabs.Content>
		<Tabs.Content value="flow" class="pt-4"><FlowReference /></Tabs.Content>
	</Tabs.Root>
</div>
