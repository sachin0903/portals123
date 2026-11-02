import { error, redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import type { CommsFacets, CommsMessage } from '@graphene/ui/comms';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * This client's slice of the firm's outbound email — the record's "Activity" section.
 *
 * The firm-wide `/emails` answers "what has this firm sent"; on a record the question is narrower
 * and more often asked: did THIS client get the invite, and did it arrive? It is the counterpart to
 * the Events tab beside it, and the pair answer different questions — Events records that a
 * document WAS SENT, Activity records that the email reached an address and was delivered at 14:02,
 * or bounced (`docs/comms.md`).
 *
 * Gated on `activity:read`, the same permission as the Events tab and the firm-wide feed: both
 * answer "what has been done here", and someone who may read one has no reason to be refused the
 * other.
 *
 * `contact` is the SCOPE, not a filter a reader can clear: it is the route's own `[uid]`, set on the
 * query here and again in `…/emails/more`, never read from the browser's parameters. It takes the
 * UID — the same spelling as the action stream's `?contact=`, so the record's two feeds narrow the
 * same way — and an unresolvable one narrows to nothing rather than widening to the firm.
 *
 * Filters are applied SERVER-SIDE and never written to the URL, the doctrine both the firm-wide
 * feed and the Events tab already follow: a filter must cover this client's whole trail rather than
 * the page that happens to be loaded, and a reload should reset to unfiltered.
 */
export const load: PageServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { canReadActivity, clientName } = await parent();
	if (!canReadActivity) error(403, 'You do not have permission to view this client’s activity.');

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
	// The vocabulary is asked for ONCE, here, for the reason the other two feeds record: every later
	// filter is applied in place, so no subsequent fetch pays for the facet scan.
	const query = new URLSearchParams({
		limit: String(PAGE_SIZE),
		contact: uid,
		facets: 'true'
	});

	let messages: CommsMessage[] = [];
	let facets: CommsFacets | null = null;
	let hasMore = false;
	let apiError: string | null = null;
	/** Whether sending is switched off. Without it a wall of "Not sent" reads as "every email to
	 *  this client is failing". A flag only: the reason names configuration and must not ship in
	 *  the page's own data. */
	let sendingDisabled = false;

	try {
		const res = await fetch(`${apiUrl()}/firm/comms/messages?${query}`, {
			headers: authHeaders(accessToken)
		});
		if (res.ok) {
			const page = (await res.json()) as {
				items: CommsMessage[];
				has_more: boolean;
				facets?: CommsFacets | null;
				sending_disabled?: string | null;
			};
			messages = page.items;
			hasMore = page.has_more;
			// Read DEFENSIVELY: absent on an API that predates the key, null whenever facets were not
			// asked for, and neither is an error — a shorter dropdown, never a failed page.
			facets = page.facets ?? null;
			sendingDisabled = Boolean(page.sending_disabled);
		} else if (res.status === 403) {
			apiError = 'You cannot view this client’s activity. Ask a firm administrator for access.';
		} else apiError = await detail(res);
	} catch {
		apiError = 'Could not load this client’s activity. Check your connection and try again.';
	}

	return {
		messages,
		facets,
		hasMore,
		apiError,
		sendingDisabled,
		client: { uid, name: clientName }
	};
}
