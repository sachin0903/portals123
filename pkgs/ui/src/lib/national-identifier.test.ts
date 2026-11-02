/**
 * The identifier rules are the REGULATOR'S, so they are pinned against the reference data rather
 * than against our own reading of them: `ref.countries.identifiers` (0050) carries the real lists,
 * and `graphene-platform`'s `lib/utils/national-identifier.ts` is the implementation this is a port
 * of. A divergence here is a mis-reported transaction, not a cosmetic difference.
 */
import { describe, expect, it } from 'vitest';
import {
	computeConcat,
	nationalityOptions,
	parseIdentifiers,
	pickIdentifierCountry,
	pickIdentifierSpec,
	type IdentifierCountry
} from './national-identifier.js';

/** The real rows, verbatim from `ref.countries`. */
const GB: IdentifierCountry = {
	code: 'GB',
	name: 'United Kingdom',
	adjective: 'British',
	in_eea: true,
	identifiers: [
		{
			name: 'National Insurance Number',
			regex: ['^[A-CEGHJ-PR-TW-Z]{1}[A-CEGHJ-NPR-TW-Z]{1}[\\d]{6}[A-DFM]{1}$'],
			example: 'QQ123456C',
			priority: 0,
			localeName: 'National Insurance Number'
		},
		{ name: 'CONCAT', regex: [], example: '19360821MARY#MIGGI', priority: 1 }
	]
};
const IE: IdentifierCountry = {
	code: 'IE',
	name: 'Ireland',
	adjective: 'Irish',
	in_eea: true,
	identifiers: [{ name: 'CONCAT', regex: [], priority: 0 }]
};
const US: IdentifierCountry = {
	code: 'US',
	name: 'United States',
	adjective: 'American',
	in_eea: false,
	identifiers: [
		{ name: 'National Passport Number', regex: ['^[A-Z\\d-]{6,20}$'], priority: 0 },
		{ name: 'CONCAT', regex: [], priority: 1 }
	]
};
const byCode = new Map([GB, IE, US].map((c) => [c.code, c]));

describe('which nationality decides the identifier', () => {
	it('prefers an EEA nationality over a non-EEA one', () => {
		expect(pickIdentifierCountry(['US', 'GB'], byCode)).toBe('GB');
	});

	it('treats the UK as EEA', () => {
		// The regulation's preference order predates the withdrawal and the onshored regime kept it.
		// Dropping GB would quietly re-rank every dual national.
		expect(pickIdentifierCountry(['GB'], byCode)).toBe('GB');
	});

	it('breaks a tie between EEA nationalities by ISO code', () => {
		// Alphabetical is arbitrary and that is the POINT — it is deterministic, so two firms
		// reporting the same dual national reach the same identifier.
		expect(pickIdentifierCountry(['IE', 'GB'], byCode)).toBe('GB');
		expect(pickIdentifierCountry(['GB', 'IE'], byCode)).toBe('GB');
	});

	it('falls back to the first nationality when none is EEA', () => {
		expect(pickIdentifierCountry(['US'], byCode)).toBe('US');
	});

	it('uses the all-other-countries bucket when the data carries one', () => {
		const withBucket = new Map(byCode);
		withBucket.set('ZZ', { code: 'ZZ', identifiers: [{ name: 'CONCAT', regex: [], priority: 0 }] });
		expect(pickIdentifierCountry(['US'], withBucket)).toBe('ZZ');
	});

	it('answers null for a client with no nationality on file', () => {
		// Not a default country: the identifier is decided BY nationality, so with none the honest
		// answer is that we cannot say which is required.
		expect(pickIdentifierCountry([], byCode)).toBeNull();
		expect(pickIdentifierCountry([''], byCode)).toBeNull();
	});
});

describe('what the identifier is called', () => {
	it('is the country’s own priority-0 name, never "NINO"', () => {
		// "NINO" is not the name of anything. The UK's entry reads "National Insurance Number",
		// which is what a form asks for and what is stored beside the digits — a firm reading a
		// record back needs to know which scheme they belong to.
		expect(pickIdentifierSpec('GB', byCode)?.name).toBe('National Insurance Number');
		expect(pickIdentifierSpec('US', byCode)?.name).toBe('National Passport Number');
	});

	it('is CONCAT where a country has no national number to give', () => {
		expect(pickIdentifierSpec('IE', byCode)?.name).toBe('CONCAT');
	});

	it('sorts by priority rather than trusting the stored order', () => {
		const jumbled = parseIdentifiers([
			{ name: 'CONCAT', priority: 1 },
			{ name: 'National Insurance Number', priority: 0 }
		]);
		expect(jumbled.map((s) => s.name)).toEqual(['National Insurance Number', 'CONCAT']);
	});

	it('drops a malformed entry rather than the whole list', () => {
		// The column is jsonb written by a seed; one bad row must not cost a country its identifier.
		expect(parseIdentifiers([{ nope: true }, { name: 'CONCAT', priority: 0 }])).toHaveLength(1);
		expect(parseIdentifiers('not a list')).toEqual([]);
	});
});

