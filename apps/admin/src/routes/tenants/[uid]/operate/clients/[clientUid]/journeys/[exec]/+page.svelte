<script lang="ts">
	import { ClientJourney } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const tenantUid = $derived(data.tenant.uid);
	const clientUid = $derived(data.detail.contact.uid);
	const base = $derived(`/tenants/${tenantUid}/operate`);
</script>

<svelte:head><title>{data.journey.flow_name} · {data.clientName}</title></svelte:head>

<ClientJourney
	journey={data.journey}
	journeyHref="{base}/journeys/{data.journey.uid}"
	documentHref={(uid) => `${base}/clients/${clientUid}/documents/${uid}`}
	otherHref={(uid) => `${base}/clients/${uid}/journeys`}
/>
