import { describe, expect, it } from 'vitest';
import { GET } from './+server';

const TXN = {
	uid: 'seccl:TX-9',
	date: '2026-08-20',
	account_id: 12,
	account_name: 'Theo Caldwell ISA',
	account_uid: 'seccl:AXFF45915',
	client_id: 1,
	client_name: 'Theo Caldwell',
	adviser: 'Craig Currie',
	type: 'dividend',
	subtype: 'Dividend',
	method: null,
	amount: 71.42,
	asset: 'Royal London Short Term Money Market Y Inc',
	isin: 'GB00B3P2RZ52',
	quantity: null,
	currency: 'GBP',
	status: 'completed',
	product_type: 'ISA'
};

function event(href: string, fetch: typeof globalThis.fetch) {
	return {
		locals: {
			user: { id: 'u1' },
			accessToken: 'tok',
			permissions: ['listings:export'],
			role: null
		},
		url: new URL(href),
		fetch
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
	} as any;
}

describe('transactions export', () => {
	it('sends the PDF the table’s own columns, in order', async () => {
		// The firm ledger shows BOTH Client and Account — the one mount that does — and none of the
		// spreadsheet-only extras (Adviser, Account reference, Method).
		const response = await GET(
			event(
				'http://test/transactions/export?format=pdf',
				async () =>
					new Response(JSON.stringify({ items: [TXN], total: 1, facets: {} }), {
						headers: { 'Content-Type': 'application/json' }
					})
			)
		);
		const table = await response.json();
		expect(table.headers).toEqual([
			'Date',
			'Reference',
			'Client',
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
		let requested = '';
		await GET(
			event('http://test/transactions/export', async (input) => {
				if (String(input).endsWith('/exports/record')) return new Response(null, { status: 204 });
				requested = String(input);
				return new Response(JSON.stringify({ items: [], total: 0, facets: {} }), {
					headers: { 'Content-Type': 'application/json' }
				});
			})
		);
		expect(requested).toContain('sort=date');
		expect(requested).toContain('dir=desc');
	});
});
