<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { AutomationDetail, type DryRunResult } from '@graphene/ui/automations';
	import type { ActionData, PageData } from './$types';
	let { data, form }: { data: PageData; form: ActionData } = $props();
	const base = $derived(`/tenants/${page.params.uid}/operate/automations`);
	const dryRun = $derived(form && 'dryRun' in form ? (form.dryRun as DryRunResult) : null);
	function older() {
		if (data.nextBefore === null) return;
		const url = new URL(page.url);
		url.searchParams.set('before', String(data.nextBefore));
		goto(url, { keepFocus: true, noScroll: true });
	}
</script>

<AutomationDetail
	automation={data.automation}
	deliveries={data.deliveries}
	hasMore={data.hasMore}
	settings={data.settings}
	flows={data.flows}
	{dryRun}
	testActionName={data.canWrite ? 'dryRun' : undefined}
	links={{ index: base, automation: (uid) => `${base}/${uid}` }}
	actions={{ enhance, ...(data.canWrite ? { update: 'update', remove: 'remove' } : {}) }}
	onmore={older}
/>
