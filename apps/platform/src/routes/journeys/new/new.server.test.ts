/**
 * `/journeys/new` — first paint is flows + the product/custodian shelf. The rest of the wizard
 * catalogues load when that step mounts.
 */
import { describe, expect, it, vi } from 'vitest';
import { load } from './+page.server';
import { GET as loadApplicants } from './applicants/+server';
import { GET as loadAccounts } from './accounts/+server';
import { GET as loadSippTotal } from './sipp-total/+server';

const json = (data: unknown, status = 200): Response =>
	new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });

function harness(handler: (url: string) => Response) {
	const calls: string[] = [];
	const fetch = vi.fn(async (input: unknown) => {
		const url = String(input);
		calls.push(url);
		return handler(url);
	});
	return { fetch, calls };
}

const staff = {
	user: { id: 'u1', email: 'Ops@Firm.test' },
	permissions: ['cases:write'],
	role: null,
	accessToken: 't'
};

describe('/journeys/new load', () => {
	it('fetches published flows and the product shelf, not the rest of the wizard', async () => {
		const { fetch, calls } = harness((url) => {
			if (url.includes('/flows')) return json([{ uid: 'flw_1', code: 'onb', status: 'published' }]);
			if (url.includes('/catalog/')) return json([]);
			return json([]);
		});
		const loaded = (await load({
			locals: staff,
			fetch,
			url: new URL('http://portal.test/journeys/new?flow=onb')
		} as never)) as {
			wizard: Promise<{ selectedFlowCode: string; prefillClientUid: string | null }>;
		};
		const data = await loaded.wizard;
		expect(data.selectedFlowCode).toBe('onb');
		expect(data.prefillClientUid).toBeNull();
		expect(calls.some((url) => url.includes('/flows?status=published'))).toBe(true);
		expect(calls.some((url) => url.includes('/catalog/custodians'))).toBe(true);
		expect(calls.some((url) => url.includes('/catalog/products'))).toBe(true);
		expect(calls.some((url) => url.includes('/documents/clients'))).toBe(false);
		expect(calls.some((url) => url.includes('/crm/illustrations'))).toBe(false);
		expect(calls.some((url) => url.includes('/crm/models'))).toBe(false);
		expect(calls.some((url) => url.includes('/ref/idv'))).toBe(false);
		expect(calls.some((url) => url.includes('/firm/members'))).toBe(false);
	});

	it('carries the client the start came from, so the wizard can fill the first card', async () => {
		const { fetch } = harness((url) => {
			if (url.includes('/flows')) return json([{ uid: 'flw_1', code: 'onb', status: 'published' }]);
			return json([]);
		});
		const loaded = (await load({
			locals: staff,
			fetch,
			url: new URL('http://portal.test/journeys/new?flow=onb&client=y8Det_wwskoC')
		} as never)) as { wizard: Promise<{ prefillClientUid: string | null }> };
		expect((await loaded.wizard).prefillClientUid).toBe('y8Det_wwskoC');
	});
});

