/**
 * Host → WorkOS environment resolution for the shared, host-keyed portals
 * (project-per-tenant; see docs/identity-and-access.md §0).
 *
 * A single portal deployment serves every tenant, so on each request we resolve the
 * tenant's WorkOS environment from the request host by asking the API's internal endpoint
 * (`GET /internal/env-config?host=…`) — the API is the sole holder of the master key and
 * returns the decrypted config. Results are cached per host (with a short TTL, and a
 * negative cache for unknown hosts) so static-asset requests are not a round-trip.
 * Branding-editor routes pass `bypassCache` so a colour/logo save is visible on the next
 * paint of that page rather than for up to the TTL; every other navigation uses the cache.
 * Transient failures are NOT cached, so a blip doesn't lock a tenant out for the TTL.
 *
 * Framework-agnostic (no SvelteKit/env deps): the per-app glue passes `apiUrl`,
 * `internalToken` and a `fetch`.
 */

import type { Branding } from './branding';
import { deviceLabel } from './client-context';

export interface EnvironmentConfig {
	tenantId: number;
	stage: string;
	workosClientId: string;
	/** The WorkOS APPLICATION client id for this host's portal (portal-via-application). The portal
	 * signs in through this when set; null falls back to `workosClientId` (behaviour-preserving
	 * until the app is configured). See docs/auth-applications-migration.md. */
	appClientId: string | null;
	issuer: string;
	/** Secrets — present only when set for the environment. */
	workosApiKey: string | null;
	cookiePassword: string | null;
	/** The WorkOS org this host's portal authenticates into (so a user in several orgs is
	 * signed into the right one). Null until the environment's portal orgs are created. */
	organizationId: string | null;
	/** White-label branding (non-secret) — tokens + logo/name/copy, applied per request. */
	branding: Branding | null;
	/** The firm's display name (used as the base page title). */
	appName: string | null;
}

export interface ResolveEnvironmentOptions {
	/** Base URL of the API (e.g. http://127.0.0.1:8000). */
	apiUrl: string;
	/** Shared secret for the API's internal endpoints (`INTERNAL_API_TOKEN`). */
	internalToken: string;
	/** Injected fetch (defaults to global). */
	fetch?: typeof fetch;
	/** Positive/negative cache TTL in ms (default 60s). */
	cacheTtlMs?: number;
	/** Skip a still-fresh cache entry, then write the new result back. Branding-editor
	 * routes use this so a colour save is visible on the next paint of that page instead of
	 * serving the previous theme for the TTL. Other navigations keep the cache. */
	bypassCache?: boolean;
}

interface CacheEntry {
	value: EnvironmentConfig | null;
	expires: number;
}

const cache = new Map<string, CacheEntry>();

/** Clear the resolution cache (tests, or after an environment is (re)registered). */
export function clearEnvironmentCache(): void {
	cache.clear();
}

interface RawEnvConfig {
	tenant_id: number;
	stage: string;
	workos_client_id: string;
	app_client_id?: string | null;
	issuer: string;
	workos_api_key: string | null;
	cookie_password: string | null;
	organization_id: string | null;
	branding?: Branding | null;
	app_name?: string | null;
}

function normalise(raw: RawEnvConfig): EnvironmentConfig {
	return {
		tenantId: raw.tenant_id,
		stage: raw.stage,
		workosClientId: raw.workos_client_id,
		appClientId: raw.app_client_id ?? null,
		issuer: raw.issuer,
		workosApiKey: raw.workos_api_key ?? null,
		cookiePassword: raw.cookie_password ?? null,
		organizationId: raw.organization_id ?? null,
		branding: raw.branding ?? null,
		appName: raw.app_name ?? null
	};
}

/**
 * Resolve the WorkOS environment for a request host, or null when the host is unknown or
 * the resolver isn't configured. Never throws — a transient API error resolves to null
 * (and is not cached), so the caller can fall back (e.g. to a dev single-environment).
 */
export async function resolveEnvironment(
	host: string,
	opts: ResolveEnvironmentOptions
): Promise<EnvironmentConfig | null> {
	if (!host || !opts.apiUrl || !opts.internalToken) return null;

	const now = Date.now();
	const hit = cache.get(host);
	if (!opts.bypassCache && hit && hit.expires > now) return hit.value;

	const doFetch = opts.fetch ?? fetch;
	const ttl = opts.cacheTtlMs ?? 60_000;
	try {
		const res = await doFetch(
			`${opts.apiUrl}/internal/env-config?host=${encodeURIComponent(host)}`,
			{ headers: { 'x-internal-token': opts.internalToken }, cache: 'no-store' }
		);
		if (res.ok) {
			const value = normalise((await res.json()) as RawEnvConfig);
			cache.set(host, { value, expires: now + ttl });
			return value;
		}
		if (res.status === 404) {
			// Unknown host: negative-cache so we don't hammer the API for bad hosts.
			cache.set(host, { value: null, expires: now + ttl });
			return null;
		}
		// Any other status is treated as transient: do not cache.
		return null;
	} catch {
		// Network/transient failure: do not cache, let the caller fall back.
		return null;
	}
}

