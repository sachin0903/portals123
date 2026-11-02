/**
 * Cross-site check for state-changing requests, applied in `hooks.server.ts` so it covers every
 * `+server.ts` mutation as well as the form actions SvelteKit already guards.
 *
 * SvelteKit's own CSRF check only looks at form-encoded / multipart / text-plain bodies — the ones
 * a cross-site `<form>` can produce. The portals' JSON endpoints (`/messages/send`,
 * `/notifications/mark`, `/active-contact`, …) are outside it; a browser cannot send them
 * cross-site without a CORS preflight, and the session cookie is `SameSite=Lax`, so the exposure is
 * defence in depth rather than an open hole — but a single check the whole portal shares is cheaper
 * than reasoning about that per endpoint every time one is added.
 *
 * `Sec-Fetch-Site` is authoritative when present (every current browser sends it and a page cannot
 * forge it); `Origin` is the fallback. A request with neither (a non-browser client) is not judged:
 * it carries no ambient credentials a cross-site page could have lent it.
 */

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

/** True when `request` is a mutation that a page on ANOTHER site sent. */
export function isCrossSiteMutation(request: Request, origin: string): boolean {
	if (SAFE_METHODS.has(request.method.toUpperCase())) return false;
	const site = request.headers.get('sec-fetch-site');
	if (site) return site !== 'same-origin' && site !== 'none';
	const from = request.headers.get('origin');
	if (from) return from !== origin;
	return false;
}

/** The 403 a cross-site mutation is answered with. */
export function crossSiteRefusal(): Response {
	return new Response(JSON.stringify({ error: 'cross_site' }), {
		status: 403,
		headers: { 'content-type': 'application/json', 'cache-control': 'no-store' }
	});
}
