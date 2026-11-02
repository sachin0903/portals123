/**
 * The BFF routes behind the Clients and Accounts tabs. What they own is the gate (nothing reaches
 * the API for a caller who may not use it), what they forward, and passing the API's own error text
 * through: a 409 says WHICH accounts are still mapped, and the UI shows exactly that.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET as accountsTable } from './accounts-table/+server';
import { POST as accountsLink } from './accounts-link/+server';
import { GET as ioAccountsForClient } from './io-accounts-for-client/+server';
import { POST as clientsLink } from './clients-link/+server';
import { POST as unlinkAccount } from './accounts/[accountUid]/unlink/+server';
import { POST as unlinkClient } from './clients/[contactUid]/unlink/+server';
import { responseMessage } from './mapping-errors';

const user = { id: 'u-ops', email: 'ops@graphene.test' };
const READER = { user, accessToken: 'tok', permissions: ['integrations:read'], role: null };
const WRITER = {
	user,
	accessToken: 'tok',
	permissions: ['integrations:read', 'integrations:write'],
	role: null
};

const json = (body: unknown, status = 200) =>
	new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});

function harness(reply: () => Response = () => json({})) {
	const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => reply());
	/** The URL, and the body, the API was actually called with. */
	const sent = () => {
		const [url, init] = fetch.mock.calls[0];
		return { url: new URL(url), method: init?.method, body: init?.body };
	};
	return { fetch, sent };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const call = (handler: any, event: Record<string, unknown>) => handler(event);

describe('accounts-table', () => {
	const get = (query: string, locals: object = READER, fetch: unknown = undefined) =>
		call(accountsTable, {
			locals,
			params: { uid: 'int_1' },
			url: new URL(`http://admin.test/integrations/int_1/accounts-table${query}`),
			fetch
		});

	it('forwards the page, the trimmed term and the unlinked filter', async () => {
		const { fetch, sent } = harness(() => json({ items: [], total: 0 }));

		await get('?page=2&page_size=10&search=%20MSP001143%20&unlinked_only=true', READER, fetch);

		const { url } = sent();
		expect(url.pathname).toBe('/integrations/accounts-table');
		expect(url.searchParams.get('integration_uid')).toBe('int_1');
		expect(url.searchParams.get('page')).toBe('2');
		expect(url.searchParams.get('search')).toBe('MSP001143');
		expect(url.searchParams.get('unlinked_only')).toBe('true');
	});

	it.each(['', '?search=', '?search=%20%20', '?unlinked_only=false'])(
		'sends neither filter when there is none (%j)',
		async (q) => {
			const { fetch, sent } = harness(() => json({ items: [], total: 0 }));

			await get(q, READER, fetch);

			expect(sent().url.searchParams.has('search')).toBe(false);
			expect(sent().url.searchParams.has('unlinked_only')).toBe(false);
		}
	);

	it('refuses an unauthenticated caller and one without integrations:read, calling nothing', async () => {
		const { fetch } = harness();

		await expect(get('', { ...READER, user: null }, fetch)).rejects.toMatchObject({ status: 401 });
		await expect(get('', { ...READER, permissions: [] }, fetch)).rejects.toMatchObject({
			status: 403
		});
		expect(fetch).not.toHaveBeenCalled();
	});
});

