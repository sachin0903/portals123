<script lang="ts">
	import TransferProvidersTable from '$lib/catalogue/transfer-providers-table.svelte';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<TransferProvidersTable
	providers={data.transferProviders}
	total={data.total}
	page={data.page}
	pageSize={data.pageSize}
	sort={data.sort}
	dir={data.dir}
	q={data.q}
	product={data.product}
	rail={data.rail}
	facets={data.facets}
	canManage={data.canManage}
	{form}
/>
