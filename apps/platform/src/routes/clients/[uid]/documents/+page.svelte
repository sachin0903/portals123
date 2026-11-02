<script lang="ts">
	// This client's documents, files and the upload/generate/request actions — the record's
	// "Documents" section. (No send: a document filed under a client is already visible to them.)
	import { ListingSkeleton, LoadErrorBanner, Streamed } from '@graphene/ui';
	import { invalidateAll } from '$app/navigation';
	import ClientDocuments from '$lib/components/clients/client-documents.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Documents · {data.clientName}</title></svelte:head>

<div class="space-y-5">
	<Streamed data={data.library} onretry={() => invalidateAll()}>
		{#snippet pending()}
			<ListingSkeleton fill={false} actions rail={data.canReadActivity} />
		{/snippet}
		{#snippet children(library)}
			{#if library.loadError}
				<LoadErrorBanner
					message="This client’s documents couldn’t all be loaded."
					onRetry={() => invalidateAll()}
				/>
			{/if}

			<ClientDocuments data={library.docsData} canSend={data.canSend} canWrite={data.canWrite} />
		{/snippet}
	</Streamed>
</div>
