/**
 * The MFA step's guess limiter. A six-digit TOTP against a pending token that lives for minutes
 * was unthrottled: nothing stood between an attacker with a stolen password and a million-way
 * guess. Each attempt now consumes a token on the API's `mfa` surface, keyed on a HASH of the
 * pending token (never the token itself) and on the caller's IP, before WorkOS is asked anything.
 */
import { describe, expect, it, vi } from 'vitest';
vi.mock('$env/dynamic/private', () => ({
	env: { API_URL: 'http://api.test', INTERNAL_API_TOKEN: 'internal' }
}));
import { mfaChallengeHandle } from '@graphene/auth';
import { actions } from './+page.server';

const CHALLENGE = JSON.stringify({
	pendingAuthenticationToken: 'pending_tok',
	factorId: 'factor_1'
});

function run(allowed: boolean) {
	const calls: { url: string; body: unknown }[] = [];
	const authenticateWithTotp = vi.fn(async () => ({
		sealedSession: 'sealed',
		accessToken: `x.${Buffer.from('{}').toString('base64url')}.x`
	}));
	const deleted: string[] = [];
	const ev = {
		request: new Request('http://portal.test/mfa?next=%2Ftasks', {
			method: 'POST',
			body: new URLSearchParams({ code: '123456' })
		}),
		url: new URL('http://portal.test/mfa?next=%2Ftasks'),
		getClientAddress: () => '203.0.113.9',
		fetch: vi.fn(async (url: string, init?: RequestInit) => {
			calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
			if (url.endsWith('/internal/rate-limit')) {
				return new Response(JSON.stringify({ allowed, retry_after_seconds: allowed ? 0 : 600 }), {
					status: 200
				});
			}
			return new Response(JSON.stringify({ portal: 'client' }), { status: 200 });
		}),
		cookies: {
			get: vi.fn(() => CHALLENGE),
			set: vi.fn(),
			delete: vi.fn((name: string) => deleted.push(name))
		},
		locals: {
			auth: {
				workos: {
					multiFactorAuth: { challengeFactor: vi.fn(async () => ({ id: 'challenge_1' })) },
					userManagement: { authenticateWithTotp }
				},
				clientId: 'client_test',
				cookiePassword: 'unit-test-cookie-password-32-chars'
			}
		}
	};
	return { ev, calls, authenticateWithTotp, deleted };
}

type ActionArg = Parameters<typeof actions.default>[0];

describe('the MFA guess limiter', () => {
	it('consumes one token per attempt, keyed on a hash of the pending token — never the token', async () => {
		const { ev, calls, authenticateWithTotp } = run(true);
		try {
			await actions.default(ev as unknown as ActionArg);
		} catch {
			// The success path redirects (throws); the assertions below are about the limiter call.
		}
		const limit = calls.find((c) => c.url.endsWith('/internal/rate-limit'));
		expect(limit).toBeDefined();
		const body = limit!.body as Record<string, unknown>;
		expect(body['surface']).toBe('mfa');
		expect(body['identifier']).toBe(await mfaChallengeHandle('pending_tok'));
		expect(body['identifier']).not.toContain('pending_tok');
		expect(body['client_ip']).toBe('203.0.113.9');
		expect(authenticateWithTotp).toHaveBeenCalledTimes(1);
	});

	it('spends the challenge and sends them back to sign in once the cap is hit', async () => {
		const { ev, authenticateWithTotp, deleted } = run(false);
		let location = '';
		try {
			await actions.default(ev as unknown as ActionArg);
		} catch (e) {
			location = (e as { location: string }).location;
		}
		expect(location).toBe('/login?too_many=1&next=%2Ftasks');
		expect(authenticateWithTotp).not.toHaveBeenCalled();
		expect(deleted.length).toBe(1);
	});
});
