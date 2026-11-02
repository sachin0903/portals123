<script lang="ts">
	// The platform's `/transactions`, for this firm: the same shared listing, streamed.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		ListingSkeleton,
		LoadErrorBanner,
		Streamed,
		TransactionsListing,
		type AccountBookLinks
	} from '@graphene/ui';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { operateBase } from '$lib/record-focus';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(operateBase(data.tenant.uid));
	const links: AccountBookLinks = $derived({
		account: (uid) => `${base}/accounts/${uid}`,
		client: (uid) => `${base}/clients/${uid}`
	});
	const href = (n: number) => pageHref(data.query, n, page.url.searchParams);

	function navigate(next: ListingQuery) {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

<svelte:head><title>Transactions · {data.tenant.name}</title></svelte:head>

<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton />
	{/snippet}
	{#snippet children(book)}
		{#if book.loadError}
			<LoadErrorBanner
				message="This firm’s transactions couldn’t be loaded."
				onRetry={() => invalidateAll()}
			/>
		{/if}
		<TransactionsListing
			showDateFilter
			transactions={book.transactions}
			{links}
			listing={data.query}
			matchCount={book.matchCount}
			facets={book.facets}
			bounds={book.bounds}
			onquery={navigate}
			pageHref={href}
		/>
	{/snippet}
</Streamed>
