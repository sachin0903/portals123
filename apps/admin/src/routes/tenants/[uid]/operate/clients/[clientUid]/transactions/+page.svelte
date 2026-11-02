<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { LoadErrorBanner, TransactionsListing, type AccountBookLinks } from '@graphene/ui';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/operate`);
	const links: AccountBookLinks = $derived({
		account: (uid) => `${base}/accounts/${uid}`,
		client: (uid) => `${base}/clients/${uid}`
	});
	const href = (number: number) => pageHref(data.query, number, page.url.searchParams);
	function navigate(next: ListingQuery) {
		const query = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(query ? `?${query}` : page.url.pathname, { keepFocus: true, noScroll: true });
	}
</script>

<svelte:head><title>Transactions · {data.clientName}</title></svelte:head>
{#if data.loadError}
	<LoadErrorBanner
		message="This client’s transactions could not be loaded."
		onRetry={() => invalidateAll()}
	/>
{/if}
<TransactionsListing
	transactions={data.transactions}
	{links}
	showClient={false}
	showAdviserFilter={false}
	showAccountFilter
	showDateFilter
	listing={data.query}
	matchCount={data.matchCount}
	facets={data.facets}
	bounds={data.bounds}
	onquery={navigate}
	pageHref={href}
/>
