<script lang="ts">
	// Thin mount of the SHARED clients listing (@graphene/ui book/clients) — the same component
	// the platform portal's /clients page renders, pointed at this section's routes. `actions`
	// is null on purpose: ops read a firm's book, they do not create or restore its clients.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { ClientsListing } from '@graphene/ui';
	import {
		createListingNav,
		pageHref,
		paramsFromQuery,
		type ListingQuery
	} from '@graphene/ui/book/listing';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/book`);

	const href = (n: number) => pageHref(data.query, n, page.url.searchParams);

	// Both fire for one click when a status is chosen out of the Closed population — see
	// `createListingNav`, which is what stops the second rebuilding from a URL in flight.
	const nav = createListingNav((search, options) =>
		goto(search ? `?${search}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			...options
		})
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
</script>

<svelte:head><title>Clients · {data.tenant.name} · Operations</title></svelte:head>

<ClientsListing
	clients={data.clients}
	archived={data.archived}
	valuesAsOf={data.valuesAsOf}
	loadError={data.loadError}
	archivedError={data.archivedError}
	links={{ client: (uid) => `${base}/clients/${uid}` }}
	actions={null}
	initialSegment={page.url.searchParams.get('segment') === 'closed' ||
	page.url.searchParams.get('segment') === 'archived'
		? 'archived'
		: 'live'}
	onretry={() => invalidateAll()}
	listing={data.query}
	matchCount={data.matchCount}
	facets={data.facets}
	facetCounts={data.facetCounts}
	bounds={data.bounds}
	onquery={navigate}
	pageHref={href}
	{onsegment}
/>
