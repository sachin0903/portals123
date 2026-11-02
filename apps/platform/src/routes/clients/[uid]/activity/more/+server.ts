import { actionLogFiltersFromSearchParams, actionLogQuery } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { failed, fetchActionsPage } from '$lib/audit/actions-page';
import type { RequestHandler } from './$types';

/**
 * Same-origin "Load more" for a client's Events slice. Forwards the next keyset page (id < `before`)
 * with the session token, the client fixed to the section's contact id (`?contact=`), plus the
 * filters the slice is showing. The EventStream appends the result in place — no navigation, no
 * growing `?limit=` in the URL.
 *
 * Both halves of the recipe are shared: `actionLogQuery` owns the filter→API-query mapping (so the
 * exclusive-`until` and tri-state-`proxied` rules cannot drift from the loader's), and
 * `fetchActionsPage` owns the call and its failure contract — a failure answers NON-2xx, never a
 * swallowed empty 200, so the feed keeps its "Load more" button rather than falsely showing "End of
 * events" during an outage.
 */
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	// Before anything else, and before the resolve below: an expired session is a 401, and asking
	// the API to resolve a uid with no token would answer 401 in a shape this contract does not own.
	if (!locals.user) return failed(401);
	const filters = actionLogFiltersFromSearchParams(url.searchParams);
	const query = actionLogQuery(
		// `tenant` is dropped: the firm scope comes from the session (there is deliberately no
		// `?tenant=` on the firm endpoint), and this surface has no firm dropdown to set one.
		{ ...filters, tenant: [] },
		{ limit: PAGE_SIZE, before: url.searchParams.get('before') }
	);
	// The client is this section's SCOPE, not a filter a reader can clear — set here, after the
	// builder, so it cannot be widened or re-pointed by the query the browser sent. `?contact=`
	// takes the client's UID, so the route's own parameter is the whole of it: nothing is resolved,
	// and an unknown uid narrows to nothing rather than failing the page.
	query.set('contact', params.uid);
	return fetchActionsPage(fetch, '/firm/audit/actions', query, locals);
};
