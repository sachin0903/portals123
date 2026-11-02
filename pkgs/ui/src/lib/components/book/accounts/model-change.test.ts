import { describe, expect, it } from 'vitest';
import {
	asRate,
	modelChangeAction,
	modelChangeSummary,
	modelFacts,
	NO_MODEL
} from './model-change';

describe('the model change sentence', () => {
	it('says nothing until the choice differs from the current model', () => {
		expect(modelChangeSummary('Balanced 40', 'Balanced 40', false)).toBeNull();
		expect(modelChangeSummary(null, null, false)).toBeNull();
	});

	it('names both models when one replaces another', () => {
		expect(modelChangeSummary('Balanced 40', 'Growth 60', true)).toBe(
			'Growth 60 replaces Balanced 40.'
		);
		expect(modelChangeAction('Balanced 40', 'Growth 60')).toBe('Replace model');
	});

	it('says a model is being added to an account that has none', () => {
		expect(modelChangeSummary(null, 'Growth 60', true)).toBe(
			'Growth 60 is added. The account has no model at the moment.'
		);
		expect(modelChangeAction(null, 'Growth 60')).toBe('Add model');
	});

	it('says which model is being removed when the mandate is cleared', () => {
		expect(modelChangeSummary('Balanced 40', null, true)).toBe(
			'Balanced 40 is removed. The account will have no model.'
		);
		expect(modelChangeAction('Balanced 40', null)).toBe('Remove model');
		expect(NO_MODEL).toBe('No model');
	});
});

describe('model facts', () => {
	it('states risk, target return and charge as percentage points, skipping blanks', () => {
		expect(
			modelFacts({
				id: 1,
				name: 'Growth 60',
				risk_rating: 6,
				target_return: '6.5000',
				model_charge: 0.2
			})
		).toEqual([
			['Risk rating', '6'],
			['Target return', '6.5% a year'],
			['Model charge', '0.2% a year']
		]);
		expect(modelFacts({ id: 2, name: 'Cash', risk_rating: null, target_return: null })).toEqual([]);
		expect(modelFacts(null)).toEqual([]);
	});

	it('normalises a rate rather than printing the record’s trailing zeros', () => {
		expect(asRate('6.5000')).toBe('6.5%');
		expect(asRate(0)).toBe('0%');
		expect(asRate('')).toBeNull();
		expect(asRate('abc')).toBeNull();
	});
});
