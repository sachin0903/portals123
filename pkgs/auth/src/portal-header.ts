/**
 * Each portal tells the API which surface it is.
 *
 * One local WorkOS application is stored on more than one portal, and a staff login holds
 * both platform and operations. The token alone cannot tell the admin portal from the
 * platform portal. This header is a tie-break: the API uses it only when that portal's
 * application client id equals the token and the login has an active membership there.
 */

import { currentTraceParent } from './trace';

export const PORTAL_HEADER = 'x-graphene-portal';

export type PortalName = 'client' | 'platform' | 'operations';

const MARK = Symbol.for('graphene.portalHeader');

type MarkedFetch = typeof fetch & { [MARK]?: true };

function requestUrl(input: RequestInfo | URL): string {
	if (typeof input === 'string') return input;
	if (input instanceof URL) return input.href;
	return input.url;
}

/**
 * Add the portal header to every fetch this process makes to the API.
 *
 * Server loads call `fetch(apiUrl())` directly as well as through `event.fetch`, and
 * SvelteKit's own fetch ends in the global one. Installing here covers both. Requests to
 * anywhere else are left alone, and a header the caller already set is kept.
 */
export function installPortalHeader(portal: PortalName, apiBase: () => string): void {
	const current = globalThis.fetch as MarkedFetch;
	if (current[MARK]) return;
	const baseFetch = current.bind(globalThis);
	const wrapped: MarkedFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
		const base = apiBase().replace(/\/$/, '');
		const url = requestUrl(input);
		if (!base || !url.startsWith(base)) return baseFetch(input, init);
		const headers = new Headers(
			init?.headers ??
				(typeof Request !== 'undefined' && input instanceof Request ? input.headers : undefined)
		);
		if (!headers.has(PORTAL_HEADER)) headers.set(PORTAL_HEADER, portal);
		// The page render's trace. A caller that already set one (the rum forward) keeps it.
		if (!headers.has('traceparent')) {
			const traceparent = currentTraceParent();
			if (traceparent) headers.set('traceparent', traceparent);
		}
		return baseFetch(input, { ...init, headers });
	}) as MarkedFetch;
	wrapped[MARK] = true;
	globalThis.fetch = wrapped;
}
