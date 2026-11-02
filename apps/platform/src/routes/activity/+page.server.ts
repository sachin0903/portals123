import { error, redirect } from '@sveltejs/kit';
import type { ActionEvent } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * The firm's own action trail — `GET /firm/audit/actions` waterfalled into the platform portal.
 *
 * The same reading as the admin portal's Actions surface, minus what a single-firm page has no use
 * for: there is no firm filter (the API hard-codes the scope to the caller's own tenant — acting
 * side AND proxied-target side) and no record-changes tab (audit.log stays a Graphene surface). The
 * AML event family never appears here: excluded by the API (routers/firm.py), not by anything this
 * page could get wrong.
 *
 * FILTERS ARE APPLIED BY THE API, and this loader asks the UNFILTERED question. Two decisions a
 * reader will question, so both are written down:
 *
 *   * SERVER-SIDE, because a filter has to cover the WHOLE trail. Narrowing the rows that happen to
 *     be loaded answers a smaller question than the one asked — "nobody did that" and "nobody did
 *     that in the newest fifty rows" are different answers, and on a compliance record only one of
 *     them is true. The cost is accepted: a dropdown change is a round trip.
 *   * NOT IN THE URL. None is read here and none is written by the page, so the address bar stays
 *     clean and a reload resets to the unfiltered view. Do not reinstate `?event_type=`/`?actor=`/…
 *     handling: reading filters the page never writes is the worst of the three options, because
 *     arriving on an old filtered link and then moving a dropdown would leave the URL stale and a
 *     reload would jump back to it. The page fetches page one in place instead (`/activity/more`).
 *
 * So the loader's whole job is ENTRY: the first page at the page size, plus the toolbar's option
 * vocabulary. Both are read once; every later page — filtered or paged — comes from the proxy.
 *
 * The page size is the product-wide listing one, so this feed pages like every other listing.
 */

/** The API's complete filter VOCABULARY for this firm's slice (`?facets=true`) — the event types and
 *  actors the toolbar's dropdowns offer, computed over the firm's recent stream and deliberately
 *  INDEPENDENT of the filters in force, so choosing one filter can never shrink another's choices.
 *  Withheld through the same AML deny list as the rows (a dropdown entry is a disclosure too —
 *  routers/firm.py). Declared locally until `@graphene/api-client` is regenerated for the key. */
type ActionFacets = { event_types: string[]; actors: string[] };

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'activity:read')) error(403, 'Not authorised');

	/** Whether to offer the link to the event registry (`/activity/notifications`), where a firm chooses
	 *  what each event notifies. Gated on the REGISTRY's own permission, not this page's: a reader
	 *  who may see the trail but not the notification config must not be handed a button the route
	 *  would refuse — the nav's own rule ("hide a tab rather than dangle a 403 trap"), applied to a
	 *  button. The registry has no nav item, so this is how it is reached.
	 */
	const canConfigure = can(locals.permissions, locals.role, 'notifications:read');

	// The gates above are SYNCHRONOUS and the feed is STREAMED: the page's frame renders the moment it
	// is navigated to and the rows land behind it, rather than the click buying a second of the
	// previous page while the API answers (the same shape as the client record's own Events tab,
	// and `clients/+page.server.ts`). `redirect`/`error` stay out here — they have to be thrown
	// before the response starts, not from inside a streamed promise.
	return { canConfigure, feed: loadFeed(locals.accessToken, fetch) };
};

async function loadFeed(accessToken: string | null, fetch: typeof globalThis.fetch) {
	// The newest page, unfiltered, plus the vocabulary. `facets` is asked for ONCE, here: a filter is
	// applied in place from now on, and the whole point of the list is that it does not move when one
	// is — so no later fetch requests it and the extra scan is a page-entry cost only.
	const query = new URLSearchParams({ limit: String(PAGE_SIZE), facets: 'true' });

	let events: ActionEvent[] = [];
	let facets: ActionFacets | null = null;
	let hasMore = false;
	let apiError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/firm/audit/actions?${query}`, {
			headers: authHeaders(accessToken)
		});
		if (res.ok) {
			const page = (await res.json()) as {
				items: ActionEvent[];
				has_more: boolean;
				facets?: ActionFacets | null;
			};
			events = page.items;
			hasMore = page.has_more;
			// Read DEFENSIVELY: the key is absent on an API that predates it and null whenever facets
			// were not asked for, and neither is an error. No vocabulary means the toolbar falls back to
			// the loaded rows' own options — a shorter dropdown, never a failed page.
			facets = page.facets ?? null;
		} else if (res.status === 403)
			apiError = 'You cannot view the event log. Ask a firm administrator for access.';
		else apiError = await detail(res);
	} catch {
		apiError = 'Could not load the event log. Check your connection and try again.';
	}

	return { events, facets, hasMore, apiError };
}
