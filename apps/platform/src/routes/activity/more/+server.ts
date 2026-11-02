import { actionLogFiltersFromSearchParams, actionLogQuery } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { fetchActionsPage } from '$lib/audit/actions-page';
import type { RequestHandler } from './$types';

/**
 * Same-origin "Load more" for the firm Events feed. The browser can't call the API directly, so
 * this forwards the next keyset page (events with a lower id than `before`) with the session token,
 * carrying the SAME filters the feed is showing so the appended page stays in the filtered set.
 *
 * The EventStream appends the result in place — no navigation, no growing `?limit=` in the URL.
 *
 * Both halves of the recipe are shared: `actionLogQuery` owns the filter→API-query mapping (so the
 * exclusive-`until` and tri-state-`proxied` rules cannot drift from the loader's), and
 * `fetchActionsPage` owns the call and its failure contract — a failure answers NON-2xx, never a
 * swallowed empty 200, so the feed keeps its "Load more" button rather than falsely showing "End of
 * events" during an outage.
 *
 * The firm scope is the SESSION's, applied by the API (`/firm/…` runs on `firm_session`), so there is
 * nothing to pin here: this proxy cannot be pointed at another firm's trail.
 *
 * `PAGE_SIZE` is the shared listing page size, so an appended page is the same size as the first.
 */

export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	const filters = actionLogFiltersFromSearchParams(url.searchParams);
	const query = actionLogQuery(
		// `tenant` is dropped rather than forwarded: there is deliberately no `?tenant=` on the firm
		// endpoint (the scope comes from the session), so sending one would be a param the API has no
		// name for. The toolbar has no firm dropdown on this surface, so the value can only arrive from
		// a hand-edited URL.
		{ ...filters, tenant: [] },
		{
			limit: PAGE_SIZE,
			before: url.searchParams.get('before'),
			// The live poll's end of the keyset: rows NEWER than this id. Same proxy, same filters, same
			// failure contract — a poll must be scoped and filtered exactly like the page it refreshes.
			after: url.searchParams.get('after')
		}
	);
	return fetchActionsPage(fetch, '/firm/audit/actions', query, locals);
};
