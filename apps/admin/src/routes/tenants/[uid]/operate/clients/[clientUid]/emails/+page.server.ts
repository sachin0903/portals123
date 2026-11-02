import { redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import type { CommsFacets, CommsMessage } from '@graphene/ui/comms';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

/** This client's slice of the firm's outbound email — the platform's `clients/[uid]/emails`.
 *  `contact` is the route's own uid, set here and again in `./page`, never read from the browser. */
export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	return { feed: loadFeed(fetch, locals.accessToken, tenant.uid, params.clientUid) };
};

async function loadFeed(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	clientUid: string
) {
	const result = await targetJson<{
		items: CommsMessage[];
		has_more: boolean;
		facets?: CommsFacets | null;
		sending_disabled?: string | null;
	}>(fetch, accessToken, tenantUid, '/firm/comms/messages', {
		limit: PAGE_SIZE,
		contact: clientUid,
		facets: 'true'
	});
	return {
		messages: result.ok ? result.data.items : [],
		facets: result.ok ? (result.data.facets ?? null) : null,
		hasMore: result.ok ? result.data.has_more : false,
		// A flag, never the reason: it names configuration, and page data is serialised into the HTML.
		sendingDisabled: result.ok && Boolean(result.data.sending_disabled),
		apiError: result.ok ? null : 'Could not load this client’s activity.'
	};
}
