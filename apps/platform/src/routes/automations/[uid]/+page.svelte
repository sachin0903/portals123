<script lang="ts">
	// Thin mount of the SHARED automation detail (@graphene/ui/automations). The dry-run verdict
	// comes back on `form` from this page's own action, so the panel renders the SERVER's evaluation
	// — there is one condition evaluator and it lives in the API.
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { AutomationDetail, type DryRunResult } from '@graphene/ui/automations';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// `ActionData` is the union of every action's return, so the verdict widens to `unknown` at the
	// property read. Narrowed here rather than at the prop so the shape assertion has one home.
	const dryRun = $derived(
		form && 'dryRun' in form ? ((form.dryRun ?? null) as DryRunResult | null) : null
	);

	function older() {
		if (data.nextBefore === null) return;
		const url = new URL(page.url);
		url.searchParams.set('before', String(data.nextBefore));
		goto(url, { keepFocus: true, noScroll: true });
	}
</script>

<svelte:head><title>{data.automation.name} · Automations · Platform</title></svelte:head>

<AutomationDetail
	automation={data.automation}
	deliveries={data.deliveries}
	hasMore={data.hasMore}
	settings={data.settings}
	flows={data.flows}
	{dryRun}
	testActionName={data.canWrite ? 'dryRun' : undefined}
	links={{ index: '/automations', automation: (uid) => `/automations/${uid}` }}
	actions={{
		enhance,
		...(data.canWrite ? { update: 'update', remove: 'remove' } : {})
	}}
	onmore={older}
/>
