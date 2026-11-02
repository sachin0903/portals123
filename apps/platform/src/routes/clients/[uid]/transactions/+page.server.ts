import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import type { Transaction } from '@graphene/ui';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { FILTER_KEYS, SORT } from './listing';
import type { PageServerLoad } from './$types';

/**
 * The client's transactions — the SAME table every other transactions surface uses.
 *
 * It used to map these rows through `toActivity` into the client PORTAL's `ActivityList`: one group
 * per date, the canonical type printed raw ("deposit"), and an ISO date as the group heading. That
 * shape is right for a client reading their own account and wrong for an adviser working a book,
 * who wants the same columns, the same sort and the same words as `/transactions` and the account
 * record. The rows were always real — only the presentation was borrowed.
 *
 * Was the record's "Transactions" tab; a section of its own means the overview no longer pays for
 * this read to render accounts and details.
 */
export const load: PageServerLoad = async ({ locals, fetch, url, parent }) => {
	if (!locals.user) redirect(303, '/login');

	// `contact_id` is a FILTER, not an address: the API keys the join on the serial. It comes from
	// the record the layout has already loaded, so the uid in the URL costs no extra round trip —
	// which is why this reads `parent()` rather than resolving `params.uid`.
	const { detail } = await parent();
	const query = queryFromParams(url.searchParams, FILTER_KEYS, SORT);
	return {
		query,
		book: loadBook(locals.accessToken, fetch, detail.contact.id, query)
	};
};

async function loadBook(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	contactId: number,
	query: ReturnType<typeof queryFromParams>
) {
	const res = await fetch(
		`${apiUrl()}/crm/transactions?contact_id=${contactId}&paged=true&${apiParams(query)}`,
		{
			headers: authHeaders(accessToken)
		}
	);
	// An outage must not render as "no transactions yet" — the page shows a retry banner instead.
	const page: ListingPage<Transaction> = res.ok
		? await res.json()
		: { items: [], total: 0, facets: {} };
	return {
		transactions: page.items,
		matchCount: page.total,
		facets: page.facets,
		// The slider track — see `ListingPage.bounds`.
		bounds: page.bounds ?? {},
		loadError: !res.ok
	};
}
