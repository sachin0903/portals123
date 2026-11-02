<script lang="ts">
	// Thin route over the shared accounts book (@graphene/ui book/accounts): the loader owns
	// the data, this page owns the platform's routes and its navigation — the component takes
	// all three as props.
	//
	// The listing is server-driven, so this page turns a requested query into a URL. That lives
	// here because `$app/navigation` is SvelteKit and `@graphene/ui` is not.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { AccountsListing, ListingSkeleton, Streamed, type AccountBookLinks } from '@graphene/ui';
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
			`/accounts/export` + (page.url.search ? page.url.search : '')
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

<!-- Streamed: the listing's shape is on screen the instant the sidebar is clicked, and the rows land
     behind it. The last book stays put while the next is fetched, so typing in the search box is
     never interrupted by its own reload. -->
<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton />
	{/snippet}
	{#snippet children(book)}
		<AccountsListing
			accounts={book.accounts}
			{links}
			listing={data.query}
			matchCount={book.matchCount}
			facets={book.facets}
			bounds={book.bounds}
			productTypeOptions={book.productTypeOptions}
			modelOptions={book.modelOptions}
			onquery={navigate}
			pageHref={href}
			{exportHref}
		/>
	{/snippet}
</Streamed>
