<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { FlowFunnel, type FlowFunnelLinks } from '@graphene/ui/book/journeys';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${page.params.uid}/operate/flows/${data.flowId}`);
	const links = $derived<FlowFunnelLinks>({
		flow: base,
		insights: ({ version, days }) => {
			const query = new URLSearchParams();
			if (version) query.set('version', version);
			if (days) query.set('days', String(days));
			return `${base}/insights${query.size ? `?${query}` : ''}`;
		}
	});
</script>

<FlowFunnel
	insights={data.insights}
	loadFailed={data.loadFailed}
	days={data.days}
	{links}
	onretry={() => invalidateAll()}
/>
