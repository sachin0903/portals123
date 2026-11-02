/**
 * The client portal, arrived at by a member of staff viewing one of their clients.
 *
 * Two things are worth pinning down here and nowhere else. The handoff must leave no durable
 * credential in the URL — the ticket is exchanged server-side and the token only ever reaches a
 * cookie — and a session that has stopped must stop the portal with it, rather than leaving a
 * dead cookie retrying for an hour.
 */

import { describe, expect, it, vi } from 'vitest';

vi.mock('$env/dynamic/private', () => ({
	env: { API_URL: 'http://api.test', INTERNAL_API_TOKEN: 'internal-token' }
}));
// The real module constructs a WorkOS client and reads `$app/environment`; only the cookie name
// and the shared options are needed here.
vi.mock('$lib/server/workos', () => ({
	VIEW_AS_COOKIE: 'gp-view-as-client',
	cookieOptions: { path: '/', httpOnly: true, sameSite: 'lax', secure: false }
}));

const DETAIL = {
	uid: 'abcdefghijkl',
	adviser_name: 'Rowan Adviser',
	contact_id: 7,
	contact_name: 'Beatrice Client',
	reason: 'Telephone call',
	expires_at: '2026-09-21T13:00:00Z',
	seconds_remaining: 3600
};

const { GET } = await import('./+server');
const { POST } = await import('./exit/+server');
const { redeemHandoff, resolveViewAs } = await import('$lib/server/view-as');

type Cookie = { name: string; value: string; opts: Record<string, unknown> };

function cookieJar(initial: Record<string, string> = {}) {
	const set: Cookie[] = [];
	const deleted: string[] = [];
	return {
		set,
		deleted,
		api: {
			get: (name: string) => initial[name],
			set: (name: string, value: string, opts: Record<string, unknown>) =>
				set.push({ name, value, opts }),
			delete: (name: string) => deleted.push(name)
		}
	};
}

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const fail = (status: number) => ({ ok: false, status, json: async () => ({}) }) as Response;

/** The route handlers redirect, which SvelteKit throws; unwrap it to the status + location. */
async function caught(run: () => unknown): Promise<{ status: number; location: string }> {
	try {
		await run();
	} catch (err) {
		const r = err as { status: number; location: string };
		if (typeof r?.status === 'number') return r;
		throw err;
	}
	throw new Error('expected a redirect');
}

describe('arriving with a handoff ticket', () => {
	it('exchanges the ticket server-side and puts only the token in a cookie', async () => {
		const fetch = vi.fn(async () =>
			ok({ token: 'viewas_secret', detail: DETAIL })
		) as unknown as typeof globalThis.fetch;
		const jar = cookieJar();

		const redirect = await caught(() =>
			GET({
				url: new URL('https://portal.test/view-as?ticket=viewas-handoff_abc'),
				cookies: jar.api,
				fetch
			} as never)
		);

		// The ticket went to the API with the internal token, never to the browser.
		const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(String(url)).toBe('http://api.test/view-as/redeem');
		expect((init.headers as Record<string, string>)['x-internal-token']).toBe('internal-token');
		expect(JSON.parse(init.body as string)).toEqual({ handoff_ticket: 'viewas-handoff_abc' });

		expect(jar.set).toHaveLength(1);
		expect(jar.set[0].name).toBe('gp-view-as-client');
		expect(jar.set[0].value).toBe('viewas_secret');
		expect(jar.set[0].opts.httpOnly).toBe(true);
		// The cookie lapses with the session rather than outliving what it points at.
		expect(jar.set[0].opts.maxAge).toBe(3600);
		// And the address bar it lands on is the portal's own.
		expect(redirect).toEqual({ status: 303, location: '/' });
	});

	it('sends a spent or unknown ticket to the ended page rather than an empty portal', async () => {
		const fetch = vi.fn(async () => fail(404)) as unknown as typeof globalThis.fetch;
		const jar = cookieJar();

		const redirect = await caught(() =>
			GET({
				url: new URL('https://portal.test/view-as?ticket=viewas-handoff_spent'),
				cookies: jar.api,
				fetch
			} as never)
		);

		expect(jar.set).toHaveLength(0);
		expect(redirect.location).toBe('/view-as/ended');
	});
});

describe('leaving', () => {
	it('closes the session at the API and drops the cookie', async () => {
		const fetch = vi.fn(async () => ok({})) as unknown as typeof globalThis.fetch;
		const jar = cookieJar({ 'gp-view-as-client': 'viewas_secret' });

		const redirect = await caught(() => POST({ cookies: jar.api, fetch } as never));

		const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(String(url)).toBe('http://api.test/view-as/end');
		expect((init.headers as Record<string, string>).Authorization).toBe('Bearer viewas_secret');
		expect(jar.deleted).toContain('gp-view-as-client');
		// `left=1` is what words the ended page for someone who chose to go; a failed handoff
		// lands there without it.
		expect(redirect.location).toBe('/view-as/ended?left=1');
	});

	it('still clears the cookie when the API cannot be reached', async () => {
		const fetch = vi.fn(async () => {
			throw new Error('unreachable');
		}) as unknown as typeof globalThis.fetch;
		const jar = cookieJar({ 'gp-view-as-client': 'viewas_secret' });

		await caught(() => POST({ cookies: jar.api, fetch } as never));

		expect(jar.deleted).toContain('gp-view-as-client');
	});
});

describe('resolving the session on each request', () => {
	it('reports the session behind a live token', async () => {
		const fetch = vi.fn(async () => ok(DETAIL)) as unknown as typeof globalThis.fetch;

		expect(await resolveViewAs('viewas_secret', fetch)).toEqual({
			uid: 'abcdefghijkl',
			adviserName: 'Rowan Adviser',
			contactId: 7,
			contactName: 'Beatrice Client',
			reason: 'Telephone call',
			expiresAt: '2026-09-21T13:00:00Z',
			secondsRemaining: 3600
		});
	});

	it('reports nothing for a session that has ended, so the cookie can be dropped', async () => {
		const fetch = vi.fn(async () => fail(401)) as unknown as typeof globalThis.fetch;
		expect(await resolveViewAs('viewas_secret', fetch)).toBeNull();
	});

	it('does not call the API without an internal token to call it with', async () => {
		const fetch = vi.fn() as unknown as typeof globalThis.fetch;
		expect(await redeemHandoff('viewas-handoff_abc', { internalToken: '', fetch })).toBeNull();
		expect(fetch).not.toHaveBeenCalled();
	});
});
