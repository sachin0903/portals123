/**
 * Return-to ("next") handling for the sign-in flow, shared by every portal.
 *
 * A session that expires mid-task must not lose the user's place: a page guard that finds
 * no session redirects to `/login?next=<where they were>` and the login action sends them
 * back on success. The value is attacker-controllable (it is a query parameter on an
 * unauthenticated page), so it is validated down to a *same-origin, path-only* destination
 * before it is used in a redirect — an open redirect on a login page is a
 * credential-phishing primitive, not a cosmetic bug.
 */

/** Query parameter carrying the post-login destination. */
export const RETURN_TO_PARAM = 'next';

/** Longest `next` we will carry. Anything longer is a probe, not a real path. */
const MAX_LENGTH = 512;

/** Control characters (NUL/CR/LF/DEL) are redirect- and header-splitting material.
 * Written as a scan rather than a regex — a control-character class trips `no-control-regex`. */
function hasControlChars(value: string): boolean {
	for (const ch of value) {
		const code = ch.codePointAt(0) ?? 0;
		if (code < 0x20 || code === 0x7f) return true;
	}
	return false;
}

/** The auth flow's own pages. Bouncing back to one of these either loops or strands the
 * user on a step they have already completed, so they are never valid destinations. */
const AUTH_PREFIXES = ['/login', '/logout', '/verify', '/mfa', '/set-password', '/forgot-password'];

/** Dummy origin used to parse a path-only value; anything that escapes it is rejected. */
const BASE = 'http://return-to.invalid';

/**
 * Validate a `next` value. Returns the path (with query + hash) to redirect to, or null when
 * the value is missing, off-origin or otherwise not a destination we are willing to send a
 * freshly authenticated browser to. Callers fall back to their own default (usually `/`).
 */
export function safeReturnTo(raw: string | null | undefined): string | null {
	if (typeof raw !== 'string') return null;
	const value = raw.trim();
	if (!value || value.length > MAX_LENGTH) return null;
	if (hasControlChars(value)) return null;
	// Path-only, single leading slash. Rejects absolute URLs (`https://evil.test`),
	// scheme-relative URLs (`//evil.test`) and the backslash forms some clients and proxies
	// normalise into them (`/\evil.test`).
	if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return null;

	let parsed: URL;
	try {
		// Parsing against a dummy origin is the final same-origin assertion, and normalises
		// traversal (`/a/../../b`) before we hand the value to a redirect.
		parsed = new URL(value, BASE);
	} catch {
		return null;
	}
	if (parsed.origin !== BASE) return null;

	const path = parsed.pathname;
	if (AUTH_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`))) return null;
	return `${path}${parsed.search}${parsed.hash}`;
}

/**
 * Where to send the user after a successful sign-in: their validated `next`, else `fallback`.
 * Read from the login page's own URL — the login form POSTs to the current URL, query string
 * included, so the value survives the submit without a hidden field.
 */
export function returnToFrom(url: URL, fallback = '/'): string {
	return safeReturnTo(url.searchParams.get(RETURN_TO_PARAM)) ?? fallback;
}

/**
 * `base` with the return-to attached, or bare `base` when there is nothing worth preserving
 * (the home page, or an auth page). Used to hand the destination from one step of the sign-in
 * flow to the next (`/login` → `/mfa` → …).
 */
export function withReturnTo(base: string, from: { pathname: string; search?: string }): string {
	const target = safeReturnTo(`${from.pathname}${from.search ?? ''}`);
	if (!target || target === '/') return base;
	return `${base}?${RETURN_TO_PARAM}=${encodeURIComponent(target)}`;
}

/**
 * The sign-in URL a page guard should redirect to when there is no session, preserving where
 * the user was: `redirect(303, loginPath(url))`.
 */
export function loginPath(url: { pathname: string; search?: string }): string {
	return withReturnTo('/login', url);
}

/**
 * A `/login` URL that carries BOTH a notice flag (`?check=1`, `?expired=1` — the login page turns
 * these into copy explaining why the user is back here) and the destination they were heading for.
 *
 * The bare `'/login?check=1'` / `'/login?expired=1'` redirects dropped `next`, so two of the five
 * sign-in outcomes silently lost the user's place: they finish the detour (confirm the email, retry
 * the timed-out code) and land on the dashboard instead of the page they asked for.
 */
export function loginNotice(flag: string, next?: string | null): string {
	const params = new URLSearchParams({ [flag]: '1' });
	const target = safeReturnTo(next ?? null);
	if (target && target !== '/') params.set(RETURN_TO_PARAM, target);
	return `/login?${params.toString()}`;
}
