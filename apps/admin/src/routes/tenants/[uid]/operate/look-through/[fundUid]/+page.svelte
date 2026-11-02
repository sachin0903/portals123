<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { FundLookthrough } from '@graphene/ui/book/lookthrough';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const account = $derived(data.lookthrough.holding!.account_uid);
	const dateHref = (date: string) => {
		const query = new URLSearchParams(page.url.searchParams);
		query.set('as_of', date);
		query.set('account', account);
		return `${page.url.pathname}?${query}`;
	};
</script>

<svelte:head><title>{data.lookthrough.fund.name} · Look-through</title></svelte:head>
<FundLookthrough
	data={data.lookthrough}
	backHref={`/tenants/${data.tenant.uid}/operate/accounts/${account}/holdings`}
	backLabel="Back to account holdings"
	{dateHref}
	onDateChange={(date) => goto(dateHref(date), { keepFocus: true, noScroll: true })}
/>
