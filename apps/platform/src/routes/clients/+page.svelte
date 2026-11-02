<script lang="ts">
	// Thin mount of the SHARED clients listing (@graphene/ui book/clients — the portal waterfall:
	// the admin portal renders the same component against /tenants/[id]/book links). Data loading
	// and the form actions stay in this app (+page.server.ts); the component gets typed rows, this
	// portal's link builders, and — because this user can write — the action names + `enhance`.
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { ClientsListing, ListingSkeleton, Streamed } from '@graphene/ui';
	import {
		createListingNav,
		pageHref,
		paramsFromQuery,
		type ListingQuery
	} from '@graphene/ui/book/listing';
	import { listingExportHref } from '$lib/export-auth';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/** The listing is server-driven, so a requested query becomes a URL. `keepFocus` so typing in
	 *  the search box is not interrupted by the reload it triggers; `noScroll` so paging does not
	 *  jump to the top. Unrelated params (`?segment=`) are preserved by `paramsFromQuery`. */
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
			`/clients/export` + (page.url.search ? page.url.search : '')
		)
	);

	// ONE CLICK CAN ASK FOR BOTH: choosing a status while the Closed population is on screen
	// switches population AND sets a filter, so `onsegment` and `onquery` both fire. `createListingNav`
	// is what makes the second build on the first rather than on a URL still in flight — see its
	// own note for what that cost (Export downloading the closed set under every status).
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

	/** Restore and create are independent grants. A role that can mint is not forced to
	 *  reopen, and a read-only mount never grows either affordance. */
	const listingActions = $derived(
		data.canManage || data.canCreate
			? {
					enhance,
					...(data.canManage ? { restore: '?/restoreClient' } : {}),
					...(data.canCreate
						? {
								create: '?/createClient',
								oncreated: (uid: string) => goto(`/clients/${uid}`)
							}
						: {})
				}
			: null
	);
</script>

<!-- The book is streamed, so the listing's shape is on screen the instant this page is navigated to
     and the rows land behind it. `Streamed` keeps the last book while the next one is fetched, so a
     filter keystroke does not replace the table you are reading with a grey one.
     The placeholder is the BOOK's own shape, not a generic one: `spine`, because every row here
     stacks the date of birth under the name and is eight pixels taller than a plain row; and as
     many rows as the page will hold, so below `md` (where the page scrolls rather than the card)
     the frame is already the height the rows will fill. Either mismatch moves the page when they
     land. -->
<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton actions={!!data.canCreate} spine rows={data.query.limit} />
	{/snippet}
	{#snippet children(book)}
		<ClientsListing
			clients={book.clients}
			archived={book.archived}
			valuesAsOf={book.valuesAsOf}
			loadError={book.loadError}
			archivedError={book.archivedError}
			links={{ client: (uid) => `/clients/${uid}` }}
			actions={listingActions}
			initialCreate={page.url.searchParams.get('new') === '1'}
			initialSegment={page.url.searchParams.get('segment') === 'closed' ||
			page.url.searchParams.get('segment') === 'archived'
				? 'archived'
				: 'live'}
			onretry={() => invalidateAll()}
			listing={data.query}
			matchCount={book.matchCount}
			facets={book.facets}
			facetCounts={book.facetCounts}
			bounds={book.bounds}
			onquery={navigate}
			pageHref={href}
			{exportHref}
			{onsegment}
		/>
	{/snippet}
</Streamed>
