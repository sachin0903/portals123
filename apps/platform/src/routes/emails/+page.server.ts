import { error, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import type { CommsFacets, CommsMessage } from '@graphene/ui/comms';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * Activity — every email this firm has sent, with what the provider did with it (docs/comms.md).
 *
 * The question it answers is the one the action trail cannot: `/activity` records that a document WAS
 * SENT; this records that the email went to an address and was delivered at 14:02, or bounced.
 * Until the ledger existed there was no answer at all — `send_email` returned a boolean and wrote
 * nothing down.
 *
 * Gated on `activity:read`, the same permission as `/activity`: both answer "what has this firm been
 * doing", and a firm administrator who may read one has no reason to be refused the other.
 *
 * Filters are applied SERVER-SIDE and deliberately never written to the URL — the doctrine `/activity`
 * already follows. Page one is fetched here unfiltered; every filter and every "load more" from
 * then on goes through `activity/more` and is appended or swapped in place.
 *
 * ONE EXCEPTION, and it is INBOUND ONLY: `?event_type=` opens the page already narrowed to one
 * event. The event registry's 30-day count links here for exactly that, and without it the link
 * landed on an unfiltered feed — a count that leads nowhere. Reading a deep link is not the same
 * as writing filters back to the URL, which this still never does.
 */
export const load: PageServerLoad = async ({ locals, url, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'activity:read')) error(403, 'Not authorised');

	// One value, trimmed and length-capped: it lands in an API query, and while the endpoint
	// validates it too there is no reason to forward whatever somebody pasted into the address bar.
	const eventType = (url.searchParams.get('event_type') ?? '').trim().slice(0, 100);

	// The gates and the deep link are resolved SYNCHRONOUSLY and the feed is STREAMED: the frame
	// (with the toolbar already narrowed to `eventType`) renders the moment the page is navigated to
	// and the rows land behind it — the same shape as the client record's Activity tab. `redirect`
	// and `error` stay out here, thrown before the response starts.
	return { eventType, feed: loadFeed(locals.accessToken, fetch, eventType) };
};

async function loadFeed(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	eventType: string
) {
	// The vocabulary is asked for ONCE, here, for the reason the logs loader records: a filter is
	// applied in place from now on, so no later fetch pays for the facet scan.
	const query = new URLSearchParams({ limit: String(PAGE_SIZE), facets: 'true' });
	if (eventType) query.set('event_type', eventType);

	let messages: CommsMessage[] = [];
	let facets: CommsFacets | null = null;
	let hasMore = false;
	let apiError: string | null = null;
	/** Whether sending is switched off. Without it a wall of "Not sent" reads as "every email is
	 *  failing" rather than as sending being off. The REASON is never forwarded: it names
	 *  configuration, which a firm cannot act on and which would ship in the page's own data. */
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
			// Defensively: absent on an API that predates the key, null whenever facets were not
			// asked for, and neither is an error — the toolbar falls back to the loaded rows.
			facets = page.facets ?? null;
			// Comes from the feed's OWN response, not from `/health/ready`: that probe is
			// infrastructure (out of the OpenAPI schema, 503s on unrelated outages, and no portal
			// calls it), and this is product context that belongs with the rows it explains.
			sendingDisabled = Boolean(page.sending_disabled);
		} else if (res.status === 403) {
			apiError = 'You cannot view sent email. Ask a firm administrator for access.';
		} else {
			apiError = await detail(res);
		}
	} catch {
		apiError = 'Could not load sent email. Check your connection and try again.';
	}

	return { messages, facets, hasMore, apiError, sendingDisabled };
}
