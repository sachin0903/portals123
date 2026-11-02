import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	clearEnvironmentCache,
	mfaChallengeHandle,
	notifyOnboardingComplete,
	reportAuthEvent,
	requestPasswordReset,
	resolveEnvironment,
	safeClientAddress
} from './environment';

const OPTS = { apiUrl: 'http://api.test', internalToken: 'tok' };

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

const RAW = {
	tenant_id: 7,
	stage: 'production',
	workos_client_id: 'client_x',
	issuer: 'https://api.workos.com/user_management/client_x',
	workos_api_key: 'sk_live_x',
	cookie_password: 'a-cookie-password-at-least-32-bytes!!'
};

afterEach(() => clearEnvironmentCache());

describe('resolveEnvironment', () => {
	it('maps the API payload to a typed config and sends the internal token', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse(RAW));
		const cfg = await resolveEnvironment('app.firm.com', { ...OPTS, fetch });
		expect(cfg).toMatchObject({
			tenantId: 7,
			workosClientId: 'client_x',
			workosApiKey: 'sk_live_x',
			cookiePassword: 'a-cookie-password-at-least-32-bytes!!'
		});
		expect(fetch).toHaveBeenCalledWith('http://api.test/internal/env-config?host=app.firm.com', {
			headers: { 'x-internal-token': 'tok' },
			cache: 'no-store'
		});
	});

	it('caches positive hits (one fetch for repeated hosts)', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse(RAW));
		await resolveEnvironment('app.firm.com', { ...OPTS, fetch });
		await resolveEnvironment('app.firm.com', { ...OPTS, fetch });
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('bypassCache refetches a still-fresh host so branding saves are visible', async () => {
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(jsonResponse({ ...RAW, branding: { v: 1 } }))
			.mockResolvedValueOnce(jsonResponse({ ...RAW, branding: { v: 2 } }));
		const first = await resolveEnvironment('app.firm.com', { ...OPTS, fetch });
		const second = await resolveEnvironment('app.firm.com', {
			...OPTS,
			fetch,
			bypassCache: true
		});
		expect(fetch).toHaveBeenCalledTimes(2);
		expect(first?.branding).toEqual({ v: 1 });
		expect(second?.branding).toEqual({ v: 2 });
	});

	it('negative-caches an unknown host (404)', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse({ detail: 'Unknown host' }, 404));
		expect(await resolveEnvironment('nope.com', { ...OPTS, fetch })).toBeNull();
		expect(await resolveEnvironment('nope.com', { ...OPTS, fetch })).toBeNull();
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('does NOT cache transient errors (500) — retries next call', async () => {
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(jsonResponse({}, 500))
			.mockResolvedValueOnce(jsonResponse(RAW));
		expect(await resolveEnvironment('app.firm.com', { ...OPTS, fetch })).toBeNull();
		const cfg = await resolveEnvironment('app.firm.com', { ...OPTS, fetch });
		expect(cfg?.workosClientId).toBe('client_x');
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('does NOT cache a network failure', async () => {
		const fetch = vi
			.fn()
			.mockRejectedValueOnce(new Error('boom'))
			.mockResolvedValueOnce(jsonResponse(RAW));
		expect(await resolveEnvironment('app.firm.com', { ...OPTS, fetch })).toBeNull();
		expect((await resolveEnvironment('app.firm.com', { ...OPTS, fetch }))?.tenantId).toBe(7);
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('returns null (no fetch) when host or resolver config is missing', async () => {
		const fetch = vi.fn();
		expect(await resolveEnvironment('', { ...OPTS, fetch })).toBeNull();
		expect(await resolveEnvironment('h', { apiUrl: '', internalToken: 't', fetch })).toBeNull();
		expect(await resolveEnvironment('h', { apiUrl: 'x', internalToken: '', fetch })).toBeNull();
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe('reportAuthEvent', () => {
	it('posts a login with the WorkOS id and host to the internal endpoint', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		await reportAuthEvent('login', {
			...OPTS,
			host: 'firm.portal.test',
			workosUserId: 'user_42',
			fetch: doFetch as unknown as typeof fetch
		});
		const [url, init] = doFetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://api.test/internal/auth-event');
		expect(JSON.parse(String(init.body))).toEqual({
			kind: 'login',
			workos_user_id: 'user_42',
			host: 'firm.portal.test'
		});
	});

	it('stamps the browser on a successful sign-in', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		const userAgent =
			'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
		await reportAuthEvent('login', {
			...OPTS,
			host: 'firm.portal.test',
			workosUserId: 'user_42',
			method: 'password',
			client: { ipAddress: '203.0.113.8', userAgent },
			fetch: doFetch as unknown as typeof fetch
		});
		expect(
			JSON.parse(String((doFetch.mock.calls[0] as unknown as [string, RequestInit])[1].body))
		).toEqual({
			kind: 'login',
			workos_user_id: 'user_42',
			method: 'password',
			host: 'firm.portal.test',
			client_ip: '203.0.113.8',
			user_agent: userAgent,
			device: 'Chrome on macOS'
		});
	});

	it('posts a failed login with the attempted email and no subject', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		await reportAuthEvent('login_failed', {
			...OPTS,
			host: 'h.test',
			email: 'wrong@firm.test',
			fetch: doFetch as unknown as typeof fetch
		});
		expect(
			JSON.parse(String((doFetch.mock.calls[0] as unknown as [string, RequestInit])[1].body))
		).toEqual({ kind: 'login_failed', email: 'wrong@firm.test', host: 'h.test' });
	});

	it('is a no-op without an apiUrl or internal token (never throws)', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		await reportAuthEvent('logout', {
			apiUrl: '',
			internalToken: '',
			host: 'h.test',
			workosUserId: 'user_1',
			fetch: doFetch as unknown as typeof fetch
		});
		expect(doFetch).not.toHaveBeenCalled();
	});
});

describe('notifyOnboardingComplete', () => {
	// The portals used to hold their own copy of this and call it with `res.email ?? ''`, which was
	// always empty — so the push silently did nothing and people who had onboarded stayed "pending"
	// in the mirror. It now reports whether it actually sent, so "did nothing" cannot hide.
	it('posts the identity and the host to the internal endpoint', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		const sent = await notifyOnboardingComplete(
			{ workosUserId: 'user_42', email: 'jo@firm.example' },
			'firm.portal.test',
			{ ...OPTS, fetch: doFetch as unknown as typeof fetch }
		);
		expect(sent).toBe(true);
		const [url, init] = doFetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe('http://api.test/internal/onboarding-complete');
		expect(JSON.parse(String(init.body))).toEqual({
			workos_user_id: 'user_42',
			email: 'jo@firm.example',
			host: 'firm.portal.test'
		});
	});

	it('sends with only a user id (no email needed)', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		expect(
			await notifyOnboardingComplete({ workosUserId: 'user_42' }, 'h.test', {
				...OPTS,
				fetch: doFetch as unknown as typeof fetch
			})
		).toBe(true);
		expect(
			JSON.parse(String((doFetch.mock.calls[0] as unknown as [string, RequestInit])[1].body))
		).toEqual({
			workos_user_id: 'user_42',
			host: 'h.test'
		});
	});

	it('reports false rather than pretending, when there is no identity or no config', async () => {
		const doFetch = vi.fn(async () => new Response('{}', { status: 200 }));
		const f = { fetch: doFetch as unknown as typeof fetch };
		expect(await notifyOnboardingComplete({}, 'h.test', { ...OPTS, ...f })).toBe(false);
		expect(
			await notifyOnboardingComplete({ email: null, workosUserId: null }, 'h.test', {
				...OPTS,
				...f
			})
		).toBe(false);
		expect(
			await notifyOnboardingComplete({ workosUserId: 'user_42' }, 'h.test', {
				apiUrl: '',
				internalToken: 'tok',
				...f
			})
		).toBe(false);
		expect(doFetch).not.toHaveBeenCalled();
	});

	it('never throws when the API is unreachable', async () => {
		const doFetch = vi.fn(async () => {
			throw new Error('ECONNREFUSED');
		});
		await expect(
			notifyOnboardingComplete({ workosUserId: 'user_42' }, 'h.test', {
				...OPTS,
				fetch: doFetch as unknown as typeof fetch
			})
		).resolves.toBe(false);
	});
});

