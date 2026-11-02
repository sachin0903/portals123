<script lang="ts">
	import { PageHeader, SectionHeading, Tabs } from '@graphene/ui';
	import { WorkbenchStore } from '$lib/contributions/store.svelte';
	import KpiStrip from '$lib/contributions/components/KpiStrip.svelte';
	import PipelineStrip from '$lib/contributions/components/PipelineStrip.svelte';
	import WorkQueue from '$lib/contributions/components/WorkQueue.svelte';
	import ClassifyPanel from '$lib/contributions/components/ClassifyPanel.svelte';
	import SeiPoolTable from '$lib/contributions/components/SeiPoolTable.svelte';
	import ApprovalQueue from '$lib/contributions/components/ApprovalQueue.svelte';
	import RasBatches from '$lib/contributions/components/RasBatches.svelte';
	import ReliefTable from '$lib/contributions/components/ReliefTable.svelte';
	import ReconView from '$lib/contributions/components/ReconView.svelte';
	import StatusMappings from '$lib/contributions/components/StatusMappings.svelte';
	import FlowReference from '$lib/contributions/components/FlowReference.svelte';

	const wb = new WorkbenchStore();
</script>

<div class="space-y-5">
	<PageHeader
		eyebrow="Internal operations"
		title="Contributions workbench"
		description="Review contributions, incoming payments, tax relief and reconciliation."
	/>

	<Tabs.Root bind:value={wb.tab}>
		<Tabs.List class="flex flex-wrap">
			<Tabs.Trigger value="workbench">Workbench</Tabs.Trigger>
			<Tabs.Trigger value="incoming">Incoming payments</Tabs.Trigger>
			<Tabs.Trigger value="approvals"
				>Approvals{wb.pendingApprovals.length
					? ` (${wb.pendingApprovals.length})`
					: ''}</Tabs.Trigger
			>
			<Tabs.Trigger value="ras">RAS &amp; tax relief</Tabs.Trigger>
			<Tabs.Trigger value="recon">Reconciliation</Tabs.Trigger>
			<Tabs.Trigger value="mappings">Status mappings</Tabs.Trigger>
			<Tabs.Trigger value="flow">Flow &amp; reference</Tabs.Trigger>
		</Tabs.List>

		<Tabs.Content value="workbench" class="space-y-4 pt-4">
			<KpiStrip {wb} />
			<PipelineStrip {wb} />
			<div class="grid grid-cols-1 gap-4 xl:grid-cols-[1fr_390px]">
				<WorkQueue {wb} />
				<ClassifyPanel {wb} />
			</div>
		</Tabs.Content>

		<Tabs.Content value="incoming" class="pt-4"><SeiPoolTable {wb} /></Tabs.Content>
		<Tabs.Content value="approvals" class="pt-4"><ApprovalQueue {wb} /></Tabs.Content>
		<Tabs.Content value="ras" class="space-y-6 pt-4">
			<RasBatches />
			<div>
				<SectionHeading title="Relief in transit: expected against received" />
				<ReliefTable {wb} />
			</div>
		</Tabs.Content>
		<Tabs.Content value="recon" class="pt-4"><ReconView /></Tabs.Content>
		<Tabs.Content value="mappings" class="pt-4"><StatusMappings /></Tabs.Content>
		<Tabs.Content value="flow" class="pt-4"><FlowReference /></Tabs.Content>
	</Tabs.Root>
</div>
