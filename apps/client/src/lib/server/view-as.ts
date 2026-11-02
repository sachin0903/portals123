/**
 * This portal, seen by a member of staff acting as one of their clients.
 *
 * The new tab arrives at `/view-as` carrying a single-use ticket. This module exchanges that
 * ticket — server to server, so the session token never reaches the browser — puts the token in
 * an httpOnly cookie, and from then on the portal runs on it exactly as it would on a client's
 * own access token. Every page loader already sends `locals.accessToken` as a bearer, so nothing
 * downstream has to know which kind of session it is serving.
 *
 * What the API decides, and this module therefore does not: whether the session is live, whose
 * portal it is, and what it can reach. All three are re-answered on every request against
 * `auth.view_as_sessions` and the claim minter (`auth.resolve_view_as_claims`). A cookie here is
 * a claim to a session, never a grant of one.
 */

import { apiUrl } from '$lib/api';

/** Who is looking, whose portal this is, and how long is left — the banner's content. */
export type ViewAsSession = {
	uid: string;
	adviserName: string;
	contactId: number;
	contactName: string;
	reason: string;
	expiresAt: string;
	secondsRemaining: number;
};

type SessionPayload = {
	uid: string;
	adviser_name: string;
	contact_id: number;
	contact_name: string;
	reason: string;
	expires_at: string;
	seconds_remaining: number;
};

const shape = (body: SessionPayload): ViewAsSession => ({
	uid: body.uid,
	adviserName: body.adviser_name,
	contactId: body.contact_id,
	contactName: body.contact_name,
	reason: body.reason,
	expiresAt: body.expires_at,
	secondsRemaining: body.seconds_remaining
});

/**
 * Exchange the handoff ticket the new tab arrived with for the session token.
 *
 * Single use: a second attempt with the same ticket gets nothing, so a link forwarded to someone
 * else after the tab has opened is inert. Returns null whenever the exchange fails — an expired
 * ticket, a session already closed, a misconfigured internal token — and the route then shows
 * the "session ended" page rather than guessing.
 */
export async function redeemHandoff(
	ticket: string,
	opts: { internalToken: string; fetch: typeof fetch }
): Promise<{ token: string; session: ViewAsSession } | null> {
	if (!ticket || !opts.internalToken) return null;
	try {
		const res = await opts.fetch(`${apiUrl()}/view-as/redeem`, {
			method: 'POST',
			headers: { 'content-type': 'application/json', 'x-internal-token': opts.internalToken },
			body: JSON.stringify({ handoff_ticket: ticket })
		});
		if (!res.ok) return null;
		const body = (await res.json()) as { token: string; detail: SessionPayload };
		return { token: body.token, session: shape(body.detail) };
	} catch {
		return null;
	}
}

/**
 * The session a token still names, or null if it names none any more.
 *
 * Called on every request in `hooks.server.ts`, which is deliberate: a session ended by the
 * person who opened it, or by its own expiry, has to stop working on the next page load rather
 * than whenever the cookie happens to lapse.
 */
export async function resolveViewAs(
	token: string,
	fetchFn: typeof fetch
): Promise<ViewAsSession | null> {
	try {
		const res = await fetchFn(`${apiUrl()}/view-as/session`, {
			headers: { Authorization: `Bearer ${token}` }
		});
		if (!res.ok) return null;
		return shape((await res.json()) as SessionPayload);
	} catch {
		return null;
	}
}

/** Close the session. Best-effort: the cookie is cleared either way, and an unclosed session
 *  still lapses on its own clock. */
export async function endViewAs(token: string, fetchFn: typeof fetch): Promise<void> {
	try {
		await fetchFn(`${apiUrl()}/view-as/end`, {
			method: 'POST',
			headers: { Authorization: `Bearer ${token}` }
		});
	} catch {
		/* the clock closes it regardless */
	}
}
