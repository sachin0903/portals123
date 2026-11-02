import { error, redirect } from '@sveltejs/kit';
import type { ActionEvent } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * This client's slice of the firm's action-event stream — every event that NAMES the client
 * (`GET /firm/audit/actions?contact=<uid>`: its own-record edits, documents, tasks, instructions,
 * illustrations …), the record's "Events" section. It replaced the old documents-only Activity tab.
 *
 * Scoped server-side to the contact (the API's `contact` filter), so it can neither miss nor leak
 * across clients; gated on `activity:read` (the same permission the firm-wide Events page uses),
 * NOT `documents:read` — the tab now reads the whole action stream, not the document trail.
 *
 * FILTERS ARE APPLIED BY THE API and this loader asks the UNFILTERED question for the client. The two
 * decisions behind that, because a reader will question both: SERVER-SIDE, so a filter covers the
 * client's whole trail rather than the fifty rows that happen to be loaded (a filter that answers
 * "nothing in the newest page" as though it were "nothing ever" is worse than no filter on a
 * compliance record); and NOT IN THE URL, so nothing is read from it or written to it here, the
 * address bar stays clean and a reload resets to unfiltered. The page fetches page one in place
 * instead (`…/activity/more`), which is also where "Load more" goes.
 *
 * `contact` is the one param that still matters here, and it is a SCOPE, not a filter: it is the
 * route's own `[uid]`, set after the query is built so nothing the browser sent can widen or
 * re-point it — the rule `…/activity/more` already follows.
 *
 * The page size is the shared listing one, so this feed pages like every other listing.
 */

/** The API's complete filter VOCABULARY (`?facets=true`) — the event types and actors the toolbar's
 *  dropdowns offer, computed independently of the filters in force so choosing one can never shrink
 *  another's choices. Note the scope: the API computes facets over the caller's READABLE scope, which
 *  on this endpoint is the whole firm — `contact` narrows the rows, not the vocabulary — so a dropdown
 *  here can offer a type or an actor with no events on THIS client. A complete list with a dead option
 *  beats a list that silently withdraws the value you wanted to switch to; narrowing it needs
 *  `contact` inside the facet scope, which is an API-side change. Declared locally until
 *  `@graphene/api-client` is regenerated for the key. */
type ActionFacets = { event_types: string[]; actors: string[] };

export const load: PageServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { canReadActivity, clientName } = await parent();
	if (!canReadActivity) error(403, 'You do not have permission to view this client’s events.');

	return {
		feed: loadFeed(locals.accessToken, fetch, params.uid, clientName)
	};
};

async function loadFeed(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string,
	clientName: string
) {
	// The newest page for this client, unfiltered, plus the toolbar's vocabulary. `contact` is the
	// scope (from `parent()`), never a URL param; nothing else is read from the URL. `facets` is asked
	// for ONCE, here — the list is meant not to move when a filter is applied, so no later fetch
	// requests it and the extra scan is a page-entry cost only.
	// `?contact=` takes the client's UID — the route's own param, exactly as `…/activity/more`
	// sends it. It was the SERIAL here, which matches no uid, and an unresolvable filter narrows to
	// NOTHING rather than failing: `_contact_filter_id` returns None and the stream's `:contact`
	// binds NULL, which means NO NARROWING. So page one was the whole firm's stream while Load more
	// was correctly this client's — the population changed as you scrolled.
	const query = new URLSearchParams({
		limit: String(PAGE_SIZE),
		contact: uid,
		facets: 'true'
	});

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
			// Read DEFENSIVELY: the key is absent on an API that predates it and null whenever facets were
			// not asked for, and neither is an error. No vocabulary means the toolbar falls back to the
			// loaded rows' own options — a shorter dropdown, never a failed page.
			facets = page.facets ?? null;
		} else if (res.status === 403) {
			apiError = 'You cannot view this client’s events. Ask a firm administrator for access.';
		} else apiError = await detail(res);
	} catch {
		apiError = 'Could not load this client’s events. Check your connection and try again.';
	}

	return { events, facets, hasMore, apiError, client: { uid, name: clientName } };
}
