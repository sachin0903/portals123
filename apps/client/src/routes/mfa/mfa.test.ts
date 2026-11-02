/**
 * The MFA step's exits.
 *
 * Login hands the destination on as `/mfa?next=…`, but every exit from this page used to throw it
 * away: a successful code landed on `/`, and an expired pending token redirected to a bare
 * `/login?expired=1`. So the one flow the return-to work existed for — session expires mid-task,
 * sign in again, come back — silently failed for anyone with 2FA enabled.
 */

import { describe, expect, it, vi } from 'vitest';
vi.mock('$env/dynamic/private', () => ({ env: { API_URL: 'http://api.test' } }));
import { MFA_COOKIE } from '$lib/server/workos';
import { actions, load } from './+page.server';

const ACCESS_TOKEN = `x.${Buffer.from(JSON.stringify({ sid: 'session_1' })).toString('base64url')}.x`;
const CHALLENGE = JSON.stringify({
	pendingAuthenticationToken: 'pending_tok',
	factorId: 'factor_1'
});

function stubWorkOS(authenticateWithTotp?: () => Promise<unknown>) {
	return {
		multiFactorAuth: { challengeFactor: vi.fn(async () => ({ id: 'challenge_1' })) },
		userManagement: {
			authenticateWithTotp: vi.fn(
				authenticateWithTotp ??
					(async () => ({ sealedSession: 'sealed', accessToken: ACCESS_TOKEN }))
			),
			revokeSession: vi.fn(async () => undefined)
		}
	};
}

function event(search: string, workos: unknown) {
	return {
		request: new Request(`http://portal.test/mfa${search}`, {
			method: 'POST',
			body: new URLSearchParams({ code: '123456' })
		}),
		url: new URL(`http://portal.test/mfa${search}`),
		fetch: vi.fn(async () => new Response(JSON.stringify({ portal: 'client' }), { status: 200 })),
		cookies: { get: vi.fn(() => CHALLENGE), set: vi.fn(), delete: vi.fn() },
		locals: {
			auth: {
				workos,
				clientId: 'client_test',
				cookiePassword: 'unit-test-cookie-password-32-chars'
			}
		}
	};
}

type ActionArg = Parameters<typeof actions.default>[0];
type LoadArg = Parameters<typeof load>[0];

async function locationOf(ev: ReturnType<typeof event>): Promise<string> {
	try {
		await actions.default(ev as unknown as ActionArg);
	} catch (e) {
		return (e as { location: string }).location;
	}
	throw new Error('expected a redirect');
}

describe('MFA exits keep the user’s place', () => {
	it('sends a verified user to where they were heading', async () => {
		expect(await locationOf(event('?next=%2Ftasks', stubWorkOS()))).toBe('/tasks');
	});

	it('falls back to the home page when there is no destination', async () => {
		expect(await locationOf(event('', stubWorkOS()))).toBe('/');
	});

	it('keeps the destination when the pending token has expired', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const workos = stubWorkOS(async () => {
			throw { rawData: { code: 'invalid_pending_authentication_token' } };
		});
		expect(await locationOf(event('?next=%2Ftasks', workos))).toBe(
			'/login?expired=1&next=%2Ftasks'
		);
	});

	it('never forwards an off-origin destination (open redirect)', async () => {
		expect(await locationOf(event('?next=https%3A%2F%2Fevil.test', stubWorkOS()))).toBe('/');
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const workos = stubWorkOS(async () => {
			throw { rawData: { code: 'invalid_pending_authentication_token' } };
		});
		expect(await locationOf(event('?next=%2F%2Fevil.test', workos))).toBe('/login?expired=1');
	});

	it('carries the destination on when the challenge cookie has gone', async () => {
		const ev = {
			locals: { user: null },
			cookies: { get: vi.fn(() => undefined) },
			url: new URL('http://portal.test/mfa?next=%2Ftasks')
		};
		try {
			await load(ev as unknown as LoadArg);
			throw new Error('expected a redirect');
		} catch (e) {
			expect((e as { location: string }).location).toBe('/login?next=%2Ftasks');
		}
		expect(MFA_COOKIE).toBeTruthy(); // the cookie name the guard reads
	});
});