describe('requestPasswordReset', () => {
	it('carries the end user\u2019s IP so the API\u2019s per-IP cap on the surface has a key', async () => {
		const doFetch = vi.fn(async () => jsonResponse({ ok: true }));
		await requestPasswordReset('a@b.test', 'app.firm.com', {
			...OPTS,
			clientIp: '203.0.113.9',
			fetch: doFetch as unknown as typeof fetch
		});
		const [, init] = doFetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(JSON.parse(String(init.body))).toEqual({
			email: 'a@b.test',
			host: 'app.firm.com',
			client_ip: '203.0.113.9'
		});
	});

	it('sends an explicit null when the portal could not read the address', async () => {
		const doFetch = vi.fn(async () => jsonResponse({ ok: true }));
		await requestPasswordReset('a@b.test', 'h', {
			...OPTS,
			fetch: doFetch as unknown as typeof fetch
		});
		const [, init] = doFetch.mock.calls[0] as unknown as [string, RequestInit];
		expect(JSON.parse(String(init.body)).client_ip).toBeNull();
	});
});

describe('safeClientAddress', () => {
	it('returns the address, or null when the adapter throws or has none', () => {
		expect(safeClientAddress(() => '10.0.0.1')).toBe('10.0.0.1');
		expect(safeClientAddress(() => '')).toBeNull();
		expect(
			safeClientAddress(() => {
				throw new Error('Could not determine clientAddress');
			})
		).toBeNull();
	});
});

describe('mfaChallengeHandle', () => {
	it('is a stable digest that does not contain the pending token', async () => {
		const a = await mfaChallengeHandle('pending_abc');
		expect(a).toBe(await mfaChallengeHandle('pending_abc'));
		expect(a).not.toBe(await mfaChallengeHandle('pending_abd'));
		expect(a).toMatch(/^[0-9a-f]{32}$/);
		expect(a).not.toContain('pending');
	});
});
