<script lang="ts">
	import { Alert, AlertDescription, PageHeader } from '@graphene/ui';
	import { DataSources } from '@graphene/ui/book/platforms';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Data sources · {data.tenant.name}</title></svelte:head>

<PageHeader
	eyebrow="Operate"
	title="Data sources"
	description="The custodians and providers feeding this firm’s book, and when they last did."
/>

<div class="space-y-4">
	{#if data.loadError}
		<Alert variant="destructive">
			<AlertDescription>
				{data.loadError} This is not the same as having none connected.
			</AlertDescription>
		</Alert>
	{/if}
	<DataSources sources={data.sources} />
</div>
