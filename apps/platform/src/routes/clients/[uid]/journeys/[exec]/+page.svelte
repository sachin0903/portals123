<script lang="ts">
	import { ClientJourney } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const clientUid = $derived(data.detail.contact.uid);
</script>

<svelte:head><title>{data.journey.flow_name} · {data.clientName}</title></svelte:head>

<ClientJourney
	journey={data.journey}
	journeyHref="/journeys/{data.journey.uid}"
	documentHref={(uid) => `/clients/${clientUid}/documents/${uid}`}
	otherHref={(uid) => `/clients/${uid}/journeys`}
/>
