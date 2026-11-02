<script lang="ts">
	// The platform's `/clients`, for this firm: the same shared listing, streamed. Read-only —
	// operate has no client create/restore actions, so `actions` is null.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { ClientsListing, ListingSkeleton, Streamed } from '@graphene/ui';
	import {
		createListingNav,
		pageHref,
		paramsFromQuery,
		type ListingQuery
	} from '@graphene/ui/book/listing';
	import { operateBase } from '$lib/record-focus';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(operateBase(data.tenant.uid));
	const href = (n: number) => pageHref(data.query, n, page.url.searchParams);
	// Both fire for one click when a status is chosen out of the Closed population — see
	// `createListingNav`, which is what stops the second rebuilding from a URL in flight.
	const nav = createListingNav((search, options) =>
		goto(search ? `?${search}` : page.url.pathname, { keepFocus: true, noScroll: true, ...options })
	);
	function navigate(next: ListingQuery) {
		nav.commit(paramsFromQuery(next, nav.params(page.url.searchParams)), true);
	}
	function onsegment(segment: 'live' | 'archived') {
		const params = nav.params(page.url.searchParams);
		if (segment === 'archived') params.set('segment', 'closed');
		else params.delete('segment');
		nav.commit(params);
	}
	const segment = $derived(page.url.searchParams.get('segment'));
</script>

<svelte:head><title>Clients · {data.tenant.name}</title></svelte:head>

<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton />
	{/snippet}
	{#snippet children(book)}
		<ClientsListing
			clients={book.clients}
			archived={book.archived}
			valuesAsOf={book.valuesAsOf}
			loadError={book.loadError}
			archivedError={book.archivedError}
			links={{ client: (uid) => `${base}/clients/${uid}` }}
			actions={null}
			initialSegment={segment === 'closed' || segment === 'archived' ? 'archived' : 'live'}
			onretry={() => invalidateAll()}
			listing={data.query}
			matchCount={book.matchCount}
			facets={book.facets}
			facetCounts={book.facetCounts}
			bounds={book.bounds}
			onquery={navigate}
			pageHref={href}
			{onsegment}
		/>
	{/snippet}
</Streamed>
