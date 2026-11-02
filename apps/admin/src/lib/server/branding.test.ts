/**
 * The admin shell-brand resolver: mapping (payload → Brand, all-unset → null), the 60s cache
 * (positive AND 404-negative), and the transient-failure rule (never cached, so one blip never
 * pins the static fallback for the whole TTL) — the resolveEnvironment contract, one endpoint.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clearAdminBrandCache, fetchAdminBrand } from './branding';

const payload = {
	app_name: 'Graphene Platforms',
	logo_url: 'https://cdn.test/graphene.svg',
	logo_dark_url: 'https://cdn.test/graphene-dark.svg',
	icon_url: 'https://cdn.test/graphene-mark.svg',
	logo_variant: 'wordmark' as const
};

function respond(status: number, body?: unknown): Response {
	return new Response(body === undefined ? null : JSON.stringify(body), { status });
}

const opts = { apiUrl: 'http://api.test', internalToken: 'tok' };

beforeEach(() => clearAdminBrandCache());

describe('fetchAdminBrand', () => {
	it('maps the payload onto the shell Brand shape (logos/icon/name only)', async () => {
		const fetch = vi.fn().mockResolvedValue(respond(200, payload));
		const brand = await fetchAdminBrand({ ...opts, fetch });
		expect(brand).toEqual({
			name: 'Graphene Platforms',
			logoUrl: 'https://cdn.test/graphene.svg',
			logoDarkUrl: 'https://cdn.test/graphene-dark.svg',
			iconUrl: 'https://cdn.test/graphene-mark.svg',
			logoVariant: 'wordmark'
		});
		expect(fetch).toHaveBeenCalledWith('http://api.test/internal/admin-branding', {
			headers: { 'x-internal-token': 'tok' }
		});
	});

	it('returns null for an all-unset branding, so the shells keep the static default', async () => {
		const fetch = vi.fn().mockResolvedValue(
			respond(200, {
				app_name: null,
				logo_url: null,
				logo_dark_url: null,
				icon_url: null,
				logo_variant: 'wordmark'
			})
		);
		expect(await fetchAdminBrand({ ...opts, fetch })).toBeNull();
	});

	it('returns null without fetching when the resolver is unconfigured', async () => {
		const fetch = vi.fn();
		expect(await fetchAdminBrand({ apiUrl: '', internalToken: 'tok', fetch })).toBeNull();
		expect(
			await fetchAdminBrand({ apiUrl: 'http://api.test', internalToken: '', fetch })
		).toBeNull();
		expect(fetch).not.toHaveBeenCalled();
	});

	it('caches a hit for the TTL and refetches after it', async () => {
		const fetch = vi.fn().mockResolvedValue(respond(200, payload));
		await fetchAdminBrand({ ...opts, fetch, cacheTtlMs: 60_000 });
		await fetchAdminBrand({ ...opts, fetch, cacheTtlMs: 60_000 });
		expect(fetch).toHaveBeenCalledTimes(1);

		clearAdminBrandCache();
		await fetchAdminBrand({ ...opts, fetch, cacheTtlMs: 60_000 });
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('bypassCache refetches a still-fresh brand so a logo save is on the next paint', async () => {
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(respond(200, payload))
			.mockResolvedValueOnce(respond(200, { ...payload, app_name: 'Graphene' }));
		const first = await fetchAdminBrand({ ...opts, fetch });
		const second = await fetchAdminBrand({ ...opts, fetch, bypassCache: true });
		expect(fetch).toHaveBeenCalledTimes(2);
		expect(first?.name).toBe('Graphene Platforms');
		expect(second?.name).toBe('Graphene');
	});

	it('negative-caches a 404 (unregistered core env / token gate)', async () => {
		const fetch = vi.fn().mockResolvedValue(respond(404));
		expect(await fetchAdminBrand({ ...opts, fetch })).toBeNull();
		expect(await fetchAdminBrand({ ...opts, fetch })).toBeNull();
		expect(fetch).toHaveBeenCalledTimes(1);
	});

	it('does NOT cache a transient failure — the next request tries again', async () => {
		const fetch = vi
			.fn()
			.mockResolvedValueOnce(respond(500))
			.mockRejectedValueOnce(new Error('boom'))
			.mockResolvedValueOnce(respond(200, payload));
		expect(await fetchAdminBrand({ ...opts, fetch })).toBeNull(); // 500 — fallback, uncached
		expect(await fetchAdminBrand({ ...opts, fetch })).toBeNull(); // network — same
		expect((await fetchAdminBrand({ ...opts, fetch }))?.name).toBe('Graphene Platforms');
		expect(fetch).toHaveBeenCalledTimes(3);
	});
});
