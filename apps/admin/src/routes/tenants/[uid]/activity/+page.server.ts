import { error, redirect } from '@sveltejs/kit';
import type { ActionEvent } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

/**
 * This firm's slice of the action-event stream (`audit.actions`) — its own staff's actions AND
 * Graphene support acting on its behalf (proxied `?tenant=` rows, which the API matches on the
 * TARGET side too). Scoped to the section's firm via `?tenant=<uid>`; there is no firm filter here
 * (the firm is the URL), the standalone `/events` page is the cross-firm oversight surface.
 *
 * Gated on `audit:read` — the same permission as the global surface; narrowing to one firm is a
 * filter, not a privilege. This is the audit trail, distinct from `book:read` (client data), so an
 * operator who can read the trail but not the book still sees a firm's activity.
 *
 * FILTERS ARE APPLIED BY THE API and this loader asks the UNFILTERED question for the firm. The two
 * decisions behind that, because a reader will question both: SERVER-SIDE, so a filter covers this
 * firm's WHOLE slice rather than the fifty rows that happen to be loaded (on an evidence surface,
 * "nobody did that" and "nobody did that in the newest page" must not be spelled the same way); and
 * NOT IN THE URL, so nothing is read from it or written to it here, the address bar stays clean and a
 * reload resets to unfiltered. Page one at the new filters is fetched in place through `…/activity/more`,
 * which is also where "Load more" goes.
 */

/** The first page, at page entry only; later pages — filtered or paged — come from `…/activity/more`.
 *  The URL never carries a growing limit: the list grows in place. The size is the product-wide
 *  listing one. */
const ACTIONS_PAGE_SIZE = PAGE_SIZE;

/** The API's complete filter VOCABULARY for this firm's slice (`?facets=true`) — the event types and
 *  actors the toolbar's dropdowns offer, computed over the firm's recent stream and independently of
 *  the filters in force, so choosing one can never shrink another's choices. Scoped by the SAME
 *  `?tenant=` predicate as the rows, so it is this firm's vocabulary and not the estate's. Declared
 *  locally until `@graphene/api-client` is regenerated for the key. */
type ActionFacets = { event_types: string[]; actors: string[] };

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'audit:read')) error(403, 'Not authorised');

	let events: ActionEvent[] = [];
	let facets: ActionFacets | null = null;
	let hasMore = false;
	let apiError: string | null = null;

	// The newest page for this firm, unfiltered, plus the toolbar's vocabulary. `tenant` is the one
	// param that still matters, and it is the section's SCOPE rather than a filter: pinned from the
	// route param so it cannot be widened or re-pointed. (`params.uid` is always present and typed
	// `string`; a `?? ''` fallback would send `tenant=`, which the API reads as a filter for the empty
	// string and answers with an empty slice.) `facets` is asked for ONCE, here — the list is meant not
	// to move when a filter is applied, so no later fetch requests it.
	const query = new URLSearchParams({
		limit: String(ACTIONS_PAGE_SIZE),
		tenant: params.uid,
		facets: 'true'
	});
	try {
		const res = await fetch(`${apiUrl()}/admin/audit/actions?${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) {
			const page = (await res.json()) as {
				items: ActionEvent[];
				has_more: boolean;
				facets?: ActionFacets | null;
			};
			events = page.items;
			hasMore = page.has_more;
			// Read DEFENSIVELY: the key is absent on an API that predates it and null whenever facets were
			// not asked for, and neither is an error. No vocabulary means the toolbar falls back to the
			// loaded rows' own options — a shorter dropdown, never a failed page.
			facets = page.facets ?? null;
		} else if (res.status === 403) apiError = 'audit:read required to view the event stream.';
		else apiError = await detail(res);
	} catch {
		apiError = 'The service is unavailable.';
	}

	return { events, facets, hasMore, apiError, tenantUid: params.uid };
};
