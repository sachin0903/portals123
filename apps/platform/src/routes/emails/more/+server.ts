import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * Same-origin filter/paging proxy for the Activity feed. The browser cannot call the API directly,
 * so this forwards the query with the session token.
 *
 * Every parameter is forwarded by NAME from a fixed list rather than passed through wholesale: an
 * unknown parameter reaching the API would be ignored silently, and a `tenant` one would be a
 * param the firm endpoint has no name for. The firm scope comes from the session either way
 * (`/firm/…` runs on `firm_session`), so this proxy cannot be pointed at another firm's ledger.
 *
 * A failure answers NON-2xx rather than a swallowed empty 200 — an empty page during an outage
 * would read as "no more messages" and take the "Load more" button away.
 */
// `sort`/`dir`/`offset` travel together: the keyset cursor belongs to the default order alone.
const FORWARDED = [
	'limit',
	'before',
	'after',
	'status',
	'template_code',
	'category',
	'event_type',
	'q',
	'since',
	'until',
	'sort',
	'dir',
	'offset'
] as const;

export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	if (!locals.user) return new Response('Unauthorised', { status: 401 });
	const params = new URLSearchParams();
	for (const key of FORWARDED) {
		const value = url.searchParams.get(key);
		if (value) params.set(key, value);
	}
	const res = await fetch(`${apiUrl()}/firm/comms/messages?${params}`, {
		headers: authHeaders(locals.accessToken)
	});
	return new Response(await res.text(), {
		status: res.status,
		headers: { 'content-type': 'application/json' }
	});
};
