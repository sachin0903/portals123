<script lang="ts">
	import OperateClientRecord from '$lib/operate-client-record.svelte';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>{data.clientName} · {data.tenant.name}</title></svelte:head>
<OperateClientRecord record={data.record} tenantUid={data.tenant.uid} section="overview" />
