<script lang="ts">
	import OperateAccountRecord from '$lib/operate-account-record.svelte';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Holdings · {data.title} · {data.tenant.name}</title></svelte:head>
<OperateAccountRecord {data} section="holdings" />
