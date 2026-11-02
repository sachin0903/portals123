/** Fees are view only for now: the proxy refuses every write and never reaches the API. */
import { describe, expect, it, vi } from 'vitest';
import { PUT } from './+server';

type Handler = Parameters<typeof PUT>[0];

describe('the account fee-package proxy', () => {
	it('refuses with 403 and never calls the API', async () => {
		const fetch = vi.fn(async () => new Response('{}'));
		const response = await PUT({
			locals: {
				user: { id: 'u1' },
				accessToken: 'tok',
				permissions: ['instructions:write'],
				role: 'adviser'
			},
			params: { uid: 'acc_1' },
			request: new Request('http://portal.test/accounts/acc_1/fee-package', {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ fee_packages: [] })
			}),
			fetch
		} as unknown as Handler);
		expect(response.status).toBe(403);
		expect(await response.json()).toEqual({ error: 'Fees are view only.' });
		expect(fetch).not.toHaveBeenCalled();
	});
});
