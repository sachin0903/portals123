<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { RunDetailView, type RunLinks } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/** Injected navigation, per the portal waterfall: the view hardcodes no hrefs. */
	const links: RunLinks = {
		run: (uid) => `/actions/runs/${uid}`,
		client: (uid) => `/clients/${uid}`,
		account: (uid) => `/accounts/${uid}`,
		journey: (uid) => `/journeys/${uid}`,
		policy: (key) => `/actions?tab=policies&action=${encodeURIComponent(key)}`
	};
</script>

<svelte:head><title>{data.run.name} · Run · Actions</title></svelte:head>

<RunDetailView
	run={data.run}
	canManage={data.canManage}
	{enhance}
	{links}
	actions={{ retry: '?/retry', retryStep: '?/retry', cancel: '?/cancel', decide: '?/decide' }}
	back={{ href: '/actions', label: 'Actions' }}
	onrefresh={() => invalidateAll()}
/>
