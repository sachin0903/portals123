/** The listing query's URL round-trip.
 *
 * These are the rules that make a filtered listing shareable and reversible, which is the whole
 * reason the state lives in the URL rather than in the component:
 *  - defaults are omitted, so `/clients` stays `/clients`;
 *  - unrelated params survive a filter change (a `?focus=` deep link is not collateral);
 *  - only declared filters are read, so a tracking tag is never sent to the API as a filter;
 *  - junk clamps instead of breaking, because a hand-edited URL is a normal thing to receive.
 */
import { describe, expect, it } from 'vitest';
import {
	PAGE_SIZE,
	apiParams,
	emptyQuery,
	isFiltered,
	itemsOf,
	optionsFromFacet,
	pageOf,
	paramsFromQuery,
	queryFromParams,
	rangeLabel,
	barValueFrom,
	withBarValue
} from './query.js';

const KEYS = ['status', 'adviser', 'type'] as const;

describe('reading a query out of a URL', () => {
	it('takes the search term, the declared filters and the offset', () => {
		const query = queryFromParams(
			new URLSearchParams('q=halloway&status=active&adviser=Dan+Reeve&offset=100'),
			KEYS
		);
		expect(query.q).toBe('halloway');
		expect(query.filters).toEqual({ status: 'active', adviser: 'Dan Reeve' });
		expect(query.offset).toBe(100);
		expect(query.limit).toBe(PAGE_SIZE);
	});

	it('ignores params that are not declared filters', () => {
		// `focus` is a deep link into the contributions workbench and `utm_source` is noise.
		// Forwarding either to the API as a filter would return nothing, silently.
		const query = queryFromParams(
			new URLSearchParams('status=active&focus=con_1&utm_source=email'),
			KEYS
		);
		expect(query.filters).toEqual({ status: 'active' });
	});

	it('is unfiltered for an empty URL', () => {
		expect(queryFromParams(new URLSearchParams(), KEYS)).toEqual(emptyQuery(KEYS));
	});

	it.each(['-10', 'abc', '', 'NaN', '1e999'])('clamps a junk offset (%s) to zero', (raw) => {
		expect(queryFromParams(new URLSearchParams(`offset=${raw}`), KEYS).offset).toBe(0);
	});

	it('floors a fractional offset rather than sending it on', () => {
		expect(queryFromParams(new URLSearchParams('offset=10.7'), KEYS).offset).toBe(10);
	});
});

describe('writing a query back to a URL', () => {
	it('omits everything at its default', () => {
		expect(paramsFromQuery(emptyQuery(KEYS)).toString()).toBe('');
	});

	it('omits offset zero, so the first page has a clean link', () => {
		const params = paramsFromQuery({ ...emptyQuery(KEYS), q: 'bex' });
		expect(params.toString()).toBe('q=bex');
	});

	it('keeps unrelated params through a filter change', () => {
		// Narrowing the list must not drop the `?focus=` the Action-required strip arrived on.
		const params = paramsFromQuery(
			{ ...emptyQuery(KEYS), filters: { status: 'active' } },
			new URLSearchParams('focus=con_1')
		);
		expect(params.get('focus')).toBe('con_1');
		expect(params.get('status')).toBe('active');
	});

	it('removes a filter that has been cleared', () => {
		const params = paramsFromQuery(emptyQuery(KEYS), new URLSearchParams('status=active&q=bex'));
		expect(params.has('status')).toBe(false);
		expect(params.has('q')).toBe(false);
	});

	it('trims the search term', () => {
		expect(paramsFromQuery({ ...emptyQuery(KEYS), q: '  bex  ' }).get('q')).toBe('bex');
	});

	it('round-trips', () => {
		const query = { ...emptyQuery(KEYS), q: 'bex', filters: { status: 'active' }, offset: 50 };
		expect(queryFromParams(paramsFromQuery(query), KEYS)).toEqual(query);
	});
});

describe('the API query string', () => {
	it('always states the limit, so the response is the paged envelope', () => {
		// Without `limit` the API returns a bare list — the house rule — and the pager would have
		// no total to read.
		expect(apiParams(emptyQuery(KEYS)).get('limit')).toBe(String(PAGE_SIZE));
	});

	it('carries the search and filters', () => {
		const params = apiParams({
			...emptyQuery(KEYS),
			q: 'bex',
			filters: { status: 'active' },
			offset: 50
		});
		expect(params.get('q')).toBe('bex');
		expect(params.get('status')).toBe('active');
		expect(params.get('offset')).toBe('50');
	});

	it('does not carry an empty filter value', () => {
		expect(apiParams({ ...emptyQuery(KEYS), filters: { status: '' } }).has('status')).toBe(false);
	});
});

describe('isFiltered', () => {
	it('is false for an untouched query', () => {
		expect(isFiltered(emptyQuery(KEYS))).toBe(false);
	});

	it('is true for a search or a filter, but not for a page', () => {
		expect(isFiltered({ ...emptyQuery(KEYS), q: 'bex' })).toBe(true);
		expect(isFiltered({ ...emptyQuery(KEYS), filters: { status: 'active' } })).toBe(true);
		// Being on page 3 is not "filtered" — offering Clear there would suggest it undoes paging.
		expect(isFiltered({ ...emptyQuery(KEYS), offset: 100 })).toBe(false);
	});

	it('ignores a whitespace-only search', () => {
		expect(isFiltered({ ...emptyQuery(KEYS), q: '   ' })).toBe(false);
	});
});

