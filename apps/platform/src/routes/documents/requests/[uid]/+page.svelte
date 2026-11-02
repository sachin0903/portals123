<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import RequestRecord from './request-record.svelte';
	import type { PageData } from './$types';

	let { data, form }: { data: PageData; form: { error?: string } | null } = $props();
</script>

<svelte:head><title>Request · Platform</title></svelte:head>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} />
	{/snippet}
	{#snippet children(record)}
		<RequestRecord {record} canManage={data.canManage} canSend={data.canSend} {form} />
	{/snippet}
</Streamed>
