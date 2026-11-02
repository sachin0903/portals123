<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { Alert, AlertDescription, ListingSkeleton, PageHeader, Streamed } from '@graphene/ui';
	import { DataSources } from '@graphene/ui/book/platforms';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Data sources · Platform</title></svelte:head>

<PageHeader
	title="Data sources"
	description="The custodians and providers that feed your book, and when they last did."
/>

<div class="space-y-4">
	<Streamed data={data.feed} onretry={() => invalidateAll()} keep>
		{#snippet pending()}
			<ListingSkeleton fill={false} header={false} />
		{/snippet}
		{#snippet children(feed)}
			{#if feed.loadError}
				<Alert variant="destructive">
					<AlertDescription>
						Could not load your data sources. Check your connection and try again.
					</AlertDescription>
				</Alert>
			{/if}
			<DataSources sources={feed.sources} />
		{/snippet}
	</Streamed>
</div>
