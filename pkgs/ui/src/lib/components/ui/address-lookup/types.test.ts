import { describe, expect, it } from 'vitest';
import { isAddressSuggestionContainer, normaliseAddress } from './types.js';

describe('normaliseAddress', () => {
	it('keeps the legacy UK component ordering', () => {
		expect(
			normaliseAddress({
				organisation: 'Graphene',
				premise: '1',
				buildingname: 'Woodlands Hall',
				street: 'Bakers Lane',
				dependentlocality: 'Little Test',
				posttown: 'London',
				county: 'Greater London',
				postcode: 'SW1A 1AA',
				countrycode: 'GB'
			})
		).toMatchObject({
			line1: 'Graphene, 1 Woodlands Hall, Bakers Lane',
			line2: 'Little Test',
			town: 'London',
			county: 'Greater London',
			postcode: 'SW1A 1AA',
			countryCode: 'GB'
		});
	});

	it('prefers Postcoder’s country-aware address lines for international addresses', () => {
		expect(
			normaliseAddress(
				{
					addressline1: '123 Rue Brizeux',
					addressline2: 'Bâtiment B',
					addressline3: 'Entrée 4',
					posttown: 'Plouvien',
					county: 'Finistère',
					postcode: '29860',
					country: 'France'
				},
				'FR'
			)
		).toEqual({
			line1: '123 Rue Brizeux',
			line2: 'Bâtiment B, Entrée 4',
			town: 'Plouvien',
			county: 'Finistère',
			postcode: '29860',
			country: 'France',
			countryCode: 'FR',
			latitude: undefined,
			longitude: undefined
		});
	});

	it('uses state for countries where county is not the relevant region', () => {
		expect(
			normaliseAddress(
				{
					addressline1: '2101 E Coast Hwy',
					posttown: 'Corona del Mar',
					state: 'California',
					stateabbreviation: 'CA',
					postcode: '92625'
				},
				'US'
			).county
		).toBe('California');
	});
});

describe('isAddressSuggestionContainer', () => {
	it('uses Postcoder’s type rather than an unreliable result count', () => {
		expect(
			isAddressSuggestionContainer({
				id: 'street',
				type: 'STR',
				summaryline: 'Rue Brizeux',
				count: 1
			})
		).toBe(true);
		expect(
			isAddressSuggestionContainer({
				id: 'address',
				type: 'ADD',
				summaryline: '123 Rue Brizeux',
				count: 20
			})
		).toBe(false);
	});
});
