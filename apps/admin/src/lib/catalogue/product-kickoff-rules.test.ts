import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const products = readFileSync(new URL('./products-section.svelte', import.meta.url), 'utf8');
const actions = readFileSync(new URL('./actions.ts', import.meta.url), 'utf8');

describe('product kickoff configuration', () => {
	it('edits custody availability and the maximum party count at product grain', () => {
		expect(products).toContain('Available custody locations');
		expect(products).toContain('Eligible account owners');
		expect(products).toContain('Account currencies');
		expect(products).toContain('accounts are restricted to GBP');
		expect(products).toContain('Maximum parties per account');
		expect(products).toContain('min_age');
		expect(products).toContain('max_age');
		expect(products).toContain('Allow withdrawal bank details');
		// Lists of options, not a stack of checkboxes: MultiSelect is the control, and
		// `portal={false}` is load-bearing because this form lives in a Sheet.
		expect(products).toContain('<MultiSelect');
		expect(products).toContain('portal={false}');
		expect(products).toContain('bind:value={allowedCustodies}');
		expect(products).toContain('bind:value={allowedClientTypes}');
		expect(products).toContain('bind:value={allowedCurrencies}');
		expect(products).not.toContain('onCheckedChange={(value) => (onshore = value)}');
		expect(actions).toContain("min_age: () => num(data, 'min_age')");
		expect(actions).toContain("max_age: () => num(data, 'max_age')");
		expect(actions).toContain("bank_details_allowed: () => flag(data, 'bank_details_allowed')");
	});

	it('carries both settings through the partial product action', () => {
		expect(actions).toContain('allowed_custodies: () =>');
		expect(actions).toContain('allowed_client_types: () =>');
		expect(actions).toContain('allowed_currencies: () =>');
		expect(actions).toContain("max_parties: () => num(data, 'max_parties')");
	});
});
