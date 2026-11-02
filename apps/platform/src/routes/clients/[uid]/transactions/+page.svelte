<script lang="ts">
	// The client's transaction history across their accounts — the record's "Transactions" section.
	//
	// A thin mount of the SHARED listing, so this reads exactly like `/transactions` and the account
	// record's band: same columns, same order, same words. It used to render the client portal's
	// `ActivityList`, which grouped by date and printed the canonical type raw ("deposit") — a shape
	// meant for a client reading their own account, not an adviser scanning a book.
	//
	// `showClient={false}`: every row here belongs to the client this page IS, so the column would
	// repeat their name all the way down and take the width the account and the asset need.
	//
	// No `actionRequired` snippet. Classifying a payment-in is the contributions workbench's job (one
	// queue, worked in bulk); the record's own Action-required strip already links into it, and a
	// second entry point here would be two places showing the same work in different shapes.
	import {
		ListingSkeleton,
		LoadErrorBanner,
		Streamed,
		TransactionsListing,
		type AccountBookLinks
	} from '@graphene/ui';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { listingExportHref } from '$lib/export-auth';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const links: AccountBookLinks = {
		account: (uid) => `/accounts/${uid}`,
		client: (uid) => `/clients/${uid}`
	};

	const href = (pageNumber: number) => pageHref(data.query, pageNumber, page.url.searchParams);
	// The shared export route, so this page gets PDF as well as CSV and its file is built by the
	// same code as every other listing's. The old per-page route carried its own CSV writer with no
	// formula guard, which is exactly the drift having one route avoids. Absent without
	// `listings:export`, which is what hides the button.
	const exportHref = $derived(
		listingExportHref(
			page.data.permissions,
			page.data.role,
			`/clients/${page.params.uid}/transactions/export` +
				(page.url.searchParams.size ? `?${page.url.searchParams}` : '')
		)
	);

	function navigate(next: ListingQuery) {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

<svelte:head><title>Transactions · {data.clientName}</title></svelte:head>

<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton />
	{/snippet}
	{#snippet children(book)}
		{#if book.loadError}
			<LoadErrorBanner
				message="This client’s transactions couldn’t be loaded."
				onRetry={() => invalidateAll()}
			/>
		{/if}

		<TransactionsListing
			transactions={book.transactions}
			{links}
			showClient={false}
			showAdviserFilter={false}
			showAccountFilter
			showDateFilter
			searchPlaceholder="Search reference, account or asset"
			{exportHref}
			listing={data.query}
			matchCount={book.matchCount}
			facets={book.facets}
			bounds={book.bounds}
			onquery={navigate}
			pageHref={href}
		/>
	{/snippet}
</Streamed>
