import { describe, expect, it } from 'vitest';
import { ALL, humanise, matches, matchesAny, mergeOptions, optionsFrom, search } from './filters';

describe('matches', () => {
	it('passes everything when unfiltered', () => {
		expect(matches(ALL, 'active')).toBe(true);
		expect(matches(ALL, null)).toBe(true);
	});

	it('compares exactly once filtered', () => {
		expect(matches('active', 'active')).toBe(true);
		expect(matches('active', 'closed')).toBe(false);
	});

	it('treats a missing value as empty rather than a match', () => {
		expect(matches('active', null)).toBe(false);
		expect(matches('active', undefined)).toBe(false);
	});
});

describe('matchesAny', () => {
	it('matches when any value hits', () => {
		expect(matchesAny('Craig', ['Andy', 'Craig'])).toBe(true);
		expect(matchesAny('Gio', ['Andy', 'Craig'])).toBe(false);
	});

	it('passes everything when unfiltered, including empty lists', () => {
		expect(matchesAny(ALL, [])).toBe(true);
	});

	it('does not match an empty adviser list against a real filter', () => {
		expect(matchesAny('Craig', [])).toBe(false);
	});
});

describe('search', () => {
	it('matches case-insensitively across fields', () => {
		expect(search('mitch', 'James Mitchell', null)).toBe(true);
		expect(search('MITCH', 'James Mitchell')).toBe(true);
	});

	it('matches everything on an empty or whitespace query', () => {
		expect(search('', 'anything')).toBe(true);
		expect(search('   ', 'anything')).toBe(true);
	});

	it('tolerates null fields', () => {
		expect(search('x', null, undefined)).toBe(false);
	});
});

describe('optionsFrom', () => {
	const rows = [
		{ status: 'active', advisers: ['Craig'] },
		{ status: 'closed', advisers: ['Andy', 'Craig'] },
		{ status: 'active', advisers: [] },
		{ status: null, advisers: [null] }
	];

	it('dedupes and drops blanks', () => {
		expect(optionsFrom(rows, (r) => r.status)).toEqual([
			{ value: 'active', label: 'active' },
			{ value: 'closed', label: 'closed' }
		]);
	});

	it('flattens multi-valued columns', () => {
		expect(optionsFrom(rows, (r) => r.advisers).map((o) => o.value)).toEqual(['Andy', 'Craig']);
	});

	it('applies the label formatter', () => {
		expect(optionsFrom(rows, (r) => r.status, humanise)[0]).toEqual({
			value: 'active',
			label: 'Active'
		});
	});

	it('collates numerically so Account 2 precedes Account 10', () => {
		const named = [{ n: 'Account 10' }, { n: 'Account 2' }];
		expect(optionsFrom(named, (r) => r.n).map((o) => o.value)).toEqual(['Account 2', 'Account 10']);
	});
});

describe('humanise', () => {
	it('title-cases snake_case', () => {
		expect(humanise('execution_only')).toBe('Execution only');
	});

	it('renders a dash for nothing', () => {
		expect(humanise(null)).toBe('—');
		expect(humanise('')).toBe('—');
	});
});

describe('multi-select filters', () => {
	// The filter bar hands these matchers a LIST. The Python twin in `app/listing.py` answers the
	// same questions the same way — the two run over the same rows on different sides of the wire,
	// and a listing that filters locally must agree with the one that pages against the API.
	it('reads a list as an OR', () => {
		expect(matches(['active', 'onboarding'], 'onboarding')).toBe(true);
		expect(matches(['active', 'onboarding'], 'suspended')).toBe(false);
	});

	it('treats an EMPTIED multi-select as no filter, not as "match nothing"', () => {
		// A reader who unticks the last value means "show everything" — the same as never having
		// set it. Narrowing to zero rows there reads as an empty table, not as a cleared filter.
		expect(matches([], 'anything')).toBe(true);
		expect(matchesAny([], ['anything'])).toBe(true);
	});

	it('matches when BOTH sides are several', () => {
		// A client advised by Cole and Shaw is a match for a filter of Shaw or Vale.
		expect(matchesAny(['Shaw', 'Vale'], ['Cole', 'Shaw'])).toBe(true);
		expect(matchesAny(['Vale'], ['Cole', 'Shaw'])).toBe(false);
	});

	it('still answers a plain string the way every existing listing passes one', () => {
		expect(matches(ALL, 'anything')).toBe(true);
		expect(matches('active', 'active')).toBe(true);
		expect(matchesAny('Shaw', ['Cole', 'Shaw'])).toBe(true);
	});
});

describe('mergeOptions', () => {
	it('keeps the first list whole and authoritative, then appends what only the rows know', () => {
		// The contributions queue prepends credits from an endpoint the facets never saw, so a firm
		// with nothing booked saw three filters reading "No matches." above a full table. Never
		// re-sorted: the stage order IS the lifecycle, and alphabetising puts Completed first.
		const facet = [
			{ value: 'lodged', label: 'Lodged', count: 4 },
			{ value: 'settled', label: 'Settled', count: 2 }
		];
		const rows = [
			{ value: 'lodged', label: 'Wrong', count: 99 },
			{ value: 'identified', label: 'Awaiting classification' }
		];
		expect(mergeOptions(facet, rows)).toEqual([...facet, rows[1]]);
		expect(mergeOptions([], rows)).toEqual(rows);
	});
});
