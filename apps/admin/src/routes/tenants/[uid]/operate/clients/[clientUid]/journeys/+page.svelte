<script lang="ts">
	import { ClientJourneys } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const base = $derived(`/tenants/${data.tenant.uid}/operate/clients/${data.detail.contact.uid}`);
</script>

<svelte:head><title>Journeys · {data.clientName}</title></svelte:head>

<ClientJourneys
	journeys={data.journeys}
	href={(uid) => `${base}/journeys/${uid}`}
	newJourneyHref={data.newJourneyHref}
/>
