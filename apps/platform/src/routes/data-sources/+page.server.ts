import { error, redirect } from '@sveltejs/kit';
import type { DataSource } from '@graphene/ui/book/platforms';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * Where this firm's data comes from — `GET /firm/data-sources` waterfalled into the portal.
 *
 * The firm had no view of this at all: whether its book was current was answerable only from
 * Graphene's operations console, so its advisers read a book with no way to tell "this client has
 * no transactions" from "nothing has landed since Tuesday".
 *
 * There is no tenant parameter and there is nothing to pass: the API resolves the firm from the
 * caller's verified identity, the same posture as `/firm/audit/actions`. Read-only by design —
 * which custodians a firm is connected to, and the slice of the warehouse that is theirs, stay
 * Graphene's to set (`data_sources:read` has no write sibling).
 */
/** The page's one read, streamed: the sources, or why there are none. */
export type SourcesFeed = { sources: DataSource[]; loadError: string | null };

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'data_sources:read')) error(403, 'Not authorised');

	// The gates above are SYNCHRONOUS and the list is STREAMED: the heading renders the moment the
	// page is navigated to and the custodians land behind it, rather than the first byte waiting on
	// the API (`clients/+page.server.ts` is the pattern). `redirect`/`error` stay out here — they
	// have to be thrown before the response starts.
	return { feed: loadSources(locals.accessToken, fetch) };
};

async function loadSources(
	accessToken: string | null,
	fetch: typeof globalThis.fetch
): Promise<SourcesFeed> {
	// A failure is stated, never rendered as "no custodians are connected" — that reading is the
	// exact confusion this page exists to remove. Caught too: a streamed promise that rejects is a
	// broken page rather than a banner.
	try {
		const res = await fetch(`${apiUrl()}/firm/data-sources`, {
			headers: authHeaders(accessToken)
		});
		if (!res.ok) return { sources: [], loadError: await detail(res) };
		return { sources: (await res.json()) as DataSource[], loadError: null };
	} catch {
		return {
			sources: [],
			loadError: 'Could not load your data sources. Check your connection and try again.'
		};
	}
}
