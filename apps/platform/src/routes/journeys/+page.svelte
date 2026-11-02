<script lang="ts">
	// Thin mount of the SHARED journeys listing (@graphene/ui book/journeys — the portal
	// waterfall: the admin portal renders the same board/list read-only over its own loaders).
	// Data loading stays in this app (+page.server.ts); the component gets typed rows and this
	// portal's link builders — including the start-a-journey entry point, which read-only mounts
	// simply omit.
	import { invalidateAll } from '$app/navigation';
	import { ListingSkeleton, Streamed } from '@graphene/ui';
	import { JourneysListing } from '@graphene/ui/book/journeys';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Journeys · Platform</title></svelte:head>

<Streamed data={data.inbox} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton fill={false} actions pager />
	{/snippet}
	{#snippet children(inbox)}
		<JourneysListing
			applications={inbox.applications}
			loadFailed={inbox.loadFailed}
			links={{
				journey: (uid) => `/journeys/${uid}`,
				newJourneys: inbox.startFlows.map((flow) => ({
					code: flow.code,
					name: flow.name,
					href: `/journeys/new?flow=${encodeURIComponent(flow.code)}`
				})),
				applicationPdf: (uid) => `/journeys/${uid}/application.pdf`
			}}
			onretry={() => invalidateAll()}
		/>
	{/snippet}
</Streamed>
