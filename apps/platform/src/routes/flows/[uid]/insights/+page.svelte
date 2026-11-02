<script lang="ts">
	// A thin mount of the shared funnel (@graphene/ui/book/journeys). The route knowledge is the
	// only thing this app contributes: the admin portal mounts the same component at its own paths.
	import { invalidateAll } from '$app/navigation';
	import { FlowFunnel, type FlowFunnelLinks } from '@graphene/ui/book/journeys';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// $derived, not a plain const: `data` is reactive state, and capturing it at init would freeze
	// the hrefs on the first-loaded journey when SvelteKit reuses the component across navigations.
	const links = $derived<FlowFunnelLinks>({
		flow: `/flows/${data.flowId}`,
		insights: ({ version, days }) => {
			const query = new URLSearchParams();
			if (version) query.set('version', version);
			if (days) query.set('days', String(days));
			const qs = query.toString();
			return `/flows/${data.flowId}/insights${qs ? `?${qs}` : ''}`;
		}
	});
</script>

<svelte:head><title>Journey insights</title></svelte:head>

<FlowFunnel
	insights={data.insights}
	loadFailed={data.loadFailed}
	days={data.days}
	{links}
	onretry={() => invalidateAll()}
/>
