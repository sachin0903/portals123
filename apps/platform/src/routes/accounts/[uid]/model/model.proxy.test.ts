/**
 * The account model proxy writes the mandate on this account.
 */
import { describe, expect, it, vi } from 'vitest';
import { PUT } from './+server';

type Handler = Parameters<typeof PUT>[0];

async function putFor(permissions: string[], body: object = { model_id: 3 }) {
	const fetch = vi.fn(async () => new Response(JSON.stringify({ model_id: 3 })));
	const response = await PUT({
		locals: {
			user: { id: 'u1' },
			accessToken: 'tok',
			permissions,
			role: 'adviser'
		},
		params: { uid: 'acc_1' },
		request: new Request('http://portal.test/accounts/acc_1/model', {
			method: 'PUT',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(body)
		}),
		fetch
	} as unknown as Handler);
	return { response, fetch };
}

describe('the account model proxy', () => {
	it('writes this account’s mandate', async () => {
		const { response, fetch } = await putFor(['instructions:write']);
		expect(response.status).toBe(200);
		expect(fetch).toHaveBeenCalledTimes(1);
		const calls = fetch.mock.calls as unknown as Array<[string, RequestInit]>;
		const [url, init] = calls[0];
		expect(url).toContain('/crm/accounts/acc_1/model');
		expect(init.method).toBe('PUT');
		expect(JSON.parse(String(init.body))).toEqual({ model_id: 3 });
	});

	it('refuses without instructions:write, without calling the API', async () => {
		const { response, fetch } = await putFor(['contacts:write']);
		expect(response.status).toBe(403);
		expect(fetch).not.toHaveBeenCalled();
	});
});