describe('CONCAT', () => {
	it('matches the regulation’s own worked example', () => {
		// `19360821MARY#MIGGI` is the example the reference data ships for every country.
		expect(computeConcat({ dob: '1936-08-21', firstName: 'Mary', surname: 'Miggi' })).toBe(
			'19360821MARY#MIGGI'
		);
	});

	it('pads a short name with # and truncates a long one to five', () => {
		expect(computeConcat({ dob: '1990-01-02', firstName: 'Jo', surname: 'Fotherington' })).toBe(
			'19900102JO###FOTHE'
		);
	});

	it('strips punctuation, spaces and accents rather than dropping the letters', () => {
		// A plain [^A-Z] filter would take É out entirely and shift every later character.
		expect(computeConcat({ dob: '1990-01-02', firstName: 'Jean-Luc', surname: 'Émile' })).toBe(
			'19900102JEANLEMILE'
		);
		expect(computeConcat({ dob: '1990-01-02', firstName: "O'Brien", surname: 'De La Rue' })).toBe(
			'19900102OBRIEDELAR'
		);
	});

	it('answers null rather than a partial identifier', () => {
		// A partial CONCAT is not a shorter identifier, it is a wrong one.
		expect(computeConcat({ dob: null, firstName: 'Mary', surname: 'Miggi' })).toBeNull();
		expect(computeConcat({ dob: '1936-08-21', firstName: '', surname: 'Miggi' })).toBeNull();
		expect(computeConcat({ dob: 'not a date', firstName: 'Mary', surname: 'Miggi' })).toBeNull();
	});
});

describe('nationality options', () => {
	it('labels by adjective, not by country name', () => {
		// A nationality is a property of the person and reads as an adjective in every document a
		// firm produces. The country NAME answers a different question (tax residency, birth).
		expect(nationalityOptions([GB])).toEqual([{ value: 'GB', label: 'British' }]);
	});

	it('disambiguates a shared adjective with the country', () => {
		// "Chinese" alone cannot be posted as a code.
		const cn = { code: 'CN', name: 'China', adjective: 'Chinese' };
		const tw = { code: 'TW', name: 'Taiwan', adjective: 'Chinese' };
		expect(nationalityOptions([cn, tw]).map((o) => o.label)).toEqual([
			'Chinese (China)',
			'Chinese (Taiwan)'
		]);
	});

	it('falls back to the country name rather than dropping the row', () => {
		// REVERSED, deliberately. This dropped the country, on the reasoning that "the label would
		// be the country name, which is the answer to a different question" — and the form engine's
		// copy of the same rule fell back to `adjective ?? name ?? value`, so a `ref.countries` row
		// with a blank adjective was answerable in the onboarding fact-find and unrepresentable on
		// the client record. The identifier REGIME is derived from the nationality set, so an
		// unpickable country is a reporting gap rather than a cosmetic one; one rule, and it is the
		// one that cannot make an answer unreachable. All 250 seeded rows carry an adjective, so
		// this arm fires on no data we hold.
		expect(nationalityOptions([{ code: 'XX', name: 'Nowhere' }])).toEqual([
			{ value: 'XX', label: 'Nowhere' }
		]);
	});

	it("keeps SOURCE order — the ordering is the calling surface's choice", () => {
		// The client record sorts by label (`client-record.svelte`); `resolveOptions` keeps the
		// order the reference data came in. Labelling is the rule, ordering is presentation, so
		// this returns the rows as given rather than deciding for both.
		expect(nationalityOptions([US, GB, IE]).map((o) => o.label)).toEqual([
			'American',
			'British',
			'Irish'
		]);
		expect(nationalityOptions([IE, US, GB]).map((o) => o.label)).toEqual([
			'Irish',
			'American',
			'British'
		]);
	});
});
