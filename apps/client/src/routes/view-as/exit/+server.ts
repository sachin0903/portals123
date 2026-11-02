/**
 * Leaving a view-as session. Closes it at the API so it cannot be resumed, drops the cookie, and
 * lands on the page that says the session has ended.
 *
 * `?left=1` marks this as a deliberate departure, which is what lets that page word itself for
 * someone who chose to go. A tab that arrived there any other way — a handoff that could not be
 * redeemed — is told that instead, because the reason it is showing is something the reader
 * needs to see.
 */

import { redirect } from '@sveltejs/kit';
import { VIEW_AS_COOKIE } from '$lib/server/workos';
import { endViewAs } from '$lib/server/view-as';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async ({ cookies, fetch }) => {
	const token = cookies.get(VIEW_AS_COOKIE);
	if (token) await endViewAs(token, fetch);
	cookies.delete(VIEW_AS_COOKIE, { path: '/' });
	redirect(303, '/view-as/ended?left=1');
};
