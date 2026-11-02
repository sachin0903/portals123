import { describe, expect, it } from 'vitest';
import { personComplianceFacts } from './person-facts.js';

const blank = {
	us_person: null,
	fatca_status: null,
	source_of_funds: null,
	source_of_funds_detail: null,
	source_of_wealth: null,
	source_of_wealth_detail: null
};

describe('personComplianceFacts', () => {
	it('names each question, unanswered as null', () => {
		expect(personComplianceFacts(blank)).toEqual([
			['Is a US person?', null],
			['W-9 or W-8', null],
			['Source of funds', null],
			['Source of wealth', null]
		]);
	});

	it('reads the US-person answer and the tax form separately', () => {
		expect(personComplianceFacts({ ...blank, us_person: true })[0]).toEqual([
			'Is a US person?',
			'Yes'
		]);
		expect(personComplianceFacts({ ...blank, us_person: false })[0]).toEqual([
			'Is a US person?',
			'No'
		]);
		expect(personComplianceFacts({ ...blank, fatca_status: 'w9' })[1]).toEqual([
			'W-9 or W-8',
			'W-9'
		]);
		expect(personComplianceFacts({ ...blank, fatca_status: 'w8_cure' })[1]).toEqual([
			'W-9 or W-8',
			'W8 cure'
		]);
	});

	it('states the signed form under a W-9 or a W-8, and only there', () => {
		const form = (over: Partial<Parameters<typeof personComplianceFacts>[0]>) =>
			personComplianceFacts({ ...blank, ...over }).find(([label]) => label === 'W-9 / W-8 form');
		expect(form({ fatca_status: 'w9' })).toEqual(['W-9 / W-8 form', 'Not uploaded']);
		expect(
			form({ fatca_status: 'w8', fatca_document: { uid: 'doc_w8', name: 'w8-signed.pdf' } })
		).toEqual(['W-9 / W-8 form', 'w8-signed.pdf']);
		// It sits straight under the answer it belongs to.
		expect(personComplianceFacts({ ...blank, fatca_status: 'w9' })[2]).toEqual([
			'W-9 / W-8 form',
			'Not uploaded'
		]);
		expect(form({})).toBeUndefined();
		expect(form({ fatca_status: 'w8_cure' })).toBeUndefined();
	});

	it('adds the detail under an "Other" answer', () => {
		expect(
			personComplianceFacts({
				...blank,
				source_of_funds: 'Other (please specify)',
				source_of_funds_detail: 'Lottery win',
				source_of_wealth: 'sale_of_property'
			})
		).toEqual([
			['Is a US person?', null],
			['W-9 or W-8', null],
			['Source of funds', 'Other (please specify)'],
			['Detail', 'Lottery win'],
			['Source of wealth', 'Sale of property']
		]);
	});
});
