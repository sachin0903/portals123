import { describe, expect, it } from 'vitest';
import {
	addressLine,
	bornLine,
	countryReasons,
	groupByType,
	linkHost,
	matchScore,
	matchSubtitle,
	partialDate,
	roleDates
} from './aml-match.js';

describe('a match card', () => {
	it('prints the score as a whole percentage', () => {
		expect(matchScore(93.4)).toBe('93%');
		expect(matchScore(100)).toBe('100%');
		expect(matchScore(null)).toBeNull();
	});

	it('says what is known of gender and country under the name', () => {
		expect(matchSubtitle({ gender: 'male', countries: ['BY'] })).toBe('Male · Belarus');
		expect(matchSubtitle({ gender: null, countries: ['GB', 'FR'] })).toBe(
			'United Kingdom · France'
		);
		expect(matchSubtitle({ gender: null, countries: [] })).toBe('');
	});

	it('writes an address with the country named', () => {
		expect(
			addressLine({ line: 'Karl Marx, 38', city: 'Minsk', postal_code: '220016', country: 'BY' })
		).toBe('Karl Marx, 38, Minsk, 220016, Belarus');
		expect(addressLine({ line: null, city: 'Leeds', postal_code: null, country: null })).toBe(
			'Leeds'
		);
	});

	it('reads a partial date as far as it goes', () => {
		expect(partialDate('1958-05-26')).toBe('26 May 1958');
		expect(partialDate('2015-10')).toBe('Oct 2015');
		expect(partialDate('2015')).toBe('2015');
		expect(partialDate('2015-13')).toBe('2015-13');
		expect(partialDate('circa 1960')).toBe('circa 1960');
		expect(partialDate(null)).toBe('—');
	});
});

describe("a match's full profile", () => {
	it("names a link by its site, or shows it as it came when it can't be read", () => {
		expect(linkHost('https://www.bbc.co.uk/news/uk-scotland-123')).toBe('bbc.co.uk');
		expect(linkHost('https://forbes.com/profile/x')).toBe('forbes.com');
		expect(linkHost('not a url')).toBe('not a url');
	});

	it('says when a role was held, as far as ComplyCube gives dates', () => {
		expect(roleDates({ from: '2014-11-19', to: '2023-03', current: false })).toBe(
			'19 Nov 2014 – Mar 2023'
		);
		expect(roleDates({ from: '2014', to: null, current: true })).toBe('2014 – now');
		expect(roleDates({ from: '2014', to: null, current: false })).toBe('From 2014');
		expect(roleDates({ from: null, to: '2016', current: false })).toBe('Until 2016');
		expect(roleDates({ from: null, to: null, current: true })).toBe('Current');
		expect(roleDates({ from: null, to: null, current: false })).toBe('');
	});

	it('puts the date and the places of birth on one line', () => {
		expect(bornLine({ dob: '1970-07-19', birth_places: ['Irvine, United Kingdom'] })).toBe(
			'19 Jul 1970 · Irvine, United Kingdom'
		);
		expect(bornLine({ dob: '1970', birth_places: ['Irvine', 'Glasgow'] })).toBe(
			'1970 · Irvine; Glasgow'
		);
		expect(bornLine({ dob: null, birth_places: ['Irvine'] })).toBe('Irvine');
		expect(bornLine({ dob: '1970-07', birth_places: [] })).toBe('Jul 1970');
		expect(bornLine({ dob: null, birth_places: [' '] })).toBe('');
	});

	it('lists each country once with every reason, a code and its name as one', () => {
		expect(
			countryReasons([
				{ country: 'GB', type: 'Born in' },
				{ country: 'FR', type: 'Resident of' },
				{ country: 'United Kingdom', type: 'Citizenship' },
				{ country: 'GB', type: 'Born in' },
				{ country: 'DE', type: null }
			])
		).toEqual([
			{ country: 'United Kingdom', reasons: ['Born in', 'Citizenship'] },
			{ country: 'France', reasons: ['Resident of'] },
			{ country: 'Germany', reasons: [] }
		]);
	});

	it('groups items by type in the order each type first appears', () => {
		const names = [
			{ name: 'Nicola Ferguson Sturgeon', type: 'Primary' },
			{ name: 'Nicola Sturgeon', type: 'Also known as' },
			{ name: 'Никола Стерджен', type: 'Localised name' },
			{ name: 'N. Sturgeon', type: 'Also known as' },
			{ name: 'Sturgeon', type: null }
		];
		expect(groupByType(names).map((g) => [g.type, g.items.map((n) => n.name)])).toEqual([
			['Primary', ['Nicola Ferguson Sturgeon']],
			['Also known as', ['Nicola Sturgeon', 'N. Sturgeon']],
			['Localised name', ['Никола Стерджен']],
			['Other', ['Sturgeon']]
		]);
	});
});
