<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { FundLookthrough } from '@graphene/ui/book/lookthrough';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	// The holder-gated endpoint always returns the holding that authorised this read. The shared
	// visualisation also accepts `holding: null` for the operations reference view.
	const account = $derived(data.lookthrough.holding!.account_uid);
	const dateHref = (date: string) => {
		const query = new URLSearchParams(page.url.searchParams);
		query.set('as_of', date);
		query.set('account', account);
		return `${page.url.pathname}?${query}`;
	};
</script>

<svelte:head><title>{data.lookthrough.fund.name} · Look-through</title></svelte:head>

<!-- No backHref: the shell draws the way back on every sub page (ClientShell `back`, from this
     route's `data.back`). -->
<FundLookthrough
	data={data.lookthrough}
	{dateHref}
	onDateChange={(date) => goto(dateHref(date), { keepFocus: true, noScroll: true })}
/>
