<script lang="ts">
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { AutomationsListing } from '@graphene/ui/automations';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${page.params.uid}/operate`);
</script>

<AutomationsListing
	automations={data.automations}
	settings={data.settings}
	flows={data.flows}
	loadFailed={data.loadFailed}
	links={{
		index: `${base}/automations`,
		automation: (uid) => `${base}/automations/${uid}`,
		flows: `${base}/flows`
	}}
	actions={{
		enhance,
		...(data.canWrite
			? {
					create: 'create',
					update: 'update',
					remove: 'remove',
					toggle: 'toggle',
					reorder: 'reorder'
				}
			: {}),
		...(data.canPause ? { pause: 'pause' } : {})
	}}
/>
