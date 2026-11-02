<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { ClientJourneys, ListingSkeleton, LoadErrorBanner, Streamed } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Journeys · {data.clientName}</title></svelte:head>

<!-- The book is STREAMED (`+page.server.ts`): the section's shape is on screen once the client
     shell is, and the rows land behind it. `keep`, so a load re-run does not flash the list back
     to the skeleton. The start-journey links are the resolved `newJourneys` — they are not known
     until the published-flows read lands, so they are read in here, not off `data`. -->
<Streamed data={data.book} onretry={() => invalidateAll()} keep>
	{#snippet pending()}
		<ListingSkeleton />
	{/snippet}
	{#snippet children(book)}
		{#if book.loadError}
			<LoadErrorBanner
				message="This client’s journeys couldn’t be loaded."
				onRetry={() => invalidateAll()}
			/>
		{:else}
			<ClientJourneys
				journeys={book.journeys}
				href={(uid) => `/clients/${data.detail.contact.uid}/journeys/${uid}`}
				newJourneys={book.newJourneys}
			/>
		{/if}
	{/snippet}
</Streamed>
