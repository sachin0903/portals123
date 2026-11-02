import { describe, expect, it } from 'vitest';
import { GET } from './+server';

/** One row as `GET /crm/holdings` sends it — the lake's own field names, not the UI `Holding`. */
const RAW = {
	asset_name: 'Royal London Short Term Money Market Y Inc',
	isin: 'GB00B3P2RZ52',
	sedol: '0123456',
	currency: 'GBP',
	quantity: 1111.09,
	price: 1.23,
	value: 1366.64,
	book_cost: 1000,
	growth: 366.64,
	as_of: '2026-08-20',
	account_name: 'Oakden SIPP'
};

/** Answers the uid resolve (`accountIdFrom`) and the holdings read. */
function fetcher(payload: unknown) {
	return async (input: string | URL | Request) => {
		const url = String(input);
		if (url.includes('/crm/accounts/')) return Response.json({ id: 12 });
		return Response.json(payload);
	};
}

async function run(href: string, payload: unknown = [RAW]) {
	const response = await GET({
		locals: {
			user: { id: 'u1' },
			accessToken: 'tok',
			permissions: ['listings:export'],
			role: null
		},
		params: { uid: 'acc_12ab34cd' },
		url: new URL(href),
		fetch: fetcher(payload)
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
	} as any);
	return { response, text: await response.text() };
}

describe('account holdings export', () => {
	it('writes the TABLE’s columns, in its order, with Allocation added', async () => {
		const { text } = await run('http://test/accounts/acc_12ab34cd/holdings/export');
		const [header, row] = text.replace('﻿', '').trim().split('\r\n');
		expect(header).toBe(
			'Investment,Account,ISIN,SEDOL,Units,Currency,Price,Book cost,Value,Allocation,Unrealised gain,Gain %,As at'
		);
		expect(row).toBe(
			'Royal London Short Term Money Market Y Inc,Oakden SIPP,GB00B3P2RZ52,0123456,1111.09,GBP,1.23,1000,1366.64,100,366.64,36.66,2026-08-20'
		);
	});

	it('sends the PDF only the columns the table shows, as the table’s own cells read', async () => {
		const { text } = await run('http://test/accounts/acc_12ab34cd/holdings/export?format=pdf');
		const table = JSON.parse(text);
		// No Account — this account's own band hides it (`showWrapper={false}`); no Gain % or As at.
		expect(table.headers).toEqual([
			'Investment',
			'ISIN',
			'SEDOL',
			'Units',
			'Currency',
			'Price',
			'Book cost',
			'Value',
			'Allocation',
			'Unrealised gain'
		]);
		expect(table.rows[0]).toEqual([
			'Royal London Short Term Money Market Y Inc',
			'GB00B3P2RZ52',
			'0123456',
			'1,111.09',
			'GBP',
			'£1.23',
			'£1,000.00',
			'£1,366.64',
			'100.0%',
			'+£366.64 · +36.7%'
		]);
	});

	it('orders rows as the table sorts them — its own default, or the one the band is bound to', async () => {
		const small = { ...RAW, asset_name: 'Alpha Fund', isin: 'GB1', value: 500, book_cost: 400 };
		const big = { ...RAW, asset_name: 'Beta Fund', isin: 'GB2', value: 2000, book_cost: 1800 };
		// No `sort` param ⇒ `holdings-table.svelte`'s own default: value, descending.
		const { text: byValue } = await run('http://test/accounts/acc_12ab34cd/holdings/export', [
			small,
			big
		]);
		expect(byValue.split('\r\n')[1]).toContain('Beta Fund');
		// The band's CURRENT sort, carried on the export link (`account-detail.svelte`): name, ascending.
		const { text: byName } = await run(
			'http://test/accounts/acc_12ab34cd/holdings/export?sort=name&dir=asc',
			[small, big]
		);
		expect(byName.split('\r\n')[1]).toContain('Alpha Fund');
	});

	it('keeps only the rows the band’s filters show, each still a share of the whole account', async () => {
		const usd = { ...RAW, asset_name: 'Alpha Fund', isin: 'GB1', currency: 'USD', value: 500 };
		const gbp = { ...RAW, asset_name: 'Beta Fund', isin: 'GB2', currency: 'GBP', value: 1500 };
		const { text } = await run(
			'http://test/accounts/acc_12ab34cd/holdings/export?q=fund&currency=USD',
			[usd, gbp]
		);
		const [, ...rows] = text.replace('﻿', '').trim().split('\r\n');
		expect(rows).toHaveLength(1);
		expect(rows[0]).toContain('Alpha Fund');
		// 500 of the account's 2,000, not 100% of the one row left.
		expect(rows[0]).toContain(',25,');
	});

	it('dates each row with its OWN as-at, not one date for the file', async () => {
		const { text } = await run('http://test/accounts/acc_12ab34cd/holdings/export', [
			{ ...RAW, as_of: '2026-08-01' },
			{ ...RAW, as_of: '2026-08-20' }
		]);
		const [, first, second] = text.replace('﻿', '').trim().split('\r\n');
		expect(first.endsWith('2026-08-01')).toBe(true);
		expect(second.endsWith('2026-08-20')).toBe(true);
	});

	it('is 200 with no rows rather than an error, and drops Allocation (nothing to be a share of)', async () => {
		const { response, text } = await run('http://test/accounts/acc_12ab34cd/holdings/export', []);
		expect(response.status).toBe(200);
		expect(text).toContain('Investment');
		expect(text).not.toContain('Allocation');
	});
});
