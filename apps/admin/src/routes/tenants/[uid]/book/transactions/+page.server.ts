import { redirect } from '@sveltejs/kit';
import { authHeaders } from '$lib/api';
import { bookUrl } from '$lib/book/api';
import type { Transaction } from '@graphene/ui';
import { apiParams, queryFromParams, PAGE_SIZE, type ListingPage } from '@graphene/ui/book/listing';
import type { PageServerLoad } from './$types';

/** The filters this listing understands. Named so an unrelated query param is not forwarded. */
const FILTER_KEYS = [
	'status',
	'adviser',
	'custodian',
	'type',
	'amount',
	'quantity',
	'currency'
] as const;

/** The product-wide listing page size, which is what the platform's own ledger page uses. */
const TRANSACTIONS_PAGE_SIZE = PAGE_SIZE;

/**
 * The target firm's transactions — the platform's `/transactions` loader with `?tenant=`.
 *
 * This page served `@graphene/ui`'s fixture corpus, and the comment saying so claimed it was
 * "exactly as the platform's /transactions page". The platform page swapped to the real feed and
 * this one did not follow, so every firm in the ops book showed the SAME sixteen invented rows.
 * That is worse than the blank page a missing read would have given: an operator cannot tell an
 * invented ledger from a real one, so the caveat lived only in a comment nobody reading the screen
 * could see.
 *
 * `GET /crm/transactions` reads `platform.transactions` — every provider, with the account, client
 * and adviser joined on — and `book_session` scopes it to the `?tenant=` target under `book:read`.
 * Search, filters and paging are applied by the API, exactly as on the platform page: filtering one
 * page here would report "no transaction for Halloway" when it means "not on this page".
 */
export const load: PageServerLoad = async ({ locals, fetch, url, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();

	// The left column — see `ListingQuery.defaultSort`.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, {
		limit: TRANSACTIONS_PAGE_SIZE,
		sort: 'date',
		dir: 'desc'
	});
	const target = bookUrl('/crm/transactions', tenant.uid, { paged: 'true' });
	const res = await fetch(`${target}&${apiParams(query)}`, {
		headers: authHeaders(locals.accessToken)
	});

	// An empty page rather than a 500 — the listing renders its own empty state — but the failure is
	// REPORTED, because "this firm has no transactions" is a real answer and must not be faked.
	const page: ListingPage<Transaction> = res.ok
		? await res.json()
		: { items: [], total: 0, facets: {} };
	return {
		transactions: page.items,
		matchCount: page.total,
		facets: page.facets,
		// The slider track — see `ListingPage.bounds`.
		bounds: page.bounds ?? {},
		query,
		loadError: !res.ok
	};
};
