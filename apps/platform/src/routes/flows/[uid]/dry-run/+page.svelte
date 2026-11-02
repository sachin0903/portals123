<script lang="ts">
	// A thin mount of the shared dry-run panel (@graphene/ui/book/journeys). The app contributes
	// the form-action name, SvelteKit's `enhance` (pkgs/ui cannot import `$app/forms`) and the
	// route knowledge — nothing else.
	import { enhance } from '$app/forms';
	import { FlowDryRun, type FlowSimulation } from '@graphene/ui/book/journeys';
	import { PageHeader } from '@graphene/ui';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let pending = $state(false);

	const result = $derived(
		form && 'dryRun' in form ? ((form.dryRun ?? null) as FlowSimulation | null) : null
	);
	const error = $derived(
		form && 'dryRunError' in form ? ((form.dryRunError ?? null) as string | null) : null
	);

	// The house submit pattern: disable while in flight, re-enable on the result, and keep the
	// author's chosen subject on screen (`reset: false`) so a second run is one click. Wrapped here
	// rather than inside the shared panel because `enhance` itself is app-side.
	const submitting = (node: HTMLFormElement) =>
		enhance(node, () => {
			pending = true;
			return async ({ update }) => {
				pending = false;
				await update({ reset: false });
			};
		});
</script>

<svelte:head><title>Preview journey</title></svelte:head>

<PageHeader
	eyebrow="Journeys"
	title="Preview journey"
	description={data.flow
		? `Review the steps ${data.flow.name} would follow for this client. No changes will be made.`
		: 'Review the steps this journey would follow for this client. No changes will be made.'}
	back={{ href: `/flows/${data.flowId}`, label: 'Back to the journey' }}
/>

<FlowDryRun
	{result}
	{error}
	{pending}
	clients={data.clients}
	actionName={data.canRun ? 'dryRun' : undefined}
	enhance={data.canRun ? submitting : undefined}
/>
