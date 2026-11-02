import { describe, expect, it, vi } from 'vitest';
import { resolveSession, type SealedSessionLike } from './session';

const NOW = 1_700_000_000_000; // fixed clock (ms)
const now = () => NOW;

/** Build a fake JWT whose payload carries the given `exp` (seconds since epoch). */
function tokenExpiringAt(expSeconds: number): string {
	const payload = btoa(JSON.stringify({ exp: expSeconds })).replace(/=+$/, '');
	return `header.${payload}.sig`;
}

/** A sealed session whose behaviour is scripted per call, so we can assert the refresh dance. */
function fakeSession(script: {
	authenticate: () => ReturnType<SealedSessionLike['authenticate']>;
	refresh?: () => ReturnType<SealedSessionLike['refresh']>;
}): SealedSessionLike {
	return {
		authenticate: script.authenticate,
		refresh: script.refresh ?? (async () => ({ authenticated: false }))
	};
}

describe('resolveSession', () => {
	it('returns the current result without refreshing when the token has comfortable life left', async () => {
		const refresh = vi.fn();
		const load = () =>
			fakeSession({
				authenticate: async () => ({
					authenticated: true,
					accessToken: tokenExpiringAt(NOW / 1000 + 3600),
					user: { id: 'u1' }
				}),
				refresh
			});
		const out = await resolveSession(load, 'sealed', { now });
		expect(out.result.authenticated).toBe(true);
		expect(out.sealedSession).toBeUndefined();
		expect(refresh).not.toHaveBeenCalled();
	});

	it('proactively refreshes a valid-but-near-expiry token and returns the new sealed session', async () => {
		const load = (data: string) =>
			fakeSession({
				authenticate: async () =>
					data === 'fresh'
						? {
								authenticated: true,
								accessToken: tokenExpiringAt(NOW / 1000 + 3600),
								user: { id: 'u1' }
							}
						: {
								authenticated: true,
								accessToken: tokenExpiringAt(NOW / 1000 + 10),
								user: { id: 'u1' }
							},
				refresh: async () => ({ authenticated: true, sealedSession: 'fresh' })
			});
		const out = await resolveSession(load, 'stale', { now });
		expect(out.result.authenticated).toBe(true);
		expect(out.sealedSession).toBe('fresh');
	});

	it('keeps the still-valid token when a proactive refresh fails (rotation race)', async () => {
		const load = () =>
			fakeSession({
				authenticate: async () => ({
					authenticated: true,
					accessToken: tokenExpiringAt(NOW / 1000 + 10),
					user: { id: 'u1' }
				}),
				refresh: async () => {
					throw new Error('refresh token already rotated');
				}
			});
		const out = await resolveSession(load, 'stale', { now });
		expect(out.result.authenticated).toBe(true);
		expect(out.sealedSession).toBeUndefined();
		expect(out.clearCookie).toBeUndefined();
	});

	it('reactively refreshes an expired token', async () => {
		const load = (data: string) =>
			fakeSession({
				authenticate: async () =>
					data === 'fresh'
						? {
								authenticated: true,
								accessToken: tokenExpiringAt(NOW / 1000 + 3600),
								user: { id: 'u1' }
							}
						: { authenticated: false },
				refresh: async () => ({ authenticated: true, sealedSession: 'fresh' })
			});
		const out = await resolveSession(load, 'expired', { now });
		expect(out.result.authenticated).toBe(true);
		expect(out.sealedSession).toBe('fresh');
	});

	it('clears the cookie when an expired session cannot be refreshed', async () => {
		const load = () =>
			fakeSession({
				authenticate: async () => ({ authenticated: false }),
				refresh: async () => ({ authenticated: false })
			});
		const out = await resolveSession(load, 'dead', { now });
		expect(out.result.authenticated).toBe(false);
		expect(out.clearCookie).toBe(true);
	});

	it('clears the cookie when refresh throws for an expired session', async () => {
		const load = () =>
			fakeSession({
				authenticate: async () => ({ authenticated: false }),
				refresh: async () => {
					throw new Error('revoked');
				}
			});
		const out = await resolveSession(load, 'dead', { now });
		expect(out.clearCookie).toBe(true);
	});

	it('degrades to signed-out when the JWKS fetch fails, instead of 500ing every route', async () => {
		// `authenticate()` VERIFIES the JWT, which means it FETCHES WorkOS's JWKS — so an unreachable
		// or non-200 endpoint raises `JOSEError: Expected 200 OK from the JSON Web Key Set HTTP
		// response` out of it. This was the one call in `resolveSession` with no guard, so a WorkOS
		// blip turned into `[500] GET /` on every route of the portal, over and over.
		const refresh = vi.fn();
		const load = () =>
			fakeSession({
				authenticate: async () => {
					throw new Error('Expected 200 OK from the JSON Web Key Set HTTP response');
				},
				refresh
			});
		const out = await resolveSession(load, 'stale', { now });
		expect(out.result.authenticated).toBe(false);
		// And it does not go on to try a refresh: the same fetch would fail again.
		expect(refresh).not.toHaveBeenCalled();
	});

	it('does NOT clear the cookie when verification was merely unreachable', async () => {
		// The half that matters more than the 500. Clearing the cookie asserts the session is dead,
		// and a failed JWKS fetch says only that we could not ask — so clearing would sign every
		// signed-in user out over a transient outage, and they would stay out because the cookie is
		// gone once WorkOS returns. Contrast the two cases above, which clear it on real evidence:
		// a refresh that came back unauthenticated, or threw, for an already-expired token.
		const load = () =>
			fakeSession({
				authenticate: async () => {
					throw new Error('fetch failed');
				}
			});
		const out = await resolveSession(load, 'stale', { now });
		expect(out.clearCookie).toBeUndefined();
		expect(out.sealedSession).toBeUndefined();
	});
});
