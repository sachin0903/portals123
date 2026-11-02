<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { PageHeader } from '@graphene/ui';
	import { FlowDryRun, type FlowSimulation } from '@graphene/ui/book/journeys';
	import type { ActionData, PageData } from './$types';
	let { data, form }: { data: PageData; form: ActionData } = $props();
	let pending = $state(false);
	const result = $derived(form && 'dryRun' in form ? (form.dryRun as FlowSimulation) : null);
	const error = $derived(form && 'dryRunError' in form ? String(form.dryRunError) : null);
	const submitting = (node: HTMLFormElement) =>
		enhance(node, () => {
			pending = true;
			return async ({ update }) => {
				pending = false;
				await update({ reset: false });
			};
		});
</script>

<PageHeader
	title="Dry run"
	description="Simulate this journey without performing anything."
	back={{
		href: `/tenants/${page.params.uid}/operate/flows/${data.flowId}`,
		label: 'Back to the journey'
	}}
/>
<FlowDryRun
	{result}
	{error}
	{pending}
	clients={data.clients}
	actionName={data.canRun ? 'dryRun' : undefined}
	enhance={data.canRun ? submitting : undefined}
/>
