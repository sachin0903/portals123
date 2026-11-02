import { describe, expect, it } from 'vitest';
import { answerLabel, answerRows, answerSections } from './answers.js';

const FACT_FIND = {
	dob: '1990-01-15',
	email: 'ada@example.com',
	phone: '07700900123',
	other_numbers: [
		{ label: 'home', number: '01179000000' },
		{ label: 'work', number: '01179000001' }
	],
	title: 'ms',
	gender: 'female',
	address: {
		line1: '12 Cheyne Walk',
		city: 'London',
		postcode: 'SW3 5RA',
		country: 'GB'
	},
	surname: 'Lovelace',
	first_name: 'Ada',
	nationality: 'GB',
	tax_residency: 'GB',
	national_identifier: 'QQ123456C',
	'bank_account.sort_code': '001122',
	national_identifier_type: 'ni_number',
	'bank_account.account_name': 'Ada Lovelace',
	'bank_account.account_number': '12345678'
};

describe('answerLabel', () => {
	it('reads a snake_case key as a person would say it', () => {
		expect(answerLabel('first_name')).toBe('First name');
		expect(answerLabel('national_identifier_type')).toBe('Identifier type');
		expect(answerLabel('model_id')).toBe('Investment model');
		expect(answerLabel('fee_package_ids')).toBe('Fee packages');
		expect(answerLabel('dividend_settlement_currency')).toBe('Preferred settlement currency');
	});

	it('splits a dotted path and names dob in full', () => {
		expect(answerLabel('dob')).toBe('Date of birth');
		expect(answerLabel('bank_account.sort_code')).toBe('Bank account · Sort code');
	});
});

describe('answerRows', () => {
	it('renders display labels for catalogue references while leaving stored answers untouched', () => {
		const answers = { model_id: '37', fee_package_ids: ['6', '12', '13'] };
		expect(
			answerRows(answers, {
				model_id: 'Balanced Growth',
				fee_package_ids: ['Platform charge', 'Advice charge', 'Custody charge']
			})
		).toEqual([
			{ key: 'model_id', label: 'Investment model', value: 'Balanced Growth' },
			{
				key: 'fee_package_ids',
				label: 'Fee packages',
				value: 'Platform charge, Advice charge, Custody charge'
			}
		]);
		expect(answers).toEqual({ model_id: '37', fee_package_ids: ['6', '12', '13'] });
	});

	it('formats the fact-find dump a staff member actually reviews', () => {
		expect(answerRows(FACT_FIND)).toEqual([
			{ key: 'dob', label: 'Date of birth', value: '15 Jan 1990' },
			{ key: 'email', label: 'Email', value: 'ada@example.com' },
			{ key: 'phone', label: 'Mobile number', value: '07700900123' },
			{
				key: 'other_numbers',
				label: 'Phone',
				value: 'Home 01179000000, Work 01179000001'
			},
			{ key: 'title', label: 'Title', value: 'Ms' },
			{ key: 'gender', label: 'Gender', value: 'Female' },
			{
				key: 'address',
				label: 'Address',
				value: '12 Cheyne Walk, London, SW3 5RA, United Kingdom'
			},
			{ key: 'surname', label: 'Surname', value: 'Lovelace' },
			{ key: 'first_name', label: 'First name', value: 'Ada' },
			{ key: 'nationality', label: 'Nationality', value: 'United Kingdom' },
			{ key: 'tax_residency', label: 'Tax residency', value: 'United Kingdom' },
			{ key: 'national_identifier', label: 'National identifier', value: 'QQ123456C' },
			{ key: 'bank_account.sort_code', label: 'Bank account · Sort code', value: '00 - 11 - 22' },
			{
				key: 'national_identifier_type',
				label: 'Identifier type',
				value: 'National Insurance number'
			},
			{
				key: 'bank_account.account_name',
				label: 'Bank account · Account name',
				value: 'Ada Lovelace'
			},
			{
				key: 'bank_account.account_number',
				label: 'Bank account · Account number',
				value: '12345678'
			}
		]);
	});

	it('flattens a nested bank account the same way as dotted keys', () => {
		expect(
			answerRows({
				bank_account: { sort_code: '001122', account_name: 'Ada Lovelace' }
			})
		).toEqual([
			{ key: 'bank_account.sort_code', label: 'Bank account · Sort code', value: '00 - 11 - 22' },
			{
				key: 'bank_account.account_name',
				label: 'Bank account · Account name',
				value: 'Ada Lovelace'
			}
		]);
	});

	it('drops empty values rather than rendering a row of dashes', () => {
		expect(answerRows({ first_name: 'Ada', middle_name: '', nickname: null })).toEqual([
			{ key: 'first_name', label: 'First name', value: 'Ada' }
		]);
	});

	it('reads a list of labelled numbers as one line, in the order they were given', () => {
		// The `phoneNumbers` field answers with an array of `{label, number}`. Flattened the generic
		// way that is four rows describing the SHAPE ("Phone - 1 - Label"), which reads worse
		// than the `[object Object]` this module exists to stop. It is one fact, and its order is the
		// applicant's own — never sorted, because "the first one to try" is part of the answer.
		expect(
			answerRows({
				other_numbers: [
					{ label: 'work', number: '01179000001' },
					{ label: 'home', number: '01179000000' }
				]
			})
		).toEqual([
			{
				key: 'other_numbers',
				label: 'Phone',
				value: 'Work 01179000001, Home 01179000000'
			}
		]);
	});

	it('drops a blank number out of the list, and the whole row when none is left', () => {
		expect(answerRows({ other_numbers: [{ label: 'home', number: '' }] })).toEqual([]);
		expect(
			answerRows({
				other_numbers: [{ label: 'home', number: '' }, { number: '01179000002' }]
			})
		).toEqual([{ key: 'other_numbers', label: 'Phone', value: '01179000002' }]);
	});

	it('never stringifies an object as [object Object]', () => {
		const rows = answerRows({
			address: { line1: '1 High Street', postcode: 'YO1 1AA' },
			notes: { extra: 'Kept' }
		});
		expect(rows.map((r) => r.value).join(' ')).not.toContain('[object Object]');
		expect(rows).toContainEqual({
			key: 'address',
			label: 'Address',
			value: '1 High Street, YO1 1AA'
		});
		expect(rows).toContainEqual({ key: 'notes.extra', label: 'Notes · Extra', value: 'Kept' });
	});
});

