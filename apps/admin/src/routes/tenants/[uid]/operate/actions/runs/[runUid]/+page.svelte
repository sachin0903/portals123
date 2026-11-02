<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { RunDetailView, type RunLinks } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const operate = $derived(`/tenants/${page.params.uid}/operate`);
	const links = $derived<RunLinks>({
		run: (uid) => `${operate}/actions/runs/${uid}`,
		client: (uid) => `${operate}/clients/${uid}`,
		account: (uid) => `${operate}/accounts/${uid}`,
		policy: (key) => `${operate}/actions?tab=policies&action=${encodeURIComponent(key)}`
	});
</script>

<svelte:head><title>{data.run.name} · Run · {data.tenant.name}</title></svelte:head>

<RunDetailView
	run={data.run}
	canManage={data.canManage}
	{enhance}
	{links}
	actions={{ retry: '?/retry', retryStep: '?/retry', cancel: '?/cancel', decide: '?/decide' }}
	back={{ href: `${operate}/actions`, label: 'Actions' }}
	onrefresh={() => invalidateAll()}
/>
