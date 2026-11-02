/**
 * The account bank-details proxy writes the ACCOUNT's own list, not the client's.
 *
 * THE DEFECT. Resolution is account first, client second (`app/bank_accounts.py`), and
 * `PUT /crm/accounts/{uid}/bank-accounts` is the writer. This hop used to GET the account only
 * to learn the client's uid, then PUT `/crm/contacts/{uid}/bank-account` — so a save from the
 * account card updated the person, GET still returned no `bank_accounts`, and the card kept
 * saying "This account uses the client's bank details."
 */
import { describe, expect, it, vi } from 'vitest';
import { PUT } from './+server';

type Handler = Parameters<typeof PUT>[0];

const BODY = {
	bank_accounts: [{ name: 'Oakden SIPP', sort_code: '11-11-11', account_number: '11111111' }]
};

async function putFor(permissions: string[]) {
	const fetch = vi.fn(
		async () => new Response(JSON.stringify({ bank_accounts: BODY.bank_accounts }))
	);
	const response = await PUT({
		locals: {
			user: { id: 'u1' },
			accessToken: 'tok',
			permissions,
			role: 'adviser'
		},
		params: { uid: 'acc_1' },
		request: new Request('http://portal.test/accounts/acc_1/bank', {
			method: 'PUT',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(BODY)
		}),
		fetch
	} as unknown as Handler);
	return { response, fetch };
}

describe('the account bank-details proxy', () => {
	it('writes this account’s bank accounts, not the client’s', async () => {
		const { response, fetch } = await putFor(['instructions:write']);
		expect(response.status).toBe(200);
		expect(fetch).toHaveBeenCalledTimes(1);
		const calls = fetch.mock.calls as unknown as Array<[string, RequestInit]>;
		const [url, init] = calls[0];
		expect(url).toContain('/crm/accounts/acc_1/bank-accounts');
		expect(url).not.toContain('/contacts/');
		expect(init.method).toBe('PUT');
		expect(JSON.parse(String(init.body))).toEqual(BODY);
	});

	it('refuses without instructions:write, without calling the API', async () => {
		const { response, fetch } = await putFor(['contacts:write']);
		expect(response.status).toBe(403);
		expect(fetch).not.toHaveBeenCalled();
	});
});
