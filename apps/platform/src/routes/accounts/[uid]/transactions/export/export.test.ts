import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { GET } from './+server';

const TXN = {
	uid: 'seccl:0006D1JFB',
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

async function run(href: string, body: unknown = { items: [TXN], total: 1, facets: {} }) {
	const requested: string[] = [];
	const response = await GET({
		locals: {
			user: { id: 'u1' },
			accessToken: 'tok',
			permissions: ['listings:export'],
			role: null
		},
		params: { uid: 'acc_12ab34cd' },
		url: new URL(href),
		fetch: async (input: string | URL | Request) => {
			requested.push(String(input));
			// TWO calls now: the route is uid-addressed and `?account_id=` is a serial-keyed query
			// filter, so a standalone `+server.ts` resolves the uid first (`accountIdFrom` →
			// `GET /crm/accounts/{uid}`). Answering every request with the ledger payload made the
			// resolve read the rows and throw "Record resolved without an id".
			const json = (b: unknown) =>
				new Response(JSON.stringify(b), { headers: { 'Content-Type': 'application/json' } });
			const url = String(input);
			if (url.includes('/crm/accounts/')) return json({ id: 12 });
			return json(body);
		}
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
	} as any);
	return {
		response,
		requested,
		// The LEDGER calls, with the uid resolve filtered out. The assertions below are about what
		// narrowing was forwarded to `/crm/transactions`, and `requested[0]` is the resolve now.
		listing: requested.filter((u) => u.includes('/crm/transactions')),
		text: await response.text()
	};
}

describe('account transactions export', () => {
	it('writes the columns the LEDGER shows, in its order', async () => {
		// This drifted: the ledger gained Units, ISIN and Currency and the export did not, so the
		// file still carried Method — a column the table has never shown. It read as wrong data
		// rather than as a stale column list.
		const { text } = await run('http://test/accounts/acc_12ab34cd/transactions/export');
		const [header, row] = text.replace('﻿', '').trim().split('\r\n');
		expect(header).toBe('Date,Reference,Type,Asset,Units,ISIN,Currency,Amount,Status');
		// No Balance: a running total of unsigned amounts is not a balance, so the column is gone
		// from the table and must not survive in the file.
		expect(header).not.toContain('Balance');
		expect(header).not.toContain('Method');
		expect(row).toContain('GB00B3P2RZ52');
		expect(row).toContain('GBP');
		// The provider prefix is stripped, as the table strips it.
		expect(row).toContain('0006D1JFB');
		expect(row).not.toContain('seccl:0006D1JFB');
	});

	it('is the ledger’s own column set, not a second list', () => {
		// A source scan pairing the two: the component's headers and this route's must stay together,
		// and nothing else can see that they have parted.
		const listing = readFileSync(
			join(
				import.meta.dirname,
				'..',
				'..',
				'..',
				'..',
				'..',
				'..',
				'..',
				'..',
				'pkgs',
				'ui',
				'src',
				'lib',
				'components',
				'book',
				'accounts',
				'transactions-listing.svelte'
			),
			'utf8'
		);
		const route = readFileSync(join(import.meta.dirname, '+server.ts'), 'utf8');
		// Every header the export writes must exist as a column header in the listing.
		const exported = /headers: \[([\s\S]*?)\]/.exec(route)![1];
		for (const header of [...exported.matchAll(/'([^']+)'/g)].map((m) => m[1])) {
			// The export and the ledger use the same words now — 'Ccy' was the one abbreviation and
			// it went, because a column header is read once and guessed at for ever.
			const onScreen = header;
			expect(listing, `export writes '${header}' — the ledger has no such column`).toContain(
				`header: '${onScreen}'`
			);
		}
	});

	it('states the filters it was narrowed to', async () => {
		// A 64-row file out of a 583-row ledger reads as missing data unless it says it was filtered.
		const { text } = await run(
			'http://test/accounts/acc_12ab34cd/transactions/export?date_from=2025-12-30&type=fee'
		);
		expect(text).toBeTruthy();
		const { listing } = await run(
			'http://test/accounts/acc_12ab34cd/transactions/export?date_from=2025-12-30'
		);
		expect(listing[0]).toContain('date_from=2025-12-30');
	});

	it('carries the holding DRILL-DOWN, not just the listing filters', async () => {
		// Clicking a position on the holdings band lands on `?isin=`, and the band then shows that
		// one security under a banner saying so. The drill-down is not a listing filter, so it is not
		// in `_TRANSACTION_FILTERS`, and the export ignored it: the file answered a different
		// question from the screen it was taken from, and looked complete doing it.
		const { listing } = await run(
			'http://test/accounts/acc_12ab34cd/transactions/export?isin=GB00B3P2RZ52'
		);
		expect(listing[0]).toContain('isin=GB00B3P2RZ52');
	});

	it('falls back to a search on the asset name, as the page loader does', async () => {
		// An instrument with no ISIN is linked by name instead, and the loader answers it with `q`.
		const { listing } = await run(
			'http://test/accounts/acc_12ab34cd/transactions/export?asset=Royal%20London'
		);
		expect(listing[0]).toContain('q=Royal+London');
		expect(listing[0]).not.toContain('isin=');
	});

	it('reaches the API with the ledger’s default sort when the URL names none', async () => {
		const { listing } = await run('http://test/accounts/acc_12ab34cd/transactions/export');
		expect(listing[0]).toContain('sort=date');
		expect(listing[0]).toContain('dir=desc');
	});

	it('sends the PDF the ledger’s own columns, in order', async () => {
		const { text } = await run('http://test/accounts/acc_12ab34cd/transactions/export?format=pdf');
		const table = JSON.parse(text);
		expect(table.headers).toEqual([
			'Date',
			'Reference',
			'Type',
			'Asset',
			'Units',
			'ISIN',
			'Currency',
			'Amount',
			'Status'
		]);
	});
});