describe('/journeys/new/applicants', () => {
	it('loads clients, advisers and IDV together', async () => {
		const { fetch, calls } = harness((url) => {
			if (url.includes('/documents/clients')) return json([{ contact_id: 1, uid: 'c1' }]);
			if (url.includes('/firm/members')) return json([{ user_id: 'u', is_adviser: true }]);
			if (url.includes('/ref/idv')) return json({ available: true, manual_documents: [] });
			return json([]);
		});
		const res = await loadApplicants({
			locals: staff,
			fetch
		} as never);
		const body = await res.json();
		expect(body.clients).toEqual([{ contact_id: 1, uid: 'c1' }]);
		expect(body.advisers).toEqual([{ user_id: 'u', is_adviser: true }]);
		expect(body.signedInIsAdviser).toBe(false);
		expect(body.digitalIdvAvailable).toBe(true);
		expect(calls.some((url) => url.includes('/documents/clients'))).toBe(true);
		expect(calls.some((url) => url.includes('/ref/idv'))).toBe(true);
	});

	it('tells the wizard when the signed-in person is one of the advisers', async () => {
		// The blank picker's last fallback is the signed-in user, and only when they are marked
		// an adviser; the members list carries the email, matched without regard to case.
		const { fetch } = harness((url) => {
			if (url.includes('/firm/members')) {
				return json([
					{ user_id: 'u', email: 'ops@firm.test', is_adviser: true },
					{ user_id: 'v', email: 'other@firm.test', is_adviser: true }
				]);
			}
			if (url.includes('/ref/idv')) return json({ available: false });
			return json([]);
		});
		const res = await loadApplicants({ locals: staff, fetch } as never);
		expect((await res.json()).signedInIsAdviser).toBe(true);
	});

	it('does not count a signed-in administrator who is not marked an adviser', async () => {
		const { fetch } = harness((url) => {
			if (url.includes('/firm/members')) {
				return json([
					{ user_id: 'u', email: 'ops@firm.test', is_adviser: false },
					{ user_id: 'v', email: 'other@firm.test', is_adviser: true }
				]);
			}
			if (url.includes('/ref/idv')) return json({ available: false });
			return json([]);
		});
		const res = await loadApplicants({ locals: staff, fetch } as never);
		expect((await res.json()).signedInIsAdviser).toBe(false);
	});

	it('refuses without cases:write', async () => {
		const { fetch, calls } = harness(() => json([]));
		const res = await loadApplicants({
			locals: { ...staff, permissions: ['cases:read'] },
			fetch
		} as never);
		expect(res.status).toBe(403);
		expect(calls).toEqual([]);
	});
});

describe('/journeys/new/accounts', () => {
	it('loads illustrations and models', async () => {
		const { fetch, calls } = harness((url) => {
			if (url.includes('/crm/illustrations')) return json([{ uid: 'ill_1' }]);
			if (url.includes('/crm/models')) return json([{ id: 1, name: 'Growth' }]);
			return json([]);
		});
		const res = await loadAccounts({ locals: staff, fetch } as never);
		const body = await res.json();
		expect(body.illustrations).toEqual([{ uid: 'ill_1' }]);
		expect(body.models).toEqual([{ id: 1, name: 'Growth' }]);
		expect(calls.some((url) => url.includes('/crm/illustrations/onboarding'))).toBe(true);
		expect(calls.some((url) => url.includes('/crm/models'))).toBe(true);
	});
});

describe('/journeys/new/sipp-total', () => {
	it('sums the contact shell’s SIPP accounts and ignores the rest', async () => {
		const { fetch, calls } = harness((url) => {
			if (url.includes('/crm/contacts/con_1/shell')) {
				return json({
					contact: { id: 1, uid: 'con_1' },
					accounts: [
						{ product_type: 'SIPP', value: 10000 },
						{ product_type: 'ISA', value: 5000 }
					]
				});
			}
			return json({});
		});
		const res = await loadSippTotal({
			locals: staff,
			fetch,
			url: new URL('http://portal.test/journeys/new/sipp-total?uid=con_1')
		} as never);
		expect(await res.json()).toEqual({ total: 10000 });
		expect(calls.some((url) => url.includes('/crm/contacts/con_1/shell'))).toBe(true);
	});

	it('hides the line when the client has no SIPP', async () => {
		const { fetch } = harness(() =>
			json({ contact: { id: 1, uid: 'con_1' }, accounts: [{ product_type: 'GIA', value: 1 }] })
		);
		const res = await loadSippTotal({
			locals: staff,
			fetch,
			url: new URL('http://portal.test/journeys/new/sipp-total?uid=con_1')
		} as never);
		expect(await res.json()).toEqual({ total: null });
	});

	it('does not report a failed read as a client with no SIPP', async () => {
		const { fetch } = harness(() => json({ error: 'no' }, 503));
		const res = await loadSippTotal({
			locals: staff,
			fetch,
			url: new URL('http://portal.test/journeys/new/sipp-total?uid=con_1')
		} as never);
		expect(res.status).toBe(502);
		expect(await res.json()).not.toEqual({ total: null });
	});

	it('refuses without cases:write', async () => {
		const { fetch, calls } = harness(() => json({}));
		const res = await loadSippTotal({
			locals: { ...staff, permissions: ['cases:read'] },
			fetch,
			url: new URL('http://portal.test/journeys/new/sipp-total?uid=con_1')
		} as never);
		expect(res.status).toBe(403);
		expect(calls).toEqual([]);
	});
});
