<script lang="ts">
	import OperateAccountRecord from '$lib/operate-account-record.svelte';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Transactions · {data.title} · {data.tenant.name}</title></svelte:head>
<OperateAccountRecord
	data={{ ...data, loadError: data.loadError || data.transactionsError }}
	section="transactions"
	transactions={data.transactions}
/>
