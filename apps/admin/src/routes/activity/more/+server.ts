import { actionLogFiltersFromSearchParams, actionLogQuery } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { fetchActionsPage } from '$lib/audit/actions-page';
import type { RequestHandler } from './$types';

/**
 * Same-origin "Load more" for the estate-wide Events stream. Forwards the next keyset page (id <
 * `before`) to the API with the session token, carrying the SAME filters (including the firm
 * filter) the stream is showing, so the appended page stays in the filtered set.
 *
 * The EventStream appends the result in place — no navigation, no growing `?limit=` in the URL.
 * The `tenants` lookup is not needed here (the loader already has it).
 *
 * Both halves of the recipe are shared: `actionLogQuery` owns the filter→API-query mapping (so the
 * exclusive-`until` and tri-state-`proxied` rules cannot drift from the loader's), and
 * `fetchActionsPage` owns the call and its failure contract — a failure answers NON-2xx, never a
 * swallowed empty 200, so the feed keeps its "Load more" button rather than falsely showing "End of
 * events" during an outage.
 *
 * `PAGE_SIZE` is the shared listing page size, so an appended page matches the first.
 */

export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	// The browser sends back the filters the stream is showing, under the same wire names the page
	// URL uses — so this reads them with the loader's own reader.
	const query = actionLogQuery(actionLogFiltersFromSearchParams(url.searchParams), {
		limit: PAGE_SIZE,
		before: url.searchParams.get('before'),
		// The live poll's end of the keyset: rows NEWER than this id. Same proxy, same filters, same
		// failure contract — a poll must be scoped and filtered exactly like the page it refreshes.
		after: url.searchParams.get('after')
	});
	return fetchActionsPage(fetch, '/admin/audit/actions', query, locals);
};
