/**
 * The hook's post-authentication gate — wrong portal — on the requests a layout-only check never
 * saw: `+server.ts` endpoint calls. A layout `load` runs for pages, so every browser-callable
 * proxy on this portal served a session the pages beside it had refused.
 *
 * The login terms gate used to sit beside it. A signed-in session is not refused for terms, and
 * no request here calls `GET /terms/outstanding`.
 *
 * The WorkOS session and the API are stubbed at the module boundary; `decideGate` and friends
 * are the real ones.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const stubs = vi.hoisted(() => ({
	portal: 'client' as string | null | undefined,
	revoke: vi.fn(async () => {}),
	authenticated: true,
	/** The view-as cookie's value, when the browser carries one. */
	viewAsToken: undefined as string | undefined,
	/** What GET /view-as/session answers for it: 200 with the session, or a refusal. */
	viewAsStatus: 200
}));

const VIEW_AS_DETAIL = {
	uid: 'abcdefghijkl',
	adviser_name: 'Rowan Adviser',
	contact_id: 7,
	contact_name: 'Beatrice Client',
	reason: 'Telephone call',
	expires_at: '2026-09-21T13:00:00Z',
	seconds_remaining: 3600
};

vi.mock('$env/dynamic/private', () => ({ env: {} }));
vi.mock('$lib/api', () => ({ apiUrl: () => 'http://api.test' }));
vi.mock('$lib/server/classification', () => ({ fetchMyClassification: async () => null }));
vi.mock('$lib/server/groups', () => ({ fetchMyGroups: async () => [] }));
vi.mock('$lib/server/workos', () => ({
	SESSION_COOKIE: 'wos-session-client',
	DEV_AUTH_COOKIE: 'dev-auth',
	ACTIVE_CONTACT_COOKIE: 'gp-active-contact-client',
	VIEW_AS_COOKIE: 'gp-view-as-client',
	cookieOptions: { path: '/' },
	resolveAuth: async () => ({
		workos: { userManagement: { loadSealedSession: () => ({}), revokeSession: stubs.revoke } },
		cookiePassword: 'x',
		branding: null,
		appName: 'Test'
	})
}));
vi.mock('@graphene/auth', async (importOriginal) => {
	const real = await importOriginal<typeof import('@graphene/auth')>();
	return {
		...real,
		resolveSession: async () => ({
			result: stubs.authenticated
				? {
						authenticated: true,
						user: { id: 'u1', email: 'c@x.test' },
						accessToken: `tok-${Math.random()}`,
						sessionId: 'sess_1'
					}
				: { authenticated: false }
		}),
		fetchMe: async () => ({ permissions: [], portal: stubs.portal })
	};
});

import { handle } from './hooks.server';

type HandleArg = Parameters<typeof handle>[0];

function request(path: string, headers: Record<string, string> = {}, isDataRequest = false) {
	const deleted: string[] = [];
	const locals: Record<string, unknown> = {};
	const event = {
		url: new URL(`http://portal.test${path}`),
		request: new Request(`http://portal.test${path}`, { headers }),
		cookies: {
			get: (name: string) => {
				if (name === 'wos-session-client') return 'sealed';
				if (name === 'gp-view-as-client') return stubs.viewAsToken;
				return undefined;
			},
			set: () => {},
			delete: (name: string) => deleted.push(name)
		},
		locals,
		isDataRequest,
		getClientAddress: () => '127.0.0.1',
		fetch: vi.fn(async (url: string, init?: RequestInit) => {
			if (url.includes('/terms/outstanding')) {
				throw new Error(`terms must not be fetched on navigation: ${url}`);
			}
			if (url.includes('/view-as/session')) {
				const auth = (init?.headers as Record<string, string> | undefined)?.Authorization;
				if (auth !== `Bearer ${stubs.viewAsToken}`) return new Response(null, { status: 401 });
				return new Response(JSON.stringify(VIEW_AS_DETAIL), { status: stubs.viewAsStatus });
			}
			throw new Error(`unexpected fetch ${url}`);
		})
	} as unknown as HandleArg['event'];
	const resolve = vi.fn(
		async () => new Response('page', { headers: { 'content-type': 'text/html' } })
	);
	return { event, resolve, deleted, locals };
}

