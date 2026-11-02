/**
 * Where the staff portal's new tab lands.
 *
 * It arrives with a single-use ticket in the query string, which is exchanged here for the
 * session token and replaced by a redirect to the portal's front page. Two consequences worth
 * stating: nothing durable is ever in the URL, and the address bar the member of staff ends up
 * looking at is the client portal's own, not a special one.
 */

import { redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { VIEW_AS_COOKIE, cookieOptions } from '$lib/server/workos';
import { redeemHandoff } from '$lib/server/view-as';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ url, cookies, fetch }) => {
	const ticket = url.searchParams.get('ticket') ?? '';
	const redeemed = await redeemHandoff(ticket, {
		internalToken: env.INTERNAL_API_TOKEN ?? '',
		fetch
	});
	if (!redeemed) redirect(303, '/view-as/ended');

	// The cookie is given the session's own remaining seconds, so it lapses when the session
	// does. The row is still what decides — the cookie only ever points at it — but a credential
	// that outlives what it points at has no reason to be sitting in a browser.
	cookies.set(VIEW_AS_COOKIE, redeemed.token, {
		...cookieOptions,
		maxAge: redeemed.session.secondsRemaining
	});
	redirect(303, '/');
};
