import { error, redirect } from '@sveltejs/kit';
import { type ActionEvent, type ActionLogTenant } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

/** The action-event stream (audit.actions) — every action anyone took, across all three portals.
 *  Its own page now (was the "Actions" tab of /audit); the DML record-change trail keeps /audit.
 *
 *  FILTERS ARE APPLIED BY THE API — the firm filter included — and this loader asks the UNFILTERED
 *  question. Two decisions a reader will question, so both are written down:
 *
 *    * SERVER-SIDE, because a filter has to cover the WHOLE stream. Narrowing the fifty rows that
 *      happen to be loaded answers a smaller question than the one asked, and on an evidence surface
 *      "nobody did that" and "nobody did that in the newest page" must not be spelled the same way.
 *      A dropdown change therefore costs a round trip, which is the accepted price.
 *    * NOT IN THE URL: nothing is read from it here and nothing is written to it by the page, so the
 *      address bar stays clean and a reload resets to unfiltered. Do not reinstate `?tenant=`/
 *      `?event_type=`/… handling — reading filters the page never writes is the worst of the three
 *      options, because arriving on an old filtered link and then moving a dropdown would leave the
 *      URL stale and a reload would jump back to it. Page one at the new filters is fetched in place
 *      through `/activity/more` instead. */

/** The first page, at page entry only. Every later page — a filter applied in place, or "Load more" —
 *  comes from `/activity/more`, so the URL never carries a growing limit and the loader is always just
 *  the first page. The filter→query mapping lives once, in `@graphene/ui`'s `actionLogQuery`, which
 *  the page and that proxy both reach. The size is the product-wide listing one. */
const ACTIONS_PAGE_SIZE = PAGE_SIZE;

/** The filter vocabulary the API computes over the caller's whole recent scope — event types and
 *  actors, deliberately independent of the filters in force, so applying one filter can no longer
 *  withdraw another's choices. Read ONCE on entry: the list is meant not to move when a filter is
 *  applied, so no later fetch asks for it. Declared locally until `@graphene/api-client` is
 *  regenerated for the key. */
type ActionFacets = { event_types: string[]; actors: string[] };

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'audit:read')) error(403, 'Not authorised');

	let events: ActionEvent[] = [];
	let tenants: ActionLogTenant[] = [];
	let facets: ActionFacets | null = null;
	let hasMore = false;
	let apiError: string | null = null;

	// The newest page, unfiltered, plus the two lookups the toolbar needs: `tenants` names the firms in
	// the firm dropdown, `facets` states the event-type and actor vocabulary. A URL-supplied
	// `limit`/`before` is deliberately ignored — the loader always reads the FIRST page at the page
	// size, and the list grows in place via "Load more".
	//
	// The facets stay ESTATE-WIDE even after a firm is chosen, and that is the contract rather than an
	// oversight: the firm is a filter here (it travels in `ActionLogFilters.tenant`), and re-reading the
	// vocabulary per filter is exactly how a bar comes to eat its own options — pick an actor, and the
	// event-type list offers only what that actor did, with no way back to a value you can no longer
	// see. One scan, on entry, describing the whole readable scope.
	const query = new URLSearchParams({ limit: String(ACTIONS_PAGE_SIZE), facets: 'true' });
	try {
		const res = await fetch(`${apiUrl()}/admin/audit/actions?${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) {
			const page = (await res.json()) as {
				items: ActionEvent[];
				has_more: boolean;
				tenants: ActionLogTenant[];
				facets?: ActionFacets | null;
			};
			events = page.items;
			hasMore = page.has_more;
			tenants = page.tenants;
			// Read DEFENSIVELY: the key is absent on an API that predates it and null whenever facets were
			// not asked for, and neither is an error. No vocabulary means the toolbar falls back to the
			// loaded rows' own options — a shorter dropdown, never a failed page.
			facets = page.facets ?? null;
		} else if (res.status === 403) apiError = 'audit:read required to view the event stream.';
		else apiError = await detail(res);
	} catch {
		apiError = 'The service is unavailable.';
	}

	return { events, tenants, facets, hasMore, apiError };
};
