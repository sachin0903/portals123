import { actionLogFiltersFromSearchParams, actionLogQuery } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { fetchActionsPage } from '$lib/audit/actions-page';
import type { RequestHandler } from './$types';

/**
 * Same-origin "Load more" for one firm's Events slice. Forwards the next keyset page (id <
 * `before`) with the session token, the firm fixed to the section's uid (`?tenant=`, matched on the
 * acting AND proxied-target sides by the API) plus the non-firm filters the slice is showing.
 *
 * The EventStream appends the result in place — no navigation, no growing `?limit=` in the URL.
 *
 * Both halves of the recipe are shared: `actionLogQuery` owns the filter→API-query mapping (so the
 * exclusive-`until` and tri-state-`proxied` rules cannot drift from the loader's), and
 * `fetchActionsPage` owns the call and its failure contract — a failure answers NON-2xx, never a
 * swallowed empty 200, so the feed keeps its "Load more" button rather than falsely showing "End of
 * events" during an outage.
 *
 * `PAGE_SIZE` is the shared listing page size, so an appended page matches the first.
 */

export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	const query = actionLogQuery(actionLogFiltersFromSearchParams(url.searchParams), {
		limit: PAGE_SIZE,
		before: url.searchParams.get('before'),
		// The live poll's end of the keyset: rows NEWER than this id. Same proxy, same filters, same
		// failure contract — a poll must be scoped and filtered exactly like the page it refreshes.
		after: url.searchParams.get('after')
	});
	// The firm is this section's SCOPE, not a filter a reader can clear — so it is set here, AFTER
	// the builder, and overwrites any `?tenant=` the browser sent. `params.uid` is a route param and
	// therefore always present (typed `string` by `./$types`): the old `params.uid ?? ''` would have
	// sent `tenant=`, which the API reads as a filter for the empty string rather than as "no
	// filter", quietly answering with an empty slice.
	query.set('tenant', params.uid);
	return fetchActionsPage(fetch, '/admin/audit/actions', query, locals);
};
