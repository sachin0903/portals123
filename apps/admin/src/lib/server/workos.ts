import { dev } from '$app/environment';
import { env } from '$env/dynamic/private';
import { WorkOS } from '@workos-inc/node';
import {
	resolveEnvironment,
	cookieOptions as makeCookieOptions,
	pendingCookieOptions as makePendingCookieOptions
} from '@graphene/auth';

/** The WorkOS application resolved for a request (app-per-portal, host-keyed). */
export interface AuthContext {
	workos: WorkOS;
	clientId: string;
	cookiePassword: string;
	/** The firm org the session authenticates into (the ops tenant's own, for this portal). */
	organizationId?: string;
}

// One WorkOS client per (clientId, apiKey), cached so we don't reconstruct per request.
const clients = new Map<string, WorkOS>();
function workosFor(apiKey: string, clientId: string): WorkOS {
	const key = `${clientId} ${apiKey}`;
	let client = clients.get(key);
	if (!client) {
		// The SDK REFUSES to construct with neither a key nor a client id ("WorkOS requires either an
		// API key or a clientId"), and `resolveEnvironment` returns null for an unknown or
		// not-yet-configured host by design — it "never throws". So an unconfigured host used to
		// throw HERE instead, in `handle`, which 500s every route in the portal rather than the
		// documented misconfigured state. That is the same lesson CLAUDE.md already records for
		// module-load construction, reappearing per-request: a WorkOS client must never be built
		// from config we do not have.
		//
		// The sentinel only satisfies the constructor; it is never a usable credential. The
		// misconfigured signal stays where callers already look for it — `AuthContext.clientId` is
		// read straight from the resolved config, so it is still '' and `passwordAuth` still answers
		// `misconfigured` (a 503 "temporarily unavailable"), which is what this is meant to do.
		client = new WorkOS(apiKey, { clientId: clientId || (apiKey ? clientId : 'unconfigured') });
		clients.set(key, client);
	}
	return client;
}

/**
 * Resolve this portal's WorkOS APPLICATION for a request host (app-per-portal): the host row
 * names the operations application's client id, its own secret, the cookie password and the one
 * firm org. There is no fallback: an unknown or incompletely-configured host yields an
 * unconfigured context whose sign-in reads as 503 "temporarily unavailable" (`passwordAuth` →
 * `misconfigured`) — never a silent sign-in against some other project.
 */
export async function resolveAuth(
	host: string,
	fetch: typeof globalThis.fetch,
	opts?: { bypassCache?: boolean }
): Promise<AuthContext> {
	const cfg = await resolveEnvironment(host, {
		apiUrl: env.API_URL ?? '',
		internalToken: env.INTERNAL_API_TOKEN ?? '',
		fetch,
		bypassCache: opts?.bypassCache
	});
	const clientId = cfg?.appClientId ?? '';
	return {
		workos: workosFor(cfg?.workosApiKey ?? '', clientId),
		clientId,
		cookiePassword: cfg?.cookiePassword ?? '',
		organizationId: cfg?.organizationId ?? undefined
	};
}

// `secure` depends on dev/prod, which only this SvelteKit layer knows; bind it here.
export const cookieOptions = makeCookieOptions(dev);
export const pendingCookieOptions = makePendingCookieOptions(dev);

// Re-export the shared cookie names, error classifiers and types so routes can
// keep importing everything auth-related from `$lib/server/workos`.
// Per-portal cookie names. Portals can share a host (localhost ignores the port; prod
// sub-domains can share a parent domain), and they share the environment's cookie password,
// so a shared cookie NAME leaks one portal's session into another. Namespacing keeps each
// portal's session (and MFA/pending state) its own.
export const SESSION_COOKIE = 'wos-session-admin';
export const MFA_COOKIE = 'wos-mfa-admin';
export const ENROLL_COOKIE = 'wos-enroll-admin';

export {
	emailVerificationRequired,
	mfaChallengeRequired,
	pendingTokenInvalid,
	isValidCookiePassword,
	type MfaChallenge,
	type MfaEnrollment,
	type PendingVerification
} from '@graphene/auth';
