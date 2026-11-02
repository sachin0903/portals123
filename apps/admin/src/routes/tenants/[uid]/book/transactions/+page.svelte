<script lang="ts">
	// Thin mount of the SHARED transactions listing (@graphene/ui book/accounts) over this
	// section's routes. No `actionRequired` snippet: categorising a pension payment-in is a firm
	// workflow, so the queue that sits above the platform's table has no business here.
	//
	// Server-driven, like the platform page: the API applied the search, the filters and the page,
	// so the toolbar round-trips through the URL rather than filtering the rows in hand.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { LoadErrorBanner, TransactionsListing, type AccountBookLinks } from '@graphene/ui';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/book`);

	const links: AccountBookLinks = $derived({
		account: (uid) => `${base}/accounts/${uid}`,
		client: (uid) => `${base}/clients/${uid}`
	});

	/** Real links for the pager. The current params are passed so an unrelated one survives. */
	const href = (n: number) => pageHref(data.query, n, page.url.searchParams);

	/** `keepFocus` so typing in the search box is not interrupted by the reload it triggers;
	 *  `noScroll` so paging does not jump to the top of the page. */
	function navigate(next: ListingQuery) {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

<svelte:head><title>Transactions · {data.tenant.name} · Operations</title></svelte:head>

{#if data.loadError}
	<!-- An ops operator reading a firm's book must never be shown an empty ledger for a firm that
	     has one. The banner is the difference between "no transactions" and "we could not read
	     them". -->
	<div class="mb-4">
		<LoadErrorBanner
			message="This firm’s transactions couldn’t be loaded."
			onRetry={() => invalidateAll()}
		/>
	</div>
{/if}

<TransactionsListing
	transactions={data.transactions}
	{links}
	listing={data.query}
	matchCount={data.matchCount}
	facets={data.facets}
	bounds={data.bounds}
	onquery={navigate}
	pageHref={href}
/>
