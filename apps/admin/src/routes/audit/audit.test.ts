/**
 * The global Audit loader (`/audit`) — the cross-firm record-change trail. Pins: the `audit:read`
 * gate, the firm-lookup + rows unwrap from the endpoint's new `{items, tenants}` shape (so the
 * firm filter feeds itself under audit:read, without tenants:read), the `tenant_id` pass-through,
 * and that an old action-stream URL still redirects to the /events page.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';

type LoadEvent = Parameters<typeof load>[0];

const PAGE = {
	items: [{ id: 5 }],
	tenants: [
		{ id: 7, name: 'Acme' },
		{ id: 8, name: 'Beta' }
	]
};

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function event(over: { search?: string; permissions?: string[]; fetch?: typeof globalThis.fetch }) {
	const fetch = over.fetch ?? vi.fn(async () => json(PAGE));
	return {
		url: new URL(`http://ops.test/audit${over.search ?? ''}`),
		locals: {
			user: { id: 'ops_1' },
			accessToken: 'tok',
			permissions: over.permissions ?? ['audit:read'],
			role: null
		},
		fetch
	} as unknown as LoadEvent & { fetch: ReturnType<typeof vi.fn> };
}

const run = async (e: LoadEvent) =>
	(await load(e)) as Exclude<Awaited<ReturnType<typeof load>>, void>;

describe('the global /audit loader', () => {
	it('403s without audit:read', async () => {
		const e = event({ permissions: ['tenants:read'] });
		await expect(load(e)).rejects.toMatchObject({ status: 403 });
	});

	it('unwraps rows + the firm lookup from {items, tenants} and passes tenant_id through', async () => {
		const e = event({ search: '?tenant_id=7&actor=alice' });
		const result = await run(e);

		const called = new URL(String(e.fetch.mock.calls[0][0]));
		expect(called.pathname).toBe('/admin/audit');
		expect(called.searchParams.get('tenant_id')).toBe('7');
		expect(called.searchParams.get('actor')).toBe('alice');

		expect(result.events).toEqual(PAGE.items);
		expect(result.tenants).toEqual(PAGE.tenants);
		expect(result.filters.tenantId).toEqual(['7']);
	});

	it('redirects an old action-stream URL (event_type=…) to /activity', async () => {
		const e = event({ search: '?event_type=action.firm-&actor=bob' });
		await expect(load(e)).rejects.toMatchObject({
			status: 308,
			location: expect.stringContaining('/activity')
		});
		// The redirect fires before any API call.
		expect(e.fetch).not.toHaveBeenCalled();
	});
});
