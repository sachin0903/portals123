<script lang="ts">
	// Thin mount of the SHARED automations listing (@graphene/ui/automations). Data loading and the
	// form actions live in +page.server.ts; the component gets typed rows, this portal's link
	// builders and the names of THIS page's actions — a read-only mount simply passes fewer of them,
	// and every affordance keyed off a missing name disappears rather than 403ing.
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { ListingSkeleton, Streamed } from '@graphene/ui';
	import { AutomationsListing } from '@graphene/ui/automations';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Automations · Platform</title></svelte:head>

<!-- `keep`: every action on this page (arm, reorder, pause) re-runs the load, and the listing
     holds its place while the fresh estate lands rather than dropping back to the placeholder. -->
<Streamed data={data.estate} onretry={() => invalidateAll()} keep>
	{#snippet pending()}
		<ListingSkeleton fill={false} header={false} />
	{/snippet}
	{#snippet children(estate)}
		<AutomationsListing
			automations={estate.automations}
			settings={estate.settings}
			flows={estate.flows}
			loadFailed={estate.loadFailed}
			links={{ index: '/automations', automation: (uid) => `/automations/${uid}`, flows: '/flows' }}
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
	{/snippet}
</Streamed>
