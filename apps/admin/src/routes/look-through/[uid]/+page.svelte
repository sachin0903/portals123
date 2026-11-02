<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { FundLookthrough } from '@graphene/ui/book/lookthrough';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const dateHref = (date: string) => {
		const query = new URLSearchParams(page.url.searchParams);
		query.set('as_of', date);
		return `${page.url.pathname}?${query}`;
	};
</script>

<svelte:head><title>{data.lookthrough.fund.name} · Look-through · Operations</title></svelte:head>

<FundLookthrough
	data={data.lookthrough}
	backHref="/look-through"
	backLabel="Back to fund register"
	{dateHref}
	onDateChange={(date) => goto(dateHref(date), { keepFocus: true, noScroll: true })}
/>