/**
 * Ask the API to email a password-set/reset link (via Resend) for this email on this portal
 * host. Best-effort and always silent to the caller — the portal shows the same "check your
 * email" message regardless, so it never reveals whether the account exists.
 */
export async function requestPasswordReset(
	email: string,
	host: string,
	opts: { apiUrl: string; internalToken: string; clientIp?: string | null; fetch?: typeof fetch }
): Promise<void> {
	if (!email || !opts.apiUrl || !opts.internalToken) return;
	const doFetch = opts.fetch ?? fetch;
	try {
		// `client_ip` is the END USER's address (SvelteKit `getClientAddress()`), which the API cannot
		// see — it only ever sees the portal container. Its per-IP cap on this surface (one source
		// flooding many inboxes) is keyed on it, so a portal that omits it leaves only the per-email cap.
		await doFetch(`${opts.apiUrl}/internal/forgot-password`, {
			method: 'POST',
			headers: { 'content-type': 'application/json', 'x-internal-token': opts.internalToken },
			body: JSON.stringify({ email, host, client_ip: opts.clientIp ?? null })
		});
	} catch {
		// best-effort
	}
}

/**
 * Report an authentication event the API's request floor cannot see — login / logout / MFA happen
 * in the portal's own server via WorkOS, never as an API mutation — to `POST /internal/auth-event`,
 * which records it on the `audit.actions` stream (see app/audit_actions.py::record_auth_action).
 *
 * Best-effort and silent: it rides an auth flow that has already happened, so it must never break
 * the login/logout page. A failed login carries the attempted `email` and no `workosUserId` (the
 * credentials were wrong, so there is no verified subject); every other kind carries the id.
 */
export async function reportAuthEvent(
	kind: 'login' | 'logout' | 'login_failed' | 'mfa_enrolled' | 'mfa_removed',
	opts: {
		apiUrl: string;
		internalToken: string;
		host: string;
		workosUserId?: string;
		email?: string;
		/** WorkOS-aligned authentication method (password / sso / oauth / mfa / magic), recorded on
		 *  login + login_failed so the trail reads "Signed in with a password". */
		method?: 'password' | 'sso' | 'oauth' | 'mfa' | 'magic';
		/** Browser context for a successful sign-in. Stored on the auth event; WorkOS is told the
		 *  same facts on the call that minted the session. */
		client?: { ipAddress?: string; userAgent?: string };
		fetch?: typeof fetch;
	}
): Promise<void> {
	if (!opts.apiUrl || !opts.internalToken) return;
	const doFetch = opts.fetch ?? fetch;
	// Only a successful sign-in carries the browser. A failed attempt has no session, and logout
	// is not an authentication. The device label is derived here so the audit row and the session
	// list use one reading of the user agent.
	const client = kind === 'login' ? opts.client : undefined;
	const device = deviceLabel(client?.userAgent);
	try {
		await doFetch(`${opts.apiUrl}/internal/auth-event`, {
			method: 'POST',
			headers: { 'content-type': 'application/json', 'x-internal-token': opts.internalToken },
			body: JSON.stringify({
				kind,
				workos_user_id: opts.workosUserId,
				email: opts.email,
				method: opts.method,
				host: opts.host,
				...(client?.ipAddress ? { client_ip: client.ipAddress } : {}),
				...(client?.userAgent ? { user_agent: client.userAgent } : {}),
				...(device ? { device } : {})
			})
		});
	} catch {
		// best-effort — the auth action already happened
	}
}

/**
 * SvelteKit's `getClientAddress()` THROWS when the adapter cannot determine the peer (some adapters
 * outside a request context, or a misconfigured proxy setup). A rate-limit key is not worth a 500
 * on the forgot-password form, so the unauthenticated surfaces read it through this.
 */
export function safeClientAddress(getClientAddress: () => string): string | null {
	try {
		const ip = getClientAddress();
		return ip ? ip : null;
	} catch {
		return null;
	}
}

