// Static reference data for `{ source }` option refs on form fields (see resolveOptions).
//
// This is NOT tenant/mock form state — it's genuine shared reference data (e.g. the ISO
// country/nationality list) that both the platform builder preview and the client fill screen
// resolve dropdown options against. It lives here (not in `mock.ts`) so the forms feature path
// carries no dependency on demo fixtures. A field opts in with `options: { source: 'nationalities' }`.

import type { CountryIdentifierSpec, IdentifierCountry } from '../national-identifier.js';

/** ISO nationalities: `code` is the stored value; `adjective` is preferred as the display label. */
export const nationalities = [
	{ code: 'AU', name: 'Australia', adjective: 'Australian' },
	{ code: 'CA', name: 'Canada', adjective: 'Canadian' },
	{ code: 'CH', name: 'Switzerland', adjective: 'Swiss' },
	{ code: 'DE', name: 'Germany', adjective: 'German' },
	{ code: 'ES', name: 'Spain', adjective: 'Spanish' },
	{ code: 'FR', name: 'France', adjective: 'French' },
	{ code: 'GB', name: 'United Kingdom', adjective: 'British' },
	{ code: 'IE', name: 'Ireland', adjective: 'Irish' },
	{ code: 'IN', name: 'India', adjective: 'Indian' },
	{ code: 'IT', name: 'Italy', adjective: 'Italian' },
	{ code: 'NL', name: 'Netherlands', adjective: 'Dutch' },
	{ code: 'PL', name: 'Poland', adjective: 'Polish' },
	{ code: 'PT', name: 'Portugal', adjective: 'Portuguese' },
	{ code: 'SE', name: 'Sweden', adjective: 'Swedish' },
	{ code: 'US', name: 'United States', adjective: 'American' },
	{ code: 'ZZ', name: 'Other / unknown', adjective: 'Other' }
];

const NI_NUMBER: CountryIdentifierSpec = {
	name: 'National Insurance Number',
	regex: ['^[A-CEGHJ-PR-TW-Z]{1}[A-CEGHJ-NPR-TW-Z]{1}[\\d]{6}[A-DFM]{1}$'],
	example: 'QQ123456C',
	priority: 0,
	localeName: 'National Insurance Number'
};

const PASSPORT: CountryIdentifierSpec = {
	name: 'National Passport Number',
	regex: ['^[A-Z\\d-]{6,20}$'],
	example: 'AB12345678',
	priority: 0,
	localeName: 'National Passport Number'
};

const CONCAT: CountryIdentifierSpec = {
	name: 'CONCAT',
	regex: [],
	example: '19360821MARY#MIGGI',
	priority: 1,
	localeName: 'CONCAT'
};

const CONCAT_PRIMARY: CountryIdentifierSpec = { ...CONCAT, priority: 0 };

/** Country rows for national-identifier picking — shape matches `ref.countries` (0050).
 *  GB and ZZ carry the full identifier specs; the rest are present so EEA/non-EEA picking
 *  against the nationality list above has an `inEea` answer. */
const countryNames = new Map(nationalities.map((country) => [country.code, country.name]));
export const countries: IdentifierCountry[] = [
	{ code: 'AU', inEea: false, identifiers: [PASSPORT, CONCAT] },
	{ code: 'CA', inEea: false, identifiers: [PASSPORT, CONCAT] },
	{ code: 'CH', inEea: false, identifiers: [PASSPORT, CONCAT] },
	{ code: 'DE', inEea: true, identifiers: [CONCAT_PRIMARY] },
	{ code: 'ES', inEea: true, identifiers: [PASSPORT, CONCAT] },
	{ code: 'FR', inEea: true, identifiers: [CONCAT_PRIMARY] },
	{
		code: 'GB',
		inEea: true,
		identifiers: [NI_NUMBER, CONCAT]
	},
	{ code: 'IE', inEea: true, identifiers: [PASSPORT, CONCAT] },
	{ code: 'IN', inEea: false, identifiers: [PASSPORT, CONCAT] },
	{ code: 'IT', inEea: true, identifiers: [PASSPORT, CONCAT] },
	{ code: 'NL', inEea: true, identifiers: [PASSPORT, CONCAT] },
	{ code: 'PL', inEea: true, identifiers: [PASSPORT, CONCAT] },
	{ code: 'PT', inEea: true, identifiers: [PASSPORT, CONCAT] },
	{ code: 'SE', inEea: true, identifiers: [PASSPORT, CONCAT] },
	{ code: 'US', inEea: false, identifiers: [PASSPORT, CONCAT] },
	{
		code: 'ZZ',
		inEea: false,
		identifiers: [PASSPORT, CONCAT]
	}
].map((country) => ({ ...country, name: countryNames.get(country.code) ?? country.code }));

/** Data sources keyed by the `source` name a field's options may reference. */
export const formDataSources: Record<string, unknown[]> = {
	nationalities,
	countries
};
