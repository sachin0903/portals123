/**
 * `nextUrl` — the half of "put the query in the URL" that is worth testing.
 *
 * Each listing used to build its own query string from the one control it owned, which is how a
 * search silently reset the filters beside it and how a "clear" left `?q=` behind looking like it
 * had not worked. The rules are small; the failures are all invisible.
 */
import { describe, expect, it } from 'vitest';
import { nextUrl, nextUrlFromStart } from './url';

const at = (search = '') => new URL(`https://portal.test/clients${search}`);

describe('nextUrl', () => {
	it('sets a param', () => {
		expect(nextUrl(at(), { q: 'halloway' }).search).toBe('?q=halloway');
	});

	it('KEEPS the params it was not asked about', () => {
		// The defect this replaces: rebuilding the query string from one control dropped every
		// other filter on the page, so searching cleared the status you had just chosen.
		const out = nextUrl(at('?status=active&adviser=u-7'), { q: 'smith' });
		expect(out.searchParams.get('status')).toBe('active');
		expect(out.searchParams.get('adviser')).toBe('u-7');
		expect(out.searchParams.get('q')).toBe('smith');
	});

	it('REMOVES a param set to empty, rather than leaving it blank', () => {
		// `?q=` and no `q` mean the same thing to every loader, so leaving the empty one behind
		// makes two URLs for one state — and a clear that leaves `?q=` looks like it failed.
		expect(nextUrl(at('?q=smith'), { q: '' }).search).toBe('');
		expect(nextUrl(at('?q=smith'), { q: null }).search).toBe('');
		expect(nextUrl(at('?q=smith'), { q: undefined }).search).toBe('');
	});

	it('treats whitespace as empty', () => {
		expect(nextUrl(at('?q=smith'), { q: '   ' }).search).toBe('');
	});

	it('trims what it keeps', () => {
		expect(nextUrl(at(), { q: '  smith  ' }).searchParams.get('q')).toBe('smith');
	});

	it('takes numbers', () => {
		expect(nextUrl(at(), { offset: 50 }).searchParams.get('offset')).toBe('50');
		// The NUMBER zero is the default offset — the first page — so it is dropped rather than
		// written out: `?offset=0` and no offset are the same page.
		expect(nextUrl(at('?offset=50'), { offset: 0 }).search).toBe('');
		// The STRING '0' is something somebody typed, and survives. Testing the type rather than
		// the rendered text is what keeps those apart.
		expect(nextUrl(at(), { q: '0' }).searchParams.get('q')).toBe('0');
	});

	it('does not mutate the URL it was given', () => {
		const before = at('?q=smith');
		nextUrl(before, { q: 'jones' });
		expect(before.searchParams.get('q')).toBe('smith');
	});

	it('leaves the path alone', () => {
		expect(nextUrl(at('?q=a'), { q: 'b' }).pathname).toBe('/clients');
	});
});

describe('nextUrlFromStart', () => {
	it('returns to the first page when the question changes', () => {
		// Searching from page 3 otherwise lands on page 3 of a different, shorter list — usually
		// empty, which reads as "no matches" for a search that matched plenty.
		const out = nextUrlFromStart(at('?offset=50&q=old'), { q: 'new' });
		expect(out.searchParams.get('q')).toBe('new');
		expect(out.searchParams.has('offset')).toBe(false);
	});

	it('clears a page param under either name', () => {
		expect(nextUrlFromStart(at('?page=4'), { q: 'x' }).searchParams.has('page')).toBe(false);
	});

	it('still keeps the other filters', () => {
		const out = nextUrlFromStart(at('?status=active&offset=25'), { q: 'x' });
		expect(out.searchParams.get('status')).toBe('active');
	});
});
