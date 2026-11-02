import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import TransactionsListing from './transactions-listing.svelte';
import type { Transaction } from './transactions.js';

const links = {
	account: (uid: string) => `/a/${uid}`,
	client: (uid: string) => `/c/${uid}`
};

const receipt: Transaction = {
	uid: 'sei:384801835',
	date: '2026-08-27',
	account_id: 1,
	account_name: 'Clark Kent',
	account_uid: 'mOUdRkgl3dSk',
	client_id: 1,
	client_uid: 'mOUdRkgl3dSk',
	client_name: 'Clark Kent',
	adviser: null,
	type: 'payment',
	subtype: 'Cash Receipt',
	method: null,
	amount: 1_000_000_000,
	currency: 'USD',
	base_amount: 735_916_400,
	base_currency: 'GBP',
	asset: null,
	quantity: null,
	status: 'settled',
	product_type: 'GIA'
};

describe('the base-currency line', () => {
	it('prints the sterling figure, not the conversion object', () => {
		const body = render(TransactionsListing, {
			props: { transactions: [receipt], links }
		}).body;
		expect(body).not.toContain('[object Object]');
		expect(body).not.toContain('conv');
		expect(body).toContain('£735,916,400');
		expect(body).toContain('$1,000,000,000');
	});
});
