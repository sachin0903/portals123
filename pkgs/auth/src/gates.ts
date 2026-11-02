/**
 * The session gate a portal applies AFTER a session has authenticated: "this session belongs
 * to a different portal".
 *
 * It used to live in the root `+layout.server.ts`, which is the wrong place: a layout `load`
 * runs for PAGES, and never for a `+server.ts` endpoint — so every browser-callable proxy
 * (`/messages/send`, `/notifications/mark`, `/search`, `/documents/…/content`, …) served a
 * session that the pages beside it had already refused. The decision is made in `hooks.server.ts`
 * now, once per request; the hook answers non-navigation requests itself (403 JSON) and hands the
 * decision to the layout, which turns it into the redirect a page needs.
 *
 * A login terms gate used to sit beside this one (`terms_required`, `GET /terms/outstanding` on
 * every navigation). It was removed on 13 Oct 2026: terms are collected inside a journey
 * (`required_at = onboarding`), and that check was the one API call still awaited before the
 * first byte of every page.
 */

/** What the request may do. `undefined` portal is not an answer (fail open on portal). */
export type GateDecision = 'allow' | 'wrong_portal';

/** A confirmed portal from GET /me that is not this app's. `undefined` is not an answer. */
export function refusePortal(portal: string | null | undefined, expected: string): boolean {
	return portal !== undefined && portal !== expected;
}

export interface GateInput {
	/** A signed-in session with a token; anything else is not gated here. */
	accessToken: string | null;
	/** The portal GET /me confirmed for the session (`undefined` = no answer). */
	portal: string | null | undefined;
	/** Which portal this app is. */
	thisPortal: string;
}

/** A foreign session is refused. A same-portal session is allowed — terms are not decided here. */
export function decideGate(input: GateInput): GateDecision {
	if (!input.accessToken) return 'allow';
	if (refusePortal(input.portal, input.thisPortal)) return 'wrong_portal';
	return 'allow';
}

/**
 * Whether a request is a NAVIGATION — a document load, or SvelteKit's own `__data.json` fetch for
 * a client-side navigation — as opposed to a script's call to a `+server.ts` endpoint. A
 * navigation is answered by the layout's `redirect()` (which SvelteKit turns into the right thing
 * for both); anything else gets a plain 403 from the hook, because a redirect to an HTML page is
 * not an answer a `fetch()` caller can act on.
 */
export function isNavigationRequest(request: Request, isDataRequest: boolean): boolean {
	if (isDataRequest) return true;
	const dest = request.headers.get('sec-fetch-dest');
	if (dest) return dest === 'document' || dest === 'iframe' || dest === 'frame';
	return (request.headers.get('accept') ?? '').includes('text/html');
}

/** The 403 the hook answers a refused non-navigation request with. */
export function gateRefusal(decision: Exclude<GateDecision, 'allow'>): Response {
	return new Response(JSON.stringify({ error: decision }), {
		status: 403,
		headers: { 'content-type': 'application/json', 'cache-control': 'no-store' }
	});
}
