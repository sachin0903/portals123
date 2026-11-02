import { describe, expect, it } from 'vitest';
import {
	availableFeeIds,
	displayFees,
	feesForModel,
	isDisplayFee,
	selectFees,
	selectableFeeOption,
	unionDisplayFeeIds
} from './fee-packages';

const fees = [
	{ id: 1, name: 'Firm-wide', model_id: null, access: 'select' },
	{ id: 2, name: 'Model 10', model_id: 10, access: 'select' },
	{ id: 3, name: 'Model 20', model_id: 20, access: 'select' },
	{
		id: 4,
		name: 'Platform fee',
		model_id: null,
		access: 'display',
		fee_type: 'percentage',
		frequency: 'ongoing',
		rule: { rate: 0.35 }
	},
	{ id: 5, name: 'Hidden default', model_id: null, access: 'hidden', is_default: true },
	{ id: 6, name: 'Hidden', model_id: null, access: 'hidden', is_default: false },
	{ id: 7, name: 'Custom', model_id: null, access: 'custom' }
];

describe('account-form fee packages', () => {
	it('offers only unscoped fees until a model is selected', () => {
		expect(feesForModel(fees, null).map((fee) => fee.id)).toEqual([1, 4, 5, 6, 7]);
	});

	it('offers unscoped fees and fees for the selected model', () => {
		expect(feesForModel(fees, '10').map((fee) => fee.id)).toEqual([1, 2, 4, 5, 6, 7]);
		expect(feesForModel(fees, 20).map((fee) => fee.id)).toEqual([1, 3, 4, 5, 6, 7]);
	});

	it('drops an invisible selection after the model changes', () => {
		const visible = feesForModel(fees, 20);
		expect(availableFeeIds(['1', '2', '3'], visible)).toEqual(['1', '3']);
	});

	it('treats display and hidden-default packages as auto-applied', () => {
		expect(displayFees(fees).map((fee) => fee.id)).toEqual([4, 5]);
		expect(fees.filter(isDisplayFee).map((fee) => fee.id)).toEqual([4, 5]);
		expect(selectFees(fees).map((fee) => fee.id)).toEqual([1, 2, 3]);
	});

	it('unions display ids onto a picker selection so they cannot be omitted', () => {
		expect(unionDisplayFeeIds([], displayFees(fees))).toEqual(['4', '5']);
		expect(unionDisplayFeeIds(['1', '4'], displayFees(fees))).toEqual(['4', '5', '1']);
	});

	it('labels selectable packages with the actual charge', () => {
		expect(selectableFeeOption(fees[3]!).label).toBe('Platform fee: 0.35% ongoing');
	});
});
