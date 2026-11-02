<script lang="ts">
	// Thin route over the shared transactions listing (@graphene/ui book/accounts). The rows are
	// real now — `platform.transactions` via `/crm/transactions`, paged and filtered server-side —
	// so this page keeps only the link builders and the navigation.
	//
	// The Action required strip is GONE. Classifying a payment-in is the contributions workbench's
	// job now (one queue, detected credits at the front), and a second entry point that showed the
	// same work in a different shape meant two places to keep in step and two counts to disagree.
	// The `needs_categorisation` flag still arrives on the rows, so the listing can mark them.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		ListingSkeleton,
		Streamed,
		TransactionsListing,
		type AccountBookLinks
	} from '@graphene/ui';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { listingExportHref } from '$lib/export-auth';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const links: AccountBookLinks = {
		account: (uid) => `/accounts/${uid}`,
		client: (uid) => `/clients/${uid}`
	};

	/** Real links for the pager. The current params are passed so an unrelated one survives. */
	const href = (n: number) => pageHref(data.query, n, page.url.searchParams);

	/** The export carries the CURRENT filters, so the file matches the screen. Built from the
	 *  page's own search params rather than re-derived, for the same reason `pageHref` is: two
	 *  descriptions of the same filter set is how one of them goes stale. Absent without
	 *  `listings:export`, which is what hides the button. */
	const exportHref = $derived(
		listingExportHref(
			page.data.permissions,
			page.data.role,
			`/transactions/export` + (page.url.search ? page.url.search : '')
		)
	);

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

<!-- Streamed: the listing's shape lands on the click and the rows follow. The last page of movements
     stays on screen while the next is fetched, so paging and typing are not interrupted. -->
<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton />
	{/snippet}
	{#snippet children(book)}
		<!-- `detail="panel"`: this page owns the screen, so a picked movement opens in a panel pushed
		     in beside the table rather than a drawer over it (layout A, as on Contributions). -->
		<TransactionsListing
			showDateFilter
			detail="panel"
			transactions={book.transactions}
			{links}
			listing={data.query}
			matchCount={book.matchCount}
			facets={book.facets}
			bounds={book.bounds}
			onquery={navigate}
			pageHref={href}
			{exportHref}
		/>
	{/snippet}
</Streamed>
