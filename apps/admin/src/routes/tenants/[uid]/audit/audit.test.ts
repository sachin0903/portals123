/**
 * The per-tenant Audit loader (`/tenants/[uid]/audit`) — a firm's slice of the record-change
 * trail. Pins: the `audit:read` gate, that the firm is forced by the section's NUMERIC tenant id
 * (resolved by the layout, read via `parent()`) — never widenable from the URL — the filter/cursor
 * mapping, and the `{items}` unwrap (the endpoint returns `{items, tenants}` and the scoped view
 * ignores the firm lookup).
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const PAGE = { items: [{ id: 3 }], tenants: [{ id: 7, name: 'Acme' }] };

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function event(over: { search?: string; permissions?: string[]; fetch?: typeof globalThis.fetch }) {
	const fetch = over.fetch ?? vi.fn(async () => json(PAGE));
	return {
		params: { uid: 'ten_7' },
		url: new URL(`http://ops.test/tenants/ten_7/audit${over.search ?? ''}`),
		locals: {
			user: { id: 'ops_1' },
			accessToken: 'tok',
			permissions: over.permissions ?? ['audit:read'],
			role: null
		},
		parent: async () => ({ tenant: { id: 7, uid: 'ten_7', name: 'Acme' }, canManage: true }),
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

const run = async (e: LoadEvent) =>
	(await load(e)) as Exclude<Awaited<ReturnType<typeof load>>, void>;

describe('the per-tenant /audit loader gate', () => {
	it('bounces an unauthenticated caller to /login', async () => {
		const e = event({});
		(e as { locals: { user: unknown } }).locals.user = null;
		await expect(load(e)).rejects.toMatchObject({ status: 303, location: '/login' });
	});

	it('403s without audit:read — and never calls the API', async () => {
		const e = event({ permissions: ['tenants:read'] });
		await expect(load(e)).rejects.toMatchObject({ status: 403 });
		expect(e.fetch).not.toHaveBeenCalled();
	});
});

describe('the per-tenant /audit loader scoping', () => {
	it('forces the firm to the numeric tenant id and maps the filters', async () => {
		const e = event({
			search: '?actor=alice&action=auth.users.update&until=2026-07-05'
		});
		const result = await run(e);

		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.pathname).toBe('/admin/audit');
		expect(called.searchParams.get('tenant_id')).toBe('7');
		expect(called.searchParams.get('actor')).toBe('alice');
		expect(called.searchParams.get('action')).toBe('auth.users.update');
		expect(called.searchParams.get('until')).toBe('2026-07-05T23:59:59.999Z');
		// Load-more is client-side now (via /audit/more), so the loader no longer forwards a cursor.
		expect(called.searchParams.get('before_id')).toBeNull();

		// The rows come out of `{items}`; the firm lookup is dropped on this scoped view.
		expect(result.events).toEqual(PAGE.items);
		expect(result.tenantUid).toBe('ten_7');
		expect(result.tenantId).toBe(7);
	});
});

describe('the per-tenant /audit loader failure modes', () => {
	it('surfaces an API 403 as a banner rather than an empty log', async () => {
		const e = event({ fetch: vi.fn(async () => json({ detail: 'nope' }, 403)) });
		const result = await run(e);
		expect(result.events).toEqual([]);
		expect(result.apiError).toContain('System role');
	});
});
