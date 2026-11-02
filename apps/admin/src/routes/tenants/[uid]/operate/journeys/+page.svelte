<script lang="ts">
	// The platform's `/journeys`, for this firm: the same shared listing, streamed, with the
	// start-a-journey menu when the operator may write.
	import { invalidateAll } from '$app/navigation';
	import { ListingSkeleton, Streamed } from '@graphene/ui';
	import { JourneysListing } from '@graphene/ui/book/journeys';
	import { operateBase } from '$lib/record-focus';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(`${operateBase(data.tenant.uid)}/journeys`);
</script>

<svelte:head><title>Journeys · {data.tenant.name}</title></svelte:head>

<Streamed data={data.inbox} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton fill={false} actions pager />
	{/snippet}
	{#snippet children(inbox)}
		<JourneysListing
			applications={inbox.applications}
			loadFailed={inbox.loadFailed}
			links={{
				journey: (uid) => `${base}/${uid}`,
				newJourneys: inbox.startFlows.map((flow) => ({
					code: flow.code,
					name: flow.name,
					href: `${base}/new?flow=${encodeURIComponent(flow.code)}`
				})),
				applicationPdf: (uid) => `${base}/${uid}/application.pdf`
			}}
			onretry={() => invalidateAll()}
		/>
	{/snippet}
</Streamed>