/**
 * The opaque per-challenge handle the `mfa` rate-limit surface is keyed on: a SHA-256 of the
 * pending authentication token, so the limiter (and its logs and metrics) never hold the token —
 * it is a credential that completes a sign-in. Deterministic, so every guess at one challenge
 * lands on the same key.
 */
export async function mfaChallengeHandle(pendingAuthenticationToken: string): Promise<string> {
	const digest = await crypto.subtle.digest(
		'SHA-256',
		new TextEncoder().encode(pendingAuthenticationToken)
	);
	return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0'))
		.join('')
		.slice(0, 32);
}

/** What `consumeRateLimit` decided. `allowed: false` means show the caller a retry message. */
export interface RateLimitDecision {
	allowed: boolean;
	retryAfterSeconds: number;
}

/**
 * Consume one attempt on an unauthenticated portal surface, and say whether it is allowed.
 *
 * The portals do the WorkOS call themselves for login, so the API never sees the attempt and cannot
 * throttle it — this is how a portal hands the count over. `POST /internal/rate-limit` returns 200
 * either way (never 429) so the caller renders its own copy.
 *
 * **Calling this CONSUMES a token**, so call it once per real attempt, and only for an attempt you
 * are about to make.
 *
 * FAILS OPEN, deliberately: if the API or the internal token is unreachable, a login form that
 * refused everybody would be a self-inflicted outage far worse than an unthrottled window. The
 * failure is the caller's to log.
 *
 * This existed as an endpoint with no caller — `login_limits` was written, tested and wired to
 * nothing, so login was completely unthrottled while reading as fixed. Do not remove the call sites
 * without removing the endpoint.
 */
export async function consumeRateLimit(
	surface: 'login' | 'forgot-password' | 'mfa',
	identifier: string,
	host: string,
	opts: { apiUrl: string; internalToken: string; clientIp?: string | null; fetch?: typeof fetch }
): Promise<RateLimitDecision> {
	const open: RateLimitDecision = { allowed: true, retryAfterSeconds: 0 };
	if (!identifier || !opts.apiUrl || !opts.internalToken) return open;
	const doFetch = opts.fetch ?? fetch;
	try {
		const res = await doFetch(`${opts.apiUrl}/internal/rate-limit`, {
			method: 'POST',
			headers: { 'content-type': 'application/json', 'x-internal-token': opts.internalToken },
			body: JSON.stringify({
				surface,
				identifier,
				host,
				client_ip: opts.clientIp ?? null
			})
		});
		if (!res.ok) return open;
		const body = (await res.json()) as { allowed?: boolean; retry_after_seconds?: number };
		return {
			allowed: body.allowed !== false,
			retryAfterSeconds: Number(body.retry_after_seconds ?? 0)
		};
	} catch {
		return open;
	}
}

/**
 * Tell the API that this person has just finished onboarding by setting their password.
 *
 * Setting a password verifies the email at WorkOS but wrote nothing to our own mirror, so the only
 * reconciler was the WorkOS events poller — which leaves someone who HAS onboarded showing as
 * never-invited until an operator presses "Sync from WorkOS". This is the push; the poller is the
 * safety net. The API verifies the claim against WorkOS before promoting anything, so it is safe to
 * call from an unauthenticated page.
 *
 * Prefers `workosUserId` (exact); `email` needs `host` to disambiguate, because an address is
 * unique per tenant, not globally. Returns whether the push was actually sent — the earlier
 * portal-local version silently did nothing whenever the identity was missing, which is precisely
 * how it stayed broken. Never throws: the password is already set by this point.
 */
export async function notifyOnboardingComplete(
	identity: { workosUserId?: string | null; email?: string | null },
	host: string,
	opts: { apiUrl: string; internalToken: string; fetch?: typeof fetch }
): Promise<boolean> {
	const workosUserId = identity.workosUserId ?? '';
	const email = identity.email ?? '';
	if (!workosUserId && !email) return false;
	if (!opts.apiUrl || !opts.internalToken) return false;
	const doFetch = opts.fetch ?? fetch;
	try {
		const res = await doFetch(`${opts.apiUrl}/internal/onboarding-complete`, {
			method: 'POST',
			headers: { 'content-type': 'application/json', 'x-internal-token': opts.internalToken },
			body: JSON.stringify({
				...(workosUserId ? { workos_user_id: workosUserId } : {}),
				...(email ? { email } : {}),
				...(host ? { host } : {})
			})
		});
		return res.ok;
	} catch {
		return false; // best-effort: the poller/manual sync reconciles
	}
}
