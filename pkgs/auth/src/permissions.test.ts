import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
	PERMISSIONS_TTL_MS,
	clearPermissionsCache,
	fetchMe,
	fetchPermissions
} from './permissions';

const OPTS = { apiUrl: 'http://api.test' };

function jsonResponse(body: unknown, status = 200): Response {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

describe('fetchMe', () => {
	beforeEach(() => clearPermissionsCache());

	it('returns permissions and portal from GET /me, with the bearer token attached', async () => {
		const fetch = vi
			.fn()
			.mockResolvedValue(jsonResponse({ permissions: ['contacts:read'], portal: 'platform' }));
		const me = await fetchMe('tok_123', { ...OPTS, fetch });
		expect(me).toEqual({ permissions: ['contacts:read'], portal: 'platform' });
		expect(fetch).toHaveBeenCalledWith('http://api.test/me', {
			headers: { Authorization: 'Bearer tok_123' }
		});
	});

	it('treats a confirmed null portal as null, not as "no answer"', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse({ permissions: [], portal: null }));
		expect(await fetchMe('tok', { ...OPTS, fetch })).toEqual({
			permissions: [],
			portal: null
		});
	});

	it('fails open on portal (undefined) and closed on permissions when there is no token', async () => {
		const fetch = vi.fn();
		expect(await fetchMe(null, { ...OPTS, fetch })).toEqual({
			permissions: [],
			portal: undefined
		});
		expect(fetch).not.toHaveBeenCalled();
	});

	it('fails open on portal on a non-OK response', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse({ detail: 'nope' }, 401));
		expect(await fetchMe('tok', { ...OPTS, fetch })).toEqual({
			permissions: [],
			portal: undefined
		});
	});

	it('fails open on portal on a network error', async () => {
		const fetch = vi.fn().mockRejectedValue(new Error('network down'));
		expect(await fetchMe('tok', { ...OPTS, fetch })).toEqual({
			permissions: [],
			portal: undefined
		});
	});

	it('drops non-string permission entries rather than trusting a malformed body', async () => {
		const fetch = vi.fn().mockResolvedValue(
			jsonResponse({
				permissions: ['contacts:read', 42, null, 'messages:write'],
				portal: 'platform'
			})
		);
		expect(await fetchMe('tok', { ...OPTS, fetch })).toEqual({
			permissions: ['contacts:read', 'messages:write'],
			portal: 'platform'
		});
	});

	it('reuses GET /me within the TTL for the same token', async () => {
		const fetch = vi
			.fn()
			.mockResolvedValue(jsonResponse({ permissions: ['contacts:read'], portal: 'platform' }));
		expect(await fetchMe('tok_ttl', { ...OPTS, fetch })).toEqual({
			permissions: ['contacts:read'],
			portal: 'platform'
		});
		expect(await fetchMe('tok_ttl', { ...OPTS, fetch })).toEqual({
			permissions: ['contacts:read'],
			portal: 'platform'
		});
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('holds the answer long enough to span a run of navigations, then asks again', async () => {
		// The hook awaits GET /me before any HTML can go out, so on a warm session it is on the
		// critical path of every page. The window has to cover a reader clicking between pages a
		// few seconds apart (15–30s), and still expire so a revoke lands.
		expect(PERMISSIONS_TTL_MS).toBeGreaterThanOrEqual(15_000);
		expect(PERMISSIONS_TTL_MS).toBeLessThanOrEqual(30_000);

		const fetch = vi
			.fn()
			.mockResolvedValue(jsonResponse({ permissions: ['contacts:read'], portal: 'platform' }));
		const spy = vi.spyOn(Date, 'now');
		try {
			spy.mockReturnValue(1_000);
			await fetchMe('tok_span', { ...OPTS, fetch });
			spy.mockReturnValue(1_000 + PERMISSIONS_TTL_MS - 1);
			await fetchMe('tok_span', { ...OPTS, fetch });
			expect(fetch).toHaveBeenCalledTimes(1);
			spy.mockReturnValue(1_000 + PERMISSIONS_TTL_MS);
			await fetchMe('tok_span', { ...OPTS, fetch });
			expect(fetch).toHaveBeenCalledTimes(2);
		} finally {
			spy.mockRestore();
		}
	});

	it('never serves one token’s answer to another', async () => {
		// Keyed by the access token, not by anything coarser: two sessions hitting the same portal
		// within the window must each get their own grants, or a cache hit becomes a privilege leak.
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(jsonResponse({ permissions: ['system'], portal: 'operations' }))
			.mockResolvedValueOnce(jsonResponse({ permissions: ['contacts:read'], portal: 'platform' }));
		expect(await fetchMe('tok_ops', { ...OPTS, fetch })).toEqual({
			permissions: ['system'],
			portal: 'operations'
		});
		expect(await fetchMe('tok_adviser', { ...OPTS, fetch })).toEqual({
			permissions: ['contacts:read'],
			portal: 'platform'
		});
		expect(fetch).toHaveBeenCalledTimes(2);
		// And the second lookup did not evict the first.
		expect(await fetchMe('tok_ops', { ...OPTS, fetch })).toEqual({
			permissions: ['system'],
			portal: 'operations'
		});
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('does not cache a failure — the next request asks again', async () => {
		// A blip must not pin "no grants / no portal" for the whole window: that would lock a
		// reader out of every nav link until the TTL ran out.
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(jsonResponse({ detail: 'down' }, 503))
			.mockResolvedValueOnce(jsonResponse({ permissions: ['contacts:read'], portal: 'platform' }));
		expect(await fetchMe('tok_blip', { ...OPTS, fetch })).toEqual({
			permissions: [],
			portal: undefined
		});
		expect(await fetchMe('tok_blip', { ...OPTS, fetch })).toEqual({
			permissions: ['contacts:read'],
			portal: 'platform'
		});
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('sweeps expired entries when it writes, so the per-token map cannot grow without bound', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse({ permissions: [], portal: 'client' }));
		const spy = vi.spyOn(Date, 'now');
		try {
			spy.mockReturnValue(1_000);
			await fetchMe('tok_a', { ...OPTS, fetch });
			await fetchMe('tok_b', { ...OPTS, fetch });
			spy.mockReturnValue(1_000 + PERMISSIONS_TTL_MS + 1);
			// Both have expired. Asking for one re-fetches it (proves expiry) and, on the write,
			// sweeps the other — so asking for that one afterwards is also a fresh fetch.
			await fetchMe('tok_a', { ...OPTS, fetch });
			expect(fetch).toHaveBeenCalledTimes(3);
			spy.mockReturnValue(1_000 + PERMISSIONS_TTL_MS + 2);
			await fetchMe('tok_b', { ...OPTS, fetch });
			expect(fetch).toHaveBeenCalledTimes(4);
		} finally {
			spy.mockRestore();
		}
	});
});

describe('fetchPermissions', () => {
	beforeEach(() => clearPermissionsCache());
	it('returns the permissions array from GET /me, with the bearer token attached', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse({ permissions: ['contacts:read'] }));
		const permissions = await fetchPermissions('tok_123', { ...OPTS, fetch });
		expect(permissions).toEqual(['contacts:read']);
		expect(fetch).toHaveBeenCalledWith('http://api.test/me', {
			headers: { Authorization: 'Bearer tok_123' }
		});
	});

	it('returns [] without calling fetch when there is no access token', async () => {
		const fetch = vi.fn();
		expect(await fetchPermissions(null, { ...OPTS, fetch })).toEqual([]);
		expect(fetch).not.toHaveBeenCalled();
	});

	it('fails closed to [] on a non-OK response', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse({ detail: 'nope' }, 401));
		expect(await fetchPermissions('tok', { ...OPTS, fetch })).toEqual([]);
	});

	it('fails closed to [] on a network error', async () => {
		const fetch = vi.fn().mockRejectedValue(new Error('network down'));
		expect(await fetchPermissions('tok', { ...OPTS, fetch })).toEqual([]);
	});

	it('fails closed to [] when the body has no permissions array', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse({ user_id: 'u1' }));
		expect(await fetchPermissions('tok', { ...OPTS, fetch })).toEqual([]);
	});

	it('drops non-string entries rather than trusting a malformed body', async () => {
		const fetch = vi
			.fn()
			.mockResolvedValue(
				jsonResponse({ permissions: ['contacts:read', 42, null, 'messages:write'] })
			);
		expect(await fetchPermissions('tok', { ...OPTS, fetch })).toEqual([
			'contacts:read',
			'messages:write'
		]);
	});

	it('reuses GET /me within the TTL for the same token', async () => {
		const fetch = vi.fn().mockResolvedValue(jsonResponse({ permissions: ['contacts:read'] }));
		expect(await fetchPermissions('tok_ttl', { ...OPTS, fetch })).toEqual(['contacts:read']);
		expect(await fetchPermissions('tok_ttl', { ...OPTS, fetch })).toEqual(['contacts:read']);
		expect(fetch).toHaveBeenCalledTimes(1);
	});
});
