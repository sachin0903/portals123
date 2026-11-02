<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import PageTitle from '$lib/components/page-title.svelte';
	import { FundLookthrough } from '@graphene/ui/book/lookthrough';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const dateHrefFor = (account: string, date: string) => {
		const query = new URLSearchParams(page.url.searchParams);
		query.set('as_of', date);
		query.set('account', account);
		return `${page.url.pathname}?${query}`;
	};
</script>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton kpis={3} cards={1} />
	{/snippet}
	{#snippet children(record)}
		{@const account = record.lookthrough.holding!.account_uid}
		<PageTitle title="{record.lookthrough.fund.name} · Look-through" />
		<FundLookthrough
			data={record.lookthrough}
			backHref={`/accounts/${account}/holdings`}
			backLabel="Back to account holdings"
			accountHref={(uid) => `/accounts/${uid}`}
			dateHref={(date) => dateHrefFor(account, date)}
			onDateChange={(date) => goto(dateHrefFor(account, date), { keepFocus: true, noScroll: true })}
		/>
	{/snippet}
</Streamed>
