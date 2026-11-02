<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { EventStream, LoadErrorBanner, PageHeader } from '@graphene/ui';
	import { operateEntityHref } from '$lib/audit/entity-href';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Logs · {data.clientName}</title></svelte:head>
<div class="space-y-4">
	<PageHeader title="Activity" description="Every attributable action concerning this client." />
	{#if data.apiError}
		<LoadErrorBanner message={data.apiError} onRetry={() => invalidateAll()} />
	{/if}
	<EventStream
		events={data.events}
		facets={data.facets ?? undefined}
		hasMore={data.hasMore}
		loadError={!!data.apiError}
		showTenantFilter={false}
		attribution="firm"
		entityHref={(_event, entity) => operateEntityHref(data.tenant.uid, entity)}
		onRefresh={() => invalidateAll()}
	/>
</div>
