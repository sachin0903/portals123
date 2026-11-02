import { describe, expect, it } from 'vitest';
import { productChoices, termDraftFromForm } from './terms';

describe('the product types a term can be scoped to', () => {
	it('offers the firm\u2019s own enabled shelf, deduplicated by wrapper', () => {
		// Two shelf items can share a wrapper (one per custodian); the scope keys off the wrapper, so
		// offering it twice would be two checkboxes for one condition.
		expect(
			productChoices([
				{ product_type: 'ISA', enabled: true },
				{ product_type: 'ISA', enabled: true },
				{ product_type: 'offshore_bond', enabled: true },
				{ product_type: 'SIPP', enabled: false },
				{ product_type: null, enabled: true }
			])
		).toEqual([
			{ value: 'ISA', label: 'ISA', onShelf: true },
			{ value: 'offshore_bond', label: 'Offshore bond', onShelf: true }
		]);
	});

	it('keeps a saved scope whose product has left the shelf', () => {
		// Those accounts still exist, so dropping the checkbox would widen the term on the next save
		// without anyone choosing that.
		expect(productChoices([{ product_type: 'ISA', enabled: true }], ['GIA'])).toEqual([
			{ value: 'GIA', label: 'GIA', onShelf: false },
			{ value: 'ISA', label: 'ISA', onShelf: true }
		]);
	});
});

describe('terms editor payload', () => {
	const form = (entries: [string, string][]) => {
		const data = new FormData();
		for (const [key, value] of entries) data.append(key, value);
		return data;
	};

	it('makes the all-clients choice exclusive of stale conditions', () => {
		const draft = termDraftFromForm(
			form([
				['title', 'Platform terms'],
				['required_at', 'onboarding'],
				['scope_mode', 'all'],
				['products', 'ISA'],
				['has_transfer', 'on']
			])
		);
		expect(draft).toMatchObject({
			applicability: { always: true, products: [], has_transfer: false }
		});
	});

	it('states conditional matching as OR conditions in the API shape', () => {
		const draft = termDraftFromForm(
			form([
				['title', 'Transfer terms'],
				['required_at', 'onboarding'],
				['required_at', 'login'],
				['scope_mode', 'conditions'],
				['products', 'ISA'],
				['products', 'JISA'],
				['has_transfer', 'on']
			])
		);
		expect(draft).toMatchObject({
			required_at: ['onboarding', 'login'],
			applicability: { always: false, products: ['ISA', 'JISA'], has_transfer: true }
		});
	});

	it('refuses ambiguous empty timing and conditional choices', () => {
		expect(termDraftFromForm(form([['scope_mode', 'all']]))).toEqual({
			error: 'Choose when clients must agree to these terms.'
		});
		expect(
			termDraftFromForm(
				form([
					['required_at', 'onboarding'],
					['scope_mode', 'conditions']
				])
			)
		).toEqual({ error: 'Choose at least one product or the transfer condition.' });
	});
});
