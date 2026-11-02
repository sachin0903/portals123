import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { Transaction } from '@graphene/ui';
import ActionRequired from './action-required.svelte';

const transaction = (index: number): Transaction => ({
	uid: `txn_${index}`,
	date: `2026-08-${String(index + 1).padStart(2, '0')}`,
	account_id: index,
	account_name: `SIPP ${index}`,
	account_uid: `acc_${index}`,
	client_id: 42,
	client_name: 'Theo Caldwell',
	adviser: null,
	type: 'payment',
	subtype: 'Cash receipt',
	method: 'bank_transfer',
	amount: 1_000 + index,
	asset: null,
	quantity: null,
	status: 'settled',
	product_type: 'SIPP',
	needs_categorisation: true
});

describe('ActionRequired', () => {
	it('collapses a large queue, previews a handful of rows, and deep-links each row to its work', () => {
		const body = render(ActionRequired, {
			props: { items: Array.from({ length: 8 }, (_, index) => transaction(index)) }
		}).body;
		const text = body.replace(/<!--.*?-->/g, '').replace(/\s+/g, ' ');

		expect(text).toContain('8 actions need attention');
		expect(body).not.toContain('<details class="group" open>');
		expect(body).toContain('href="/contributions?focus=txn_0"');
		expect(body).toContain('SIPP 5');
		expect(body).not.toContain('SIPP 6');
		expect(text).toContain('Show 2 more');
		expect(body).toContain('href="/contributions"');
	});
});
