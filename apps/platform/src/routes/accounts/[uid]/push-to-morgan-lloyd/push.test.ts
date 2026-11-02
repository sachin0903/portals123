import { describe, expect, it } from 'vitest';
import { POST } from './+server';

const event = (permissions: string[]) =>
	({
		locals: {
			user: { id: 'u_1' },
			accessToken: 'tok',
			permissions,
			role: 'adviser'
		},
		params: { uid: 'acc_sipp' },
		fetch: async () => {
			throw new Error('the proxy must not reach the API without contacts:sync_morgan_lloyd');
		}
	}) as unknown as Parameters<typeof POST>[0];

describe('POST /accounts/[uid]/push-to-morgan-lloyd', () => {
	it('refuses contacts:write — yesterday’s key no longer turns the proxy', async () => {
		const res = await POST(event(['contacts:write']));
		expect(res.status).toBe(403);
	});

	it('refuses instructions:write — minting a scheme is not money movement', async () => {
		const res = await POST(event(['instructions:write']));
		expect(res.status).toBe(403);
	});

	it('forwards when the caller holds contacts:sync_morgan_lloyd', async () => {
		const requested: string[] = [];
		const res = await POST({
			...event(['contacts:sync_morgan_lloyd']),
			fetch: (async (url: string | URL) => {
				requested.push(String(url));
				return new Response(JSON.stringify({ contact: { status: 'skipped' }, accounts: [] }), {
					status: 200,
					headers: { 'content-type': 'application/json' }
				});
			}) as never
		});
		expect(res.status).toBe(200);
		expect(requested.some((u) => u.includes('/crm/accounts/acc_sipp/sync-to-morgan-lloyd'))).toBe(
			true
		);
	});
});
