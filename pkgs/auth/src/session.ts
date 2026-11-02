/**
 * Sealed-session resolution shared by every portal's `hooks.server.ts`.
 *
 * Encapsulates the authenticate → refresh dance so all three apps behave identically, and adds
 * *proactive* refresh: WorkOS's `authenticate()` reports a token that is still valid but seconds
 * from expiry as authenticated, and that near-expiry token then gets handed to the API on the
 * same request — the intermittent "invalid or expired access token" 401 the API softens with a
 * leeway (see apps/api/app/auth.py). Refreshing when the token is within a threshold of expiry
 * means downstream API calls always receive a token with comfortable life left.
 *
 * Safe under refresh-token rotation: a *proactive* refresh that fails (e.g. another concurrent
 * request already rotated the refresh token) falls back to the still-valid current token rather
 * than tearing down a working session. Only a *reactive* refresh (token already expired) that
 * fails clears the cookie.
 */

/** The subset of a WorkOS authenticate result the portals consume. */
export interface AuthenticateResult {
	authenticated: boolean;
	user?: unknown;
	accessToken?: string;
	/** WorkOS's id for this session (the token's `sid`) — what `revokeSession` takes. */
	sessionId?: string;
	organizationId?: string | null;
	/** The WorkOS role NAME. Free text chosen inside the tenant's own project, so it carries NO
	 * privilege: it is display/telemetry only. Authority travels in `permissions`. `can()` in
	 * @graphene/api-client/permissions still takes it positionally (call-site compatibility) and
	 * deliberately ignores it — a firm that named a role "admin" used to become a UI superuser. */
	role?: string | null;
}

/** The subset of a WorkOS sealed session the portals drive. */
export interface SealedSessionLike {
	authenticate(): Promise<AuthenticateResult>;
	refresh(): Promise<{ authenticated: boolean; sealedSession?: string }>;
}

export interface ResolveSessionOutcome {
	/** The authentication to apply to `locals` (authenticated=false when there's no valid session). */
	result: AuthenticateResult;
	/** Present when the session was refreshed — persist it back to the session cookie. */
	sealedSession?: string;
	/** True when the cookie is dead (revoked/expired refresh token) and should be deleted. */
	clearCookie?: boolean;
}

/** Default: refresh a valid token once it's within this many seconds of expiry. */
const PROACTIVE_REFRESH_SECONDS = 60;

/** Read a JWT's `exp` (seconds since epoch) without verifying the signature; null if unreadable. */
function decodeExp(token: string | undefined): number | null {
	if (!token) return null;
	const payload = token.split('.')[1];
	if (!payload) return null;
	try {
		const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
		const exp = (JSON.parse(json) as { exp?: unknown }).exp;
		return typeof exp === 'number' ? exp : null;
	} catch {
		return null;
	}
}

export interface ResolveSessionOptions {
	proactiveRefreshSeconds?: number;
	/** Injectable clock (ms since epoch) for tests. */
	now?: () => number;
}

/**
 * Authenticate a sealed session, refreshing reactively (expired) or proactively (near expiry).
 *
 * `loadSession` builds a sealed session from cookie data (bound to the app's cookie password) —
 * used both for the initial authenticate and to re-authenticate a freshly refreshed session.
 */
export async function resolveSession(
	loadSession: (sessionData: string) => SealedSessionLike,
	sessionData: string,
	options: ResolveSessionOptions = {}
): Promise<ResolveSessionOutcome> {
	const threshold = options.proactiveRefreshSeconds ?? PROACTIVE_REFRESH_SECONDS;
	const now = options.now ?? (() => Date.now());

	// THE FIRST AUTHENTICATE CAN THROW, and every route of the portal used to 500 when it did.
	//
	// `authenticate()` verifies the JWT against WorkOS's JWKS, which it FETCHES — so an unreachable
	// or non-200 JWKS endpoint raises out of here (`JOSEError: Expected 200 OK from the JSON Web Key
	// Set HTTP response`) rather than answering "not authenticated". It was the one call in this
	// function without a guard: the refresh dance below has always been wrapped, so a dead cookie
	// degrades properly while a WorkOS blip took down `GET /` and everything else with it. A portal
	// that cannot verify a session should show its signed-out state, not a stack trace.
	//
	// The cookie is deliberately NOT cleared. Clearing it is a claim that the session is dead, and a
	// JWKS fetch failing says nothing about the session — it says we could not ask. Signing every
	// user out over a transient outage would be the worse half of this bug, so the outcome here is
	// exactly the one this function already uses for "no valid session": unauthenticated, cookie
	// left alone, so a working WorkOS picks the session straight back up.
	let result: AuthenticateResult;
	try {
		result = await loadSession(sessionData).authenticate();
	} catch {
		return { result: { authenticated: false } };
	}

	const exp = decodeExp(result.accessToken);
	const nearExpiry = result.authenticated && exp !== null && exp * 1000 - now() < threshold * 1000;

	if (result.authenticated && !nearExpiry) return { result };

	try {
		const refreshed = await loadSession(sessionData).refresh();
		if (refreshed.authenticated && refreshed.sealedSession) {
			const reauthed = await loadSession(refreshed.sealedSession).authenticate();
			if (reauthed.authenticated) {
				return { result: reauthed, sealedSession: refreshed.sealedSession };
			}
			// Refreshed session didn't re-authenticate: fall through to the current result below.
		} else if (!result.authenticated) {
			// Reactive refresh failed without throwing (revoked/expired refresh token) and we have
			// no valid token: the cookie is dead — stop retrying it on every request.
			return { result: { authenticated: false }, clearCookie: true };
		}
	} catch {
		if (!result.authenticated) return { result: { authenticated: false }, clearCookie: true };
		// Proactive refresh threw but the current token is still valid — keep using it.
	}

	// Proactive refresh didn't produce a new session, but the current token is still valid.
	return { result };
}
