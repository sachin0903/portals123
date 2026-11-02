// The platform pages' live channel. EventSource cannot send an Authorization header, so
// the browser hits this same-origin route and the session cookie is what authenticates it.
// The body is the API's copy of the daemon's /events/stream, unread — buffering it would
// turn the feed into a poll.

import { error } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');
	const provider = params.slug;
	if (!provider) error(404, 'Not a live platform');
	const after = url.searchParams.get('after') ?? '0';
	const upstream = await fetch(
		`${apiUrl()}/admin/platforms/${provider}/events/stream?after=${encodeURIComponent(after)}`,
		{
			headers: { ...authHeaders(locals.accessToken), Accept: 'text/event-stream' }
		}
	);
	if (!upstream.ok || !upstream.body) error(upstream.status, 'Pipeline events are unavailable');
	return new Response(upstream.body, {
		headers: {
			'Content-Type': 'text/event-stream',
			'Cache-Control': 'no-cache, no-transform',
			'X-Accel-Buffering': 'no'
		}
	});
};
