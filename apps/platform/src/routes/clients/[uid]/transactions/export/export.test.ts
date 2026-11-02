import { describe, expect, it } from 'vitest';
import { GET } from './+server';

const TXN = {
	uid: 'seccl:TX-1',
	date: '2026-03-12',
	account_id: 7,
	account_name: 'Caldwell SIPP',
	account_uid: 'seccl:ACC-7',
	client_id: 42,
	client_name: 'Theo Caldwell',
	adviser: 'Zoe Lead',
	type: 'deposit',
	subtype: 'Cash receipt',
	method: 'bank_transfer',
	amount: 2500,
	asset: null,
	quantity: null,
	status: 'settled',
	product_type: 'SIPP'
};

/** `/crm/contacts/` resolves the uid; everything else is the ledger read, with its request URL
 *  there to inspect. */
function fetchFor(ledger: unknown) {
	const seen = { ledgerUrl: '' };
	const fetch = async (input: string | URL | Request): Promise<Response> => {
		const href = String(input);
		const json = (b: unknown) =>
			new Response(JSON.stringify(b), { headers: { 'Content-Type': 'application/json' } });
		if (href.includes('/crm/contacts/')) return json({ contact: { id: 42 } });
		if (href.endsWith('/exports/record')) return new Response(null, { status: 204 });
		seen.ledgerUrl = href;
		return json(ledger);
	};
	return { fetch, seen };
}

const TXN_PAGE = { items: [TXN], total: 1, facets: {} };

const event = (href: string, fetch: typeof globalThis.fetch) =>
	({
		locals: {
			user: { id: 'user_1' },
			accessToken: 'token',
			permissions: ['listings:export'],
			role: null
		},
		params: { uid: 'con_9b2e' },
		url: new URL(href),
		fetch
	}) as unknown as Parameters<typeof GET>[0];

describe('client transaction CSV export', () => {
	it('exports all matching rows and preserves the active date range', async () => {
		const { fetch, seen } = fetchFor(TXN_PAGE);
		const response = await GET(
			event(
				'http://test/clients/con_9b2e/transactions/export?date_from=2026-01-01&date_to=2026-06-30',
				fetch
			)
		);

		const body = await response.text();
		expect(seen.ledgerUrl).toContain('contact_id=42');
		expect(seen.ledgerUrl).toContain('date_from=2026-01-01');
		expect(seen.ledgerUrl).toContain('date_to=2026-06-30');
		expect(response.headers.get('Content-Type')).toContain('text/csv');
		// Date-stamped now, like every other export: two files in a downloads folder called
		// `client-42-transactions.csv` are indistinguishable.
		expect(response.headers.get('Content-Disposition')).toMatch(
			/client-con_9b2e-transactions-\d{4}-\d{2}-\d{2}\.csv/
		);
		// Quoted only where a value needs it, rather than every cell. This route used to carry its
		// own writer that quoted everything and had NO formula guard; it is on the shared one now.
		expect(body).toContain('Date,Reference,Account');
		expect(body).toContain('2026-03-12,TX-1,Caldwell SIPP,seccl:ACC-7');
		// The provider prefix is stripped from the reference, as the table strips it.
		expect(body).not.toContain('seccl:TX-1');
		expect(body).toContain('Deposit: Cash receipt');
	});

	it('sends the PDF the table’s own columns, in order', async () => {
		const { fetch } = fetchFor(TXN_PAGE);
		const response = await GET(
			event('http://test/clients/con_9b2e/transactions/export?format=pdf', fetch)
		);
		const table = await response.json();
		// No Client column (every row is this client's own) and no Adviser — this route hides that
		// filter — with Account reference and Method left out as the table leaves them out.
		expect(table.headers).toEqual([
			'Date',
			'Reference',
			'Account',
			'Type',
			'Asset',
			'Units',
			'ISIN',
			'Currency',
			'Amount',
			'Status'
		]);
	});

	it('reaches the API with the table’s default sort when the URL names none', async () => {
		const { fetch, seen } = fetchFor({ items: [], total: 0, facets: {} });
		await GET(event('http://test/clients/con_9b2e/transactions/export', fetch));
		expect(seen.ledgerUrl).toContain('sort=date');
		expect(seen.ledgerUrl).toContain('dir=desc');
	});
});