describe.each([
	[
		'clients-link',
		clientsLink,
		'/integrations/clients-link',
		{ contact_uid: 'c1', client_id_io: '9' },
		{ contact_uid: 'c1', client_id_io: '9' }
	],
	[
		// The integration is the route's `[uid]`: it decides which provider's plans may be linked.
		'accounts-link',
		accountsLink,
		'/integrations/accounts-link',
		{ account_uid: 'a1', plan_id_io: '5' },
		{ account_uid: 'a1', plan_id_io: '5', integration_uid: 'int_1' }
	]
])('%s', (_name, handler, path, body, forwarded) => {
	const post = (locals: object, fetch: unknown) =>
		call(handler, {
			locals,
			params: { uid: 'int_1' },
			request: new Request('http://admin.test/x', { method: 'POST', body: JSON.stringify(body) }),
			fetch
		});

	it('forwards the body to the API, with the integration it was posted under', async () => {
		const { fetch, sent } = harness(() => json({ ok: true }));

		await post(WRITER, fetch);

		expect(sent().url.pathname).toBe(path);
		expect(sent().method).toBe('POST');
		expect(JSON.parse(String(sent().body))).toEqual(forwarded);
	});

	it('refuses a read-only caller without calling the API', async () => {
		const { fetch } = harness();

		await expect(post(READER, fetch)).rejects.toMatchObject({ status: 403 });
		await expect(post({ ...WRITER, user: null }, fetch)).rejects.toMatchObject({ status: 401 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it("passes the API's own error text and status through (a 409 names the accounts)", async () => {
		const reply = () =>
			json({ detail: 'Account mapping exists (ISA (MSP001143)). Unlink the accounts first.' }, 409);
		const { fetch } = harness(reply);

		await expect(post(WRITER, fetch)).rejects.toMatchObject({
			status: 409,
			body: { message: expect.stringContaining('MSP001143') }
		});
	});
});

describe('unlink', () => {
	const post = (locals: object, fetch: unknown, bodyText = '', accountUid = 'acc_1') =>
		call(unlinkAccount, {
			locals,
			params: { accountUid },
			request: new Request('http://admin.test/x', { method: 'POST', body: bodyText }),
			fetch
		});

	it('unlinks the one account named in the path, encoded, with an empty body defaulted', async () => {
		const { fetch, sent } = harness(() => json({ unlinked: true }));

		await post(WRITER, fetch, '', 'a/b c');

		expect(sent().url.pathname).toBe('/integrations/accounts/a%2Fb%20c/io-unlink');
		expect(sent().body).toBe('{}');
	});

	it('forwards the skip flag when one is sent', async () => {
		const { fetch, sent } = harness(() => json({ unlinked: true }));

		await post(WRITER, fetch, JSON.stringify({ skip_intelliflo: true }));

		expect(JSON.parse(String(sent().body))).toEqual({ skip_intelliflo: true });
	});

	it('refuses a caller without integrations:write, calling nothing', async () => {
		const { fetch } = harness();

		await expect(post(READER, fetch)).rejects.toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('surfaces a failure from Intelliflo, which the API says kept the link', async () => {
		const { fetch } = harness(() =>
			json({ detail: 'Intelliflo request failed: 503. The link was kept.' }, 502)
		);

		await expect(post(WRITER, fetch)).rejects.toMatchObject({
			status: 502,
			body: { message: expect.stringContaining('link was kept') }
		});
	});
});

describe('unlink client', () => {
	const post = (locals: object, fetch: unknown, contactUid = 'con_1') =>
		call(unlinkClient, {
			locals,
			params: { contactUid },
			request: new Request('http://admin.test/x', { method: 'POST' }),
			fetch
		});

	it('unlinks the one client named in the path, encoded, with an empty body', async () => {
		const { fetch, sent } = harness(() => json({ unlinked: true, client_id_io: 'io-1' }));

		const res = await post(WRITER, fetch, 'c/d e');

		expect(sent().url.pathname).toBe('/integrations/clients/c%2Fd%20e/io-unlink');
		expect(sent().method).toBe('POST');
		expect(sent().body).toBe('{}');
		expect(await res.json()).toEqual({ unlinked: true, client_id_io: 'io-1' });
	});

	it('refuses a caller without integrations:write, calling nothing', async () => {
		const { fetch } = harness();

		await expect(post(READER, fetch)).rejects.toMatchObject({ status: 403 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('refuses an unauthenticated caller, calling nothing', async () => {
		const { fetch } = harness();

		await expect(post({ ...WRITER, user: null }, fetch)).rejects.toMatchObject({ status: 401 });
		expect(fetch).not.toHaveBeenCalled();
	});

	it('passes the API refusal through, which names the accounts still mapped', async () => {
		const { fetch } = harness(() =>
			json(
				{
					detail:
						'Account mapping exists (ISA (MSP-1)). Unlink the accounts before unlinking the client.'
				},
				409
			)
		);

		await expect(post(WRITER, fetch)).rejects.toMatchObject({
			status: 409,
			body: { message: expect.stringContaining('Unlink the accounts before unlinking') }
		});
	});
});

describe('responseMessage', () => {
	it('uses the message the BFF passed through', async () => {
		const res = json({ message: 'Unlink the accounts before changing the client mapping.' }, 409);

		expect(await responseMessage(res, 'Link failed')).toBe(
			'Unlink the accounts before changing the client mapping.'
		);
	});

	it('falls back to the status when there is no usable message', async () => {
		expect(await responseMessage(new Response('<html>', { status: 500 }), 'Link failed')).toBe(
			'Link failed (500).'
		);
		expect(await responseMessage(json({ message: '  ' }, 400), 'Link failed')).toBe(
			'Link failed (400).'
		);
	});
});

describe('io-accounts-for-client', () => {
	const get = (locals: object, fetch: unknown, search = '?client_id_io=77') =>
		call(ioAccountsForClient, {
			locals,
			params: { uid: 'int_1' },
			url: new URL(`http://admin.test/x${search}`),
			fetch
		});

	it("asks for the plans under this integration's provider, by the route's integration", async () => {
		const { fetch, sent } = harness(() => json([]));

		await get(READER, fetch);

		expect(sent().url.pathname).toBe('/integrations/io-clients/77/accounts');
		expect(sent().url.searchParams.get('integration_uid')).toBe('int_1');
	});

	it('refuses a caller who may not read integrations without calling the API', async () => {
		const { fetch } = harness();

		await expect(get({ ...READER, permissions: [] }, fetch)).rejects.toMatchObject({
			status: 403
		});
		expect(fetch).not.toHaveBeenCalled();
	});
});
