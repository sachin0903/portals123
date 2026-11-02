import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';

/**
 * The FETCH half of the trail "Load more" proxies (`/activity/more`, `/clients/[uid]/activity/more`) —
 * the call, and the contract it answers with.
 *
 * It lives per app rather than in `@graphene/ui` beside `actionLogQuery` (the pure filter→query
 * builder it partners) because `apiUrl`/`authHeaders` read server-only env through `$lib/api`, which
 * a package consumed as source by three portals cannot import. So the mapping is shared and this
 * is duplicated — one small file, with nothing in it but the recipe below. The admin portal carries
 * the same file for its three proxies.
 *
 * THE FAILURE CONTRACT, which is the whole reason this is a function rather than five copies of six
 * lines. Every caller maps `!res.ok` to `null` and keeps its "Load more" button; a 2xx is read as a
 * real page. So a failure must answer NON-2xx — 401 for no session, 502 for an upstream failure —
 * and never a 200 carrying an empty list.
 *
 * A 200 `{events: [], hasMore: false}` is indistinguishable, at the caller, from "you have reached
 * the end of the trail". The reader is told the whole record is loaded when a request merely broke,
 * the feed stops paginating, and the trail is silently truncated at whatever page the outage landed
 * on — with no error anywhere to say so. For a compliance record that is the worst answer available,
 * and it is the one each hand-rolled copy of this recipe reached for. Keeping it in one place is how
 * the four proxies that were fixed by hand stop being four places it can come back.
 */

/** Just the two `locals` fields the contract needs, structurally — so a unit test can hand it a
 *  plain object, and neither app's `App.Locals` has to be named here. */
export type TrailSession = { user?: unknown; accessToken: string | null };

/**
 * A paged trail response.
 *
 * `has_more` is authoritative wherever the endpoint carries it (the action stream — `GET
 * /firm/audit/actions` — computes it from an over-fetch). The admin portal's DML record-change trail
 * (`GET /admin/audit`) carries none, so there a FULL page is the signal that there is more behind
 * it. Both are read here, so this file stays the same in both portals and a caller never has to say
 * which kind of endpoint it is talking to.
 */
type TrailPage = { items?: unknown[] | null; has_more?: boolean | null };

/** The failure answer: non-2xx, with the same body shape a success carries so a caller that ignores
 *  the status still sees an empty page rather than a parse error.
 *
 *  Exported because a proxy can have work to do BEFORE the fetch — resolving the route's uid to the
 *  serial the API filters on — and a failure there has to answer in this shape too. Throwing
 *  instead would reach the caller as a 500 with an HTML-ish body, which `loadMore` reads as
 *  `!res.ok` and so happens to survive; but the contract is that this file decides what a failure
 *  looks like, and one definition is why it does. */
export const failed = (status: 401 | 502): Response =>
	json({ events: [], hasMore: false }, { status });

/** The page size that was ASKED FOR, read back out of the query we are about to send — so the
 *  full-page test above cannot be computed against a different number than the one on the wire.
 *  `null` when there is no usable `limit`, which suppresses the fallback rather than guessing. */
function limitOf(query: URLSearchParams): number | null {
	const limit = Number(query.get('limit'));
	return Number.isInteger(limit) && limit > 0 ? limit : null;
}

/**
 * Fetch one keyset page of a trail endpoint and answer in the `{events, hasMore}` shape every
 * "Load more" caller reads.
 *
 * `path` is the API path (`/firm/audit/actions`); `query` is what the caller built, normally with
 * `actionLogQuery` plus whatever fixed scope the surface pins (`contact`).
 */
export async function fetchActionsPage(
	fetch: typeof globalThis.fetch,
	path: string,
	query: URLSearchParams,
	locals: TrailSession
): Promise<Response> {
	// An expired session is a 401, never an empty page: "you are signed out" must not reach the
	// reader as "that is the end of the trail".
	if (!locals.user) return failed(401);
	try {
		const res = await fetch(`${apiUrl()}${path}?${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		// Includes the API's own 403 and 422: a refusal is something to report, not a last page.
		if (!res.ok) return failed(502);
		const page = (await res.json()) as TrailPage;
		const events = page.items ?? [];
		const size = limitOf(query);
		return json({ events, hasMore: page.has_more ?? (size !== null && events.length >= size) });
	} catch {
		return failed(502);
	}
}
