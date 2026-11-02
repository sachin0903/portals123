import { apiUrl, authHeaders } from '$lib/api';
import type { RequestHandler } from './$types';

/**
 * Same-origin filter/paging proxy for ONE client's Activity feed. The browser cannot call the API
 * directly, so this forwards the query with the session token.
 *
 * Every parameter is forwarded by NAME from a fixed list rather than passed through wholesale — the
 * rule `/emails/more` already follows — and `contact` is deliberately NOT on that list. It is set
 * below, after the forwarding, so the client is a scope the browser cannot widen, re-point at
 * another client, or drop: a page that answered the firm's mail under one client's name is exactly
 * the defect this section was found to have.
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

export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) return new Response('Unauthorised', { status: 401 });
	const query = new URLSearchParams();
	for (const key of FORWARDED) {
		const value = url.searchParams.get(key);
		if (value) query.set(key, value);
	}
	// The scope, last: nothing the browser sent can have set it.
	query.set('contact', params.uid);
	const res = await fetch(`${apiUrl()}/firm/comms/messages?${query}`, {
		headers: authHeaders(locals.accessToken)
	});
	return new Response(await res.text(), {
		status: res.status,
		headers: { 'content-type': 'application/json' }
	});
};
