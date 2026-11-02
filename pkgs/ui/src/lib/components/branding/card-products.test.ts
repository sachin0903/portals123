import { describe, expect, it } from 'vitest';
import { cardProductTypes } from './card-products.js';

describe('cardProductTypes', () => {
	it('lists the products a firm has enabled, not a fixed set of wrappers', () => {
		const rows = cardProductTypes([
			{ product_type: 'SIPP', name: 'Self-Invested Personal Pension', enabled: true },
			{ product_type: 'GIA', name: 'General Investment Account', enabled: true },
			{ product_type: 'JISA', name: 'Junior ISA', enabled: false },
			{ product_type: 'TPPENSION', name: 'Old pension', enabled: true }
		]);
		expect(rows.map((row) => row.type)).toEqual(['GIA', 'SIPP', 'TPPENSION']);
		expect(rows.map((row) => row.type)).not.toContain('LISA');
		expect(rows.map((row) => row.type)).not.toContain('JISA');
	});

	it('uses the firm’s own name, and the type’s label when the row was never renamed', () => {
		const rows = cardProductTypes([
			{ product_type: 'ISA', name: 'ISA', enabled: true },
			{ product_type: 'GIA', name: 'Sterling GIA', enabled: true },
			{ product_type: 'TPONBOND', name: '   ', enabled: true }
		]);
		expect(rows).toEqual([
			{ type: 'GIA', name: 'Sterling GIA' },
			{ type: 'ISA', name: 'Individual Savings Account' },
			{ type: 'TPONBOND', name: 'Third-party Onshore Bond' }
		]);
	});

	it('keeps one row per type and drops a blank type', () => {
		const rows = cardProductTypes([
			{ product_type: 'GIA', name: 'First', enabled: true },
			{ product_type: 'GIA', name: 'Second', enabled: true },
			{ product_type: '  ', name: 'Nameless', enabled: true },
			{ product_type: null, name: 'None', enabled: true }
		]);
		expect(rows).toEqual([{ type: 'GIA', name: 'First' }]);
	});

	it('sorts a type outside the vocabulary after the known ones', () => {
		const rows = cardProductTypes([
			{ product_type: 'ZZZ', name: 'Bespoke', enabled: true },
			{ product_type: 'ISA', name: 'ISA', enabled: true }
		]);
		expect(rows.map((row) => row.type)).toEqual(['ISA', 'ZZZ']);
	});
});
