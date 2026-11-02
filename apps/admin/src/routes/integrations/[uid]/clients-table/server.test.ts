/**
 * The Clients and Accounts tab's listing endpoint: the BFF between the browser and
 * `GET /integrations/clients-table`.
 *
 * What it owns is the gate (an unauthenticated or non-`integrations:read` caller never reaches the
 * API) and what it forwards. The search term is forwarded trimmed, and OMITTED when blank: the API
 * reads an absent term as "the whole book", so a bare `search=` would be a second spelling of it.
 */
import { describe, expect, it, vi } from 'vitest';
import { GET } from './+server';

const READER = {
	user: { id: 'u-ops', email: 'ops@graphene.test' },
	accessToken: 'tok',
	permissions: ['integrations:read'],
	role: null as string | null
};

const page = { items: [], total: 0 };

function harness() {
	const fetch = vi.fn(
		async () =>
			new Response(JSON.stringify(page), {
				status: 200,
				headers: { 'content-type': 'application/json' }
			})
	);
	const call = (query: string, locals: object = READER) =>
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		(GET as any)({
			locals,
			params: { uid: 'int_1' },
			url: new URL(`http://admin.test/integrations/int_1/clients-table${query}`),
			fetch
		});
	/** The query string the API was actually called with. */
	const sent = () => new URL(String((fetch.mock.calls[0] as unknown[])[0])).searchParams;
	return { fetch, call, sent };
}

describe('clients-table BFF', () => {
	it('forwards the search term, trimmed, with the page and the integration', async () => {
		const { call, sent } = harness();

		await call('?page=2&page_size=10&search=%20Halloway%20');

		expect(sent().get('search')).toBe('Halloway');
		expect(sent().get('page')).toBe('2');
		expect(sent().get('page_size')).toBe('10');
		expect(sent().get('integration_uid')).toBe('int_1');
	});

	it('forwards a term containing characters that need encoding intact', async () => {
		const { call, sent } = harness();

		await call(`?search=${encodeURIComponent('100% & O’Brien')}`);

		expect(sent().get('search')).toBe('100% & O’Brien');
	});

	it.each(['', '?search=', '?search=%20%20'])(
		'omits search entirely when blank (%j)',
		async (q) => {
			const { call, sent } = harness();

			await call(q);

			expect(sent().has('search')).toBe(false);
		}
	);

	it('refuses an unauthenticated caller without calling the API', async () => {
		const { call, fetch } = harness();

		await expect(call('?search=x', { ...READER, user: null })).rejects.toMatchObject({
			status: 401
		});
		expect(fetch).not.toHaveBeenCalled();
	});

	it('refuses a caller without integrations:read without calling the API', async () => {
		const { call, fetch } = harness();

		await expect(call('?search=x', { ...READER, permissions: [] })).rejects.toMatchObject({
			status: 403
		});
		expect(fetch).not.toHaveBeenCalled();
	});
});
