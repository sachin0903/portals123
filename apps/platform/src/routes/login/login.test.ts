/**
 * The login action's post-sign-in destination.
 *
 * A session that expires mid-task redirects to `/login?next=…`; login must send the user back
 * there — and must never treat that parameter as a destination it hasn't validated, since an
 * open redirect on a sign-in page is a phishing primitive.
 */

import { describe, expect, it, vi } from 'vitest';

// Pinned rather than inherited: the `?check=1` branch only calls the API when API_URL and
// INTERNAL_API_TOKEN are set, so without this the assertion that a link is actually sent would pass
// on a developer's machine (which has a .env.local) and quietly vacate on CI.
vi.mock('$env/dynamic/private', () => ({
	env: { API_URL: 'http://api.test', INTERNAL_API_TOKEN: 'internal-token' }
}));

import { actions, load } from './+page.server';

const ACCESS_TOKEN = `x.${Buffer.from(JSON.stringify({ sid: 'session_1' })).toString('base64url')}.x`;

function stubWorkOS() {
	return {
		userManagement: {
			authenticateWithPassword: vi.fn(async () => ({
				sealedSession: 'sealed',
				accessToken: ACCESS_TOKEN
			})),
			authenticateWithOrganizationSelection: vi.fn(async () => ({})),
			revokeSession: vi.fn(async () => undefined)
		}
	};
}

function event(search: string) {
	return {
		request: new Request(`http://portal.test/login${search}`, {
			method: 'POST',
			body: new URLSearchParams({ email: 'staff@firm.test', password: 'a passphrase' })
		}),
		url: new URL(`http://portal.test/login${search}`),
		cookies: { set: vi.fn(), get: vi.fn(), delete: vi.fn() },
		fetch: vi.fn(async (input: string | URL | Request) => {
			const target = String(input);
			if (target.endsWith('/me/portal'))
				return new Response(JSON.stringify({ portal: 'platform' }), { status: 200 });
			return new Response(JSON.stringify({ allowed: true, retry_after_seconds: 0 }), {
				status: 200
			});
		}) as unknown as typeof fetch,
		getClientAddress: () => '203.0.113.9',
		locals: {
			auth: {
				workos: stubWorkOS(),
				clientId: 'client_test',
				cookiePassword: 'unit-test-cookie-password-32-chars'
			}
		}
	};
}

type ActionArg = Parameters<typeof actions.default>[0];
type LoadArg = Parameters<typeof load>[0];

async function locationOf(search: string): Promise<string> {
	try {
		await actions.default(event(search) as unknown as ActionArg);
	} catch (e) {
		return (e as { location: string }).location;
	}
	throw new Error('expected a redirect');
}

describe('login redirect target', () => {
	it('returns the user to where their session expired', async () => {
		expect(await locationOf('?next=%2Fdocuments%2F42%3Ftab%3Dactivity')).toBe(
			'/documents/42?tab=activity'
		);
	});

	it('falls back to the home page with no next', async () => {
		expect(await locationOf('')).toBe('/');
	});

	it('refuses an off-origin next (open redirect)', async () => {
		for (const evil of ['https%3A%2F%2Fevil.test%2Fsteal', '%2F%2Fevil.test', '%2F%5Cevil.test']) {
			expect(await locationOf(`?next=${evil}`)).toBe('/');
		}
	});

	it('carries the destination into the MFA step', async () => {
		const stub = stubWorkOS();
		stub.userManagement.authenticateWithPassword = vi.fn(async () => {
			throw {
				rawData: {
					code: 'mfa_challenge',
					pending_authentication_token: 'pending_tok',
					authentication_factors: [{ type: 'totp', id: 'factor_1' }]
				}
			};
		});
		const ev = event('?next=%2Ftasks');
		ev.locals.auth.workos = stub;
		try {
			await actions.default(ev as unknown as ActionArg);
			throw new Error('expected a redirect');
		} catch (e) {
			expect((e as { location: string }).location).toBe('/mfa?next=%2Ftasks');
		}
	});

	it('keeps the destination when it sends the user off to confirm their email', async () => {
		// This exit was a bare '/login?check=1', so the place the user was trying to reach was lost
		// the moment their email turned out to be unconfirmed.
		const stub = stubWorkOS();
		stub.userManagement.authenticateWithPassword = vi.fn(async () => {
			throw {
				rawData: {
					code: 'email_verification_required',
					pending_authentication_token: 'pending_tok'
				}
			};
		});
		const ev = event('?next=%2Fdocuments%2F42');
		ev.locals.auth.workos = stub;
		// This branch asks the API to email a set-password link (via Resend); stub the call so the
		// test never depends on a reachable API, and assert it is actually made — `?check=1` tells
		// the user "we have sent you a link", so nothing having been sent would be a lie.
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		ev.fetch = doFetch as unknown as typeof fetch;
		try {
			await actions.default(ev as unknown as ActionArg);
			throw new Error('expected a redirect');
		} catch (e) {
			expect((e as { location: string }).location).toBe('/login?check=1&next=%2Fdocuments%2F42');
		}
		// Found by URL, not by position: the action also consults /internal/rate-limit first, and an
		// assertion on calls[0] would break the moment another internal call is added.
		const sent = (doFetch.mock.calls as unknown as Array<[string, RequestInit]>).find(([u]) =>
			u.includes('/internal/forgot-password')
		);
		expect(sent, 'a set-password link must actually be sent').toBeDefined();
		// The user's own address rides along: the API's per-IP cap on reset sends is keyed on it,
		// and without it the API can only see the portal container.
		expect(JSON.parse(String(sent![1].body))).toEqual({
			email: 'staff@firm.test',
			host: 'portal.test',
			client_ip: '203.0.113.9'
		});
	});
});

