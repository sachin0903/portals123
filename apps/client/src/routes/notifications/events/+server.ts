import { json, type RequestHandler } from '@sveltejs/kit';
import { privateSseResponse } from '@graphene/auth';
import { apiUrl } from '$lib/api';
import { activeContactParam } from '$lib/active-contact';

/**
 * The bell's live channel, same-origin: this server exchanges its held bearer for a single-use
 * ticket and pipes the private API's SSE stream back, so the browser needs no API host and no token.
 *
 * `?contact_id=` rides on the TICKET path, not the stream path — the ticket is what decides which
 * key this reader listens on, and `privateSseResponse` only joins base + path, so a query string
 * survives untouched. It matters for a login linked to more than one client: without it the API
 * keys the socket on the FIRST contact in the session's scope, which is not necessarily the active
 * one, so a message to the client actually on screen pushed nothing (it arrived on the 30s badge
 * poll) while one to a different linked client woke the bell.
 */
export const GET: RequestHandler = ({ locals, fetch, request, url }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const cid = activeContactParam(url);
	return privateSseResponse({
		fetch,
		apiBaseUrl: apiUrl(),
		accessToken: locals.accessToken,
		ticketPath: `/notifications/ticket${cid ? `?${cid}` : ''}`,
		eventsPath: '/notifications/events',
		signal: request.signal
	});
};
