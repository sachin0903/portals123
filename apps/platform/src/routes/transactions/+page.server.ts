import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import type { Transaction } from '@graphene/ui';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { FILTER_KEYS, SORT } from './listing';
import type { PageServerLoad } from './$types';

/** Everything the listing has to fetch, in one streamed object. */
type Book = {
	transactions: Transaction[];
	matchCount: number;
	facets: Record<string, string[]>;
	bounds: Record<string, number[]>;
	loadError: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// The query is resolved here and the rows are STREAMED, so the page renders on the click (with
	// its toolbar already showing the filters in the URL) and fills in when the API answers.
	// `redirect` stays out here: it has to be thrown before the response starts.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, SORT);
	return { query, book: loadBook(locals.accessToken, fetch, query) };
};

async function loadBook(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: ReturnType<typeof queryFromParams>
): Promise<Book> {
	// REAL DATA. This page served `transactionFixtures` because `GET /crm/transactions` used to
	// read the Seccl lake and carried none of the joins a book listing needs. It now reads
	// `platform.transactions` — the conformed fact table the materialiser writes for every
	// provider — with the account, client, adviser and product beside each row. RLS scopes it.
	//
	// Search, filters and the page live in the URL and are applied by the API: filtering one page
	// client-side would report "no transaction for Halloway" when it means "not on this page".
	const res = await fetch(`${apiUrl()}/crm/transactions?paged=true&${apiParams(query)}`, {
		headers: authHeaders(accessToken)
	});
	// An empty page rather than a 500: the listing renders its own empty state, and a failed read
	// is not worth taking the route down for.
	const page: ListingPage<Transaction> = res.ok
		? await res.json()
		: { items: [], total: 0, facets: {} };
	return {
		transactions: page.items,
		matchCount: page.total,
		facets: page.facets,
		bounds: page.bounds ?? {},
		loadError: !res.ok
	};
}