describe('login notice', () => {
	const noticeFor = async (search: string) => {
		// `load` redirects when already signed in, so its return type is a union with void.
		const result = (await load({
			locals: { user: null },
			url: new URL(`http://portal.test/login${search}`)
		} as unknown as LoadArg)) as { notice: { message: string } | null };
		return result.notice;
	};

	it('explains why the form is being shown', async () => {
		expect((await noticeFor('?next=%2Ftasks'))?.message).toMatch(/session has ended/i);
		expect((await noticeFor('?set=1'))?.message).toMatch(/password is set/i);
		expect((await noticeFor('?expired=1'))?.message).toMatch(/timed out/i);
		// The email-confirmation state has copy of its own — it used to be a bare `?check=1`
		// with nothing rendered for it.
		expect((await noticeFor('?check=1'))?.message).toMatch(/not confirmed/i);
		expect(await noticeFor('')).toBeNull();
	});
});

describe('login rate limiting', () => {
	/** `login_limits` shipped with no caller, so login was completely unthrottled while the abuse
	 *  control read as fixed in a diff. These pin the call site itself. */
	function eventWith(fetchImpl: typeof fetch) {
		const ev = event('') as unknown as Record<string, unknown>;
		ev.fetch = fetchImpl;
		return ev as unknown as ActionArg;
	}

	it('consumes a token on the login surface before authenticating', async () => {
		const calls: Array<{ url: string; body: unknown }> = [];
		const spy = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
			calls.push({ url: String(url), body: JSON.parse(String(init?.body ?? '{}')) });
			return new Response(JSON.stringify({ allowed: true, retry_after_seconds: 0 }), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			});
		}) as unknown as typeof fetch;

		try {
			await actions.default(eventWith(spy)); // redirects on success
		} catch {
			// the redirect throw is the success path here; we only care that the limiter was consulted
		}

		const hit = calls.find((c) => c.url.endsWith('/internal/rate-limit'));
		expect(hit, 'login must consult the rate limiter').toBeDefined();
		expect(hit!.body).toMatchObject({
			surface: 'login',
			identifier: 'staff@firm.test',
			host: 'portal.test',
			client_ip: '203.0.113.9'
		});
	});

	it('refuses with a retry message when the budget is spent, without calling WorkOS', async () => {
		const spy = (async () =>
			new Response(JSON.stringify({ allowed: false, retry_after_seconds: 180 }), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			})) as unknown as typeof fetch;

		const result = (await actions.default(eventWith(spy))) as unknown as {
			status: number;
			data: { error: string };
		};
		expect(result.status).toBe(429);
		expect(result.data.error).toMatch(/Too many sign-in attempts.*3 minutes/);
	});

	it('fails OPEN when the limiter is unreachable', async () => {
		const spy = (async (input: string | URL | Request) => {
			if (String(input).endsWith('/me/portal'))
				return new Response(JSON.stringify({ portal: 'platform' }), { status: 200 });
			throw new Error('connection refused');
		}) as unknown as typeof fetch;
		// Reaches WorkOS and redirects rather than locking everyone out of the product.
		await expect(actions.default(eventWith(spy))).rejects.toHaveProperty('location');
	});
});

describe('deactivated member sign-in', () => {
	it('does not write a session cookie and revokes the session WorkOS just minted', async () => {
		const ev = event('');
		ev.fetch = vi.fn(async (input: string | URL | Request) => {
			if (String(input).endsWith('/me/portal'))
				return new Response(JSON.stringify({ portal: null }), { status: 200 });
			return new Response(JSON.stringify({ allowed: true, retry_after_seconds: 0 }), {
				status: 200
			});
		}) as unknown as typeof fetch;

		const result = (await actions.default(ev as unknown as ActionArg)) as unknown as {
			status: number;
			data: { error: string };
		};

		expect(result.status).toBe(403);
		expect(result.data.error).toMatch(/deactivated/i);
		expect(ev.cookies.set).not.toHaveBeenCalled();
		expect(ev.locals.auth.workos.userManagement.revokeSession).toHaveBeenCalledWith({
			sessionId: 'session_1'
		});
	});
});