describe('the range label', () => {
	it('reads as 1-based and thousands-separated', () => {
		expect(rangeLabel({ ...emptyQuery(KEYS), offset: 0 }, 1340, 50)).toBe('1–50 of 1,340');
	});

	it('counts from the offset on a later page', () => {
		expect(rangeLabel({ ...emptyQuery(KEYS), offset: 100 }, 1340, 50)).toBe('101–150 of 1,340');
	});

	it('handles a short last page', () => {
		expect(rangeLabel({ ...emptyQuery(KEYS), offset: 100 }, 112, 12)).toBe('101–112 of 112');
	});

	it('says No matches rather than 1–0 of 0', () => {
		expect(rangeLabel(emptyQuery(KEYS), 0, 0)).toBe('No matches');
	});
});

describe('the page a query is on', () => {
	it('is the offset in pages, 1-based — what the pager highlights', () => {
		expect(pageOf({ offset: 0, limit: 25 })).toBe(1);
		expect(pageOf({ offset: 25, limit: 25 })).toBe(2);
		expect(pageOf({ offset: 775, limit: 25 })).toBe(32);
	});

	it('rounds a mid-page offset down and survives a nonsense limit', () => {
		expect(pageOf({ offset: 30, limit: 25 })).toBe(2);
		expect(pageOf({ offset: -5, limit: 25 })).toBe(1);
		expect(pageOf({ offset: 10, limit: 0 })).toBe(11);
	});
});

describe('facet options', () => {
	it('maps the server values through the label function', () => {
		expect(
			optionsFromFacet({ status: ['active', 'in_progress'] }, 'status', (v) => v.replace('_', ' '))
		).toEqual([
			{ value: 'active', label: 'active' },
			{ value: 'in_progress', label: 'in progress' }
		]);
	});

	it('is empty rather than undefined for a facet the server did not send', () => {
		// An older API without facets must render an empty filter, not crash the listing.
		expect(optionsFromFacet(undefined, 'status')).toEqual([]);
		expect(optionsFromFacet({}, 'status')).toEqual([]);
	});
});

describe('itemsOf', () => {
	it('unwraps a paged envelope and leaves a bare list alone', () => {
		expect(itemsOf([{ id: 1 }])).toEqual([{ id: 1 }]);
		expect(itemsOf({ items: [{ id: 2 }], total: 9, facets: {} })).toEqual([{ id: 2 }]);
	});
});

describe('a RANGE filter, and the order a paged listing falls back to', () => {
	const KEYS = ['status', 'value'] as const;
	const R = ['value'] as const;
	const roundTrip = (v: Record<string, string[]>) => withBarValue(emptyQuery(KEYS), KEYS, v, R);

	it('survives the round-trip POSITIONALLY, empty slot and all', () => {
		// Every other filter is a SET, so the round-trip drops blanks — and doing that to a
		// `[from, to]` pair changes the question: `['', '3000']` is "up to 3,000", and dropping the
		// blank leaves `['3000']`, read at the far end as "from 3,000". `listing.clean_range` guards
		// the same edge in Python.
		const query = roundTrip({ value: ['', '3000'] });
		expect(query.filters.value).toEqual(['', '3000']);
		expect(paramsFromQuery(query).getAll('value')).toEqual(['', '3000']);
		expect(apiParams(query).getAll('value')).toEqual(['', '3000']);
		const back = queryFromParams(new URLSearchParams('value=&value=3000'), KEYS);
		expect(barValueFrom(back, KEYS, R).value).toEqual(['', '3000']);
		// …while a pair with nothing in it is no filter, and a non-range key cleans as ever.
		expect(roundTrip({ value: ['', ''] }).filters.value).toBeUndefined();
		expect(roundTrip({ status: ['', 'active'] }).filters.status).toEqual(['active']);
	});

	it('sends the listing default to the API but keeps it out of the URL', () => {
		// A server-paged listing holds 25 of a book of 400, so its fallback order must reach the API.
		const keys = ['status'];
		const query = queryFromParams(new URLSearchParams(''), keys, { sort: 'name', dir: 'asc' });
		expect([query.sort, query.dir]).toEqual(['name', 'asc']);
		expect(apiParams(query).get('sort')).toBe('name');
		expect(paramsFromQuery(query).toString()).toBe('');
		// …but a sort the reader chose is written, even on the default column, and the URL wins.
		expect(paramsFromQuery({ ...query, dir: 'desc' as const }).get('sort')).toBe('name');
		expect(
			queryFromParams(new URLSearchParams('sort=value'), keys, { sort: 'name', dir: 'asc' }).sort
		).toBe('value');
		expect(apiParams(queryFromParams(new URLSearchParams(''), keys)).has('sort')).toBe(false);
		expect(queryFromParams(new URLSearchParams(''), keys, 50).limit).toBe(50);
	});
});
