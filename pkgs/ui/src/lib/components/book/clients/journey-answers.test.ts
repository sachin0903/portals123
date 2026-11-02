import { describe, expect, it } from 'vitest';
import { presentAnswers, scoredResult, type JourneyField } from './journey-answers';

const mortgage: JourneyField = {
	key: 'mortgages',
	label: 'Mortgages',
	section: 'Mortgages',
	field_type: 'repeating',
	options: [
		{ key: 'lender', label: 'Lender', type: 'text', required: true },
		{ key: 'balance', label: 'Balance', type: 'currency', required: false }
	]
};

describe('presentAnswers', () => {
	it('groups answered fields by section and leaves blanks out', () => {
		const sections = presentAnswers(
			[
				{
					key: 'first_name',
					label: 'First name',
					section: 'Personal details',
					field_type: 'text',
					options: null
				},
				{
					key: 'middle_name',
					label: 'Middle name',
					section: 'Personal details',
					field_type: 'text',
					options: null
				},
				{
					key: 'employment_status',
					label: 'Employment',
					section: 'Occupation',
					field_type: 'select',
					options: { options: [{ value: 'employed', label: 'Employed' }] }
				}
			],
			{ first_name: 'Ada', employment_status: 'employed' }
		);
		expect(sections).toEqual([
			{ heading: 'Personal details', rows: [{ label: 'First name', value: 'Ada' }] },
			{ heading: 'Occupation', rows: [{ label: 'Employment', value: 'Employed' }] }
		]);
	});

	it('reads a repeating schedule as rows, not JSON', () => {
		const [section] = presentAnswers([mortgage], {
			mortgages: [{ lender: 'Halifax', balance: 120000 }]
		});
		expect(section.rows[0].table).toEqual({
			columns: ['Lender', 'Balance'],
			rows: [['Halifax', '£120,000.00']],
			cells: [
				[{ text: 'Halifax' }, { text: '£120,000.00', money: { amount: 120000, currency: 'GBP' } }]
			]
		});
		expect(section.rows[0].label).toBe('Mortgages');
	});

	it('reads a country code as the country', () => {
		const [section] = presentAnswers(
			[
				{
					key: 'nationality',
					label: 'Nationality',
					section: 'Personal details',
					field_type: 'text',
					options: null
				}
			],
			{ nationality: 'GB' }
		);
		expect(section.rows[0].value).toBe('United Kingdom');
	});

	it('keeps an answer whose field has left the form', () => {
		const sections = presentAnswers([], { intended_retirement_age: '67' });
		expect(sections[0].rows[0]).toMatchObject({
			label: 'Intended retirement age',
			value: '67'
		});
	});
});

describe('scoredResult', () => {
	it('reads the band and the score off the outputs', () => {
		expect(
			scoredResult({
				band: 'cautious',
				score: 18,
				label: 'Cautious',
				definition: 'A careful investor.'
			})
		).toEqual({ headline: 'Cautious · 18', definition: 'A careful investor.' });
	});

	it('is absent when nothing was scored', () => {
		expect(scoredResult({})).toBeNull();
		expect(scoredResult(null)).toBeNull();
	});
});

describe('money answers', () => {
	it('carry the figure and currency so the reader can split the pence', () => {
		const [section] = presentAnswers(
			[
				{
					key: 'income',
					label: 'Income',
					section: null,
					field_type: 'currency',
					options: { currency: 'EUR' }
				}
			],
			{ income: '1,250.5' }
		);
		expect(section.rows[0].money).toEqual({ amount: 1250.5, currency: 'EUR' });
		expect(section.rows[0].value).not.toBe('');
	});
});