describe('answerSections', () => {
	it('orders a fact-find as a person would read it and groups the bank details', () => {
		expect(answerSections(FACT_FIND)).toEqual([
			{
				heading: null,
				rows: [
					{ key: 'title', label: 'Title', value: 'Ms' },
					{ key: 'first_name', label: 'First name', value: 'Ada' },
					{ key: 'surname', label: 'Surname', value: 'Lovelace' },
					{ key: 'dob', label: 'Date of birth', value: '15 Jan 1990' },
					{ key: 'gender', label: 'Gender', value: 'Female' },
					{ key: 'nationality', label: 'Nationality', value: 'United Kingdom' },
					{ key: 'tax_residency', label: 'Tax residency', value: 'United Kingdom' },
					{ key: 'national_identifier', label: 'National identifier', value: 'QQ123456C' },
					{
						key: 'national_identifier_type',
						label: 'Identifier type',
						value: 'National Insurance number'
					},
					{ key: 'email', label: 'Email', value: 'ada@example.com' },
					{ key: 'phone', label: 'Mobile number', value: '07700900123' },
					{
						key: 'other_numbers',
						label: 'Phone',
						value: 'Home 01179000000, Work 01179000001'
					},
					{
						key: 'address',
						label: 'Address',
						value: '12 Cheyne Walk, London, SW3 5RA, United Kingdom'
					}
				]
			},
			{
				heading: 'Bank account',
				rows: [
					{
						key: 'bank_account.account_name',
						label: 'Account name',
						value: 'Ada Lovelace'
					},
					{
						key: 'bank_account.account_number',
						label: 'Account number',
						value: '12345678'
					},
					{ key: 'bank_account.sort_code', label: 'Sort code', value: '00 - 11 - 22' }
				]
			}
		]);
	});
});