const endpoint = { 'sec-fetch-dest': 'empty', accept: 'application/json' };
const document = { 'sec-fetch-dest': 'document', accept: 'text/html' };

beforeEach(() => {
	stubs.portal = 'client';
	stubs.authenticated = true;
	stubs.viewAsToken = undefined;
	stubs.viewAsStatus = 200;
	stubs.revoke.mockClear();
});

describe('a member of staff viewing the portal as a client', () => {
	it('runs the request on the view-as token, ahead of any WorkOS session on the host', async () => {
		stubs.viewAsToken = 'viewas_secret';
		const { event, resolve, locals } = request('/accounts', document);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(200);
		expect(locals['accessToken']).toBe('viewas_secret');
		expect(locals['viewAs']).toMatchObject({
			uid: 'abcdefghijkl',
			contactName: 'Beatrice Client',
			adviserName: 'Rowan Adviser',
			secondsRemaining: 3600
		});
		expect(locals['portal']).toBe('client');
		expect(locals['gate']).toBe('allow');
	});

	// Terms are not asked of anyone, including a view-as session.
	it('does not fetch outstanding terms', async () => {
		stubs.viewAsToken = 'viewas_secret';
		const { event, resolve, locals } = request('/accounts', document);
		await handle({ event, resolve });
		expect(locals['gate']).toBe('allow');
		const asked = (event.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
		expect(asked.some((u) => u.includes('/terms/outstanding'))).toBe(false);
	});

	// `recordPageView` sends on the global fetch, not the event's, so that is what is watched.
	it('does not record the client’s page views', async () => {
		const sent = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response());
		try {
			stubs.viewAsToken = 'viewas_secret';
			const { event, resolve } = request('/accounts', document);
			await handle({ event, resolve });
			await Promise.resolve();
			const asked = sent.mock.calls.map((c) => String(c[0]));
			expect(asked.some((u) => u.includes('/me/page-view'))).toBe(false);

			// And the same navigation on a client's own session IS recorded — so the check above
			// is proving an exemption, not an absence.
			stubs.viewAsToken = undefined;
			const own = request('/accounts', document);
			await handle({ event: own.event, resolve: own.resolve });
			await Promise.resolve();
			const after = sent.mock.calls.map((c) => String(c[0]));
			expect(after.some((u) => u.includes('/me/page-view'))).toBe(true);
		} finally {
			sent.mockRestore();
		}
	});

	// Revocability is the point of the session being a row: ended or lapsed, it stops on the
	// next request — and the dead cookie goes with it rather than retrying for an hour.
	it('drops a cookie whose session has ended and falls back to the ordinary flow', async () => {
		stubs.viewAsToken = 'viewas_stale';
		stubs.viewAsStatus = 401;
		const { event, resolve, locals, deleted } = request('/accounts', document);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(200);
		expect(deleted).toContain('gp-view-as-client');
		expect(locals['viewAs']).toBeNull();
		// The WorkOS session on the host is what the request then runs on.
		expect(String(locals['accessToken'])).toMatch(/^tok-/);
	});

	// /view-as/* does not use the view-as cookie (those routes mint and end it), and it must not
	// fall through to the WorkOS cookie either. On localhost the adviser portal's login is on
	// the same host, and the wrong-portal gate would revoke it — Leave would sign them out.
	it('does not revoke the other login that shares this host', async () => {
		stubs.viewAsToken = 'viewas_secret';
		stubs.portal = 'platform';
		const { event, resolve } = request('/view-as/exit', endpoint);
		await handle({ event, resolve });
		expect(stubs.revoke).not.toHaveBeenCalled();
		expect(event.locals.user).toBeNull();
		expect(event.locals.accessToken).toBeNull();
	});

	it('is not consulted on the routes that mint and end it', async () => {
		stubs.viewAsToken = 'viewas_secret';
		const { event, resolve, locals } = request('/view-as/ended', document);
		await handle({ event, resolve });
		expect(locals['viewAs']).toBeNull();
		const asked = (event.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
		expect(asked.some((u) => u.includes('/view-as/session'))).toBe(false);
	});
});

describe('the hook cross-site check', () => {
	it('refuses a mutation another site sent, before any session work', async () => {
		const { event, resolve } = request('/messages/send', {
			'sec-fetch-site': 'cross-site',
			accept: 'application/json'
		});
		(event as unknown as { request: Request }).request = new Request(
			'http://portal.test/messages/send',
			{ method: 'POST', headers: { 'sec-fetch-site': 'cross-site' } }
		);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(403);
		expect(await res.json()).toEqual({ error: 'cross_site' });
		expect(resolve).not.toHaveBeenCalled();
		expect(event.fetch).not.toHaveBeenCalled();
	});

	it('lets a same-origin mutation through to the gate', async () => {
		const { event, resolve } = request('/messages/send', endpoint);
		(event as unknown as { request: Request }).request = new Request(
			'http://portal.test/messages/send',
			{ method: 'POST', headers: { 'sec-fetch-site': 'same-origin', ...endpoint } }
		);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(200);
	});
});

describe('the hook gate on endpoint calls', () => {
	it('lets a clean session through and records the decision', async () => {
		const { event, resolve, locals } = request('/messages/send', endpoint);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(200);
		expect(locals['gate']).toBe('allow');
		expect(resolve).toHaveBeenCalled();
	});

	it('does not fetch /terms/outstanding, and does not refuse a session for terms', async () => {
		const page = request('/accounts', document);
		const pageRes = await handle({ event: page.event, resolve: page.resolve });
		expect(pageRes.status).toBe(200);
		expect(page.locals['gate']).toBe('allow');
		expect(page.resolve).toHaveBeenCalled();

		const endpointCall = request('/messages/send', endpoint);
		const endpointRes = await handle({
			event: endpointCall.event,
			resolve: endpointCall.resolve
		});
		expect(endpointRes.status).toBe(200);
		expect(endpointCall.locals['gate']).toBe('allow');

		const data = request('/accounts/__data.json', endpoint, true);
		const dataRes = await handle({ event: data.event, resolve: data.resolve });
		expect(dataRes.status).toBe(200);
		expect(data.locals['gate']).toBe('allow');

		for (const ev of [page.event, endpointCall.event, data.event]) {
			const asked = (ev.fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
			expect(asked.some((u) => u.includes('/terms/outstanding'))).toBe(false);
		}
	});

	it('ends a session confirmed for another portal: revoke, clear, refuse', async () => {
		stubs.portal = 'platform';
		const { event, resolve, deleted, locals } = request('/notifications/mark', endpoint);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(403);
		expect(await res.json()).toEqual({ error: 'wrong_portal' });
		expect(stubs.revoke).toHaveBeenCalledWith({ sessionId: 'sess_1' });
		expect(deleted).toContain('wos-session-client');
		expect(locals['user']).toBeNull();
		expect(locals['accessToken']).toBeNull();
	});

	it('leaves a wrong-portal navigation to the layout', async () => {
		stubs.portal = 'platform';
		const { event, resolve, deleted, locals } = request('/accounts', document);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(200);
		expect(locals['gate']).toBe('wrong_portal');
		expect(resolve).toHaveBeenCalled();
		expect(stubs.revoke).toHaveBeenCalledWith({ sessionId: 'sess_1' });
		expect(deleted).toContain('wos-session-client');
	});

	it('fails open on portal when GET /me gave no answer', async () => {
		stubs.portal = undefined;
		const { event, resolve, locals } = request('/messages/send', endpoint);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(200);
		expect(locals['gate']).toBe('allow');
		expect(stubs.revoke).not.toHaveBeenCalled();
	});

	it('does not gate a signed-out request — the endpoint answers 401 itself', async () => {
		stubs.authenticated = false;
		const { event, resolve, locals } = request('/messages/send', endpoint);
		const res = await handle({ event, resolve });
		expect(res.status).toBe(200);
		expect(locals['gate']).toBe('allow');
		expect(event.fetch).not.toHaveBeenCalled();
	});
});
