/**
 * The listing toolbars' URL round-trip ($lib/listing-url.ts) — the parse half feeds the server
 * loaders AND composed-page's listingQuery, the build half is what onlistingchange navigates
 * to, so both rules are pinned here where they live together.
 */
import { describe, expect, it } from 'vitest';
import { listingHref, listingQueryOf } from './listing-url';

const at = (pathAndQuery: string) => new URL(`http://portal.test${pathAndQuery}`);

describe('listingQueryOf', () => {
	it('parses q and account off the URL', () => {
		expect(listingQueryOf(at('/holdings?q=bond&account=General+ISA'))).toEqual({
			q: 'bond',
			account: 'General ISA'
		});
	});

	it('junk — absent, blank, whitespace — reads as unfiltered (absent keys)', () => {
		expect(listingQueryOf(at('/holdings'))).toEqual({ q: undefined, account: undefined });
		expect(listingQueryOf(at('/holdings?q=&account='))).toEqual({
			q: undefined,
			account: undefined
		});
		expect(listingQueryOf(at('/holdings?q=%20%20'))).toEqual({ q: undefined, account: undefined });
	});

	it('trims and caps an over-long q at the API’s 80-char clamp', () => {
		const long = 'x'.repeat(200);
		expect(listingQueryOf(at(`/holdings?q=${long}`)).q).toBe('x'.repeat(80));
		expect(listingQueryOf(at('/holdings?q=%20bond%20')).q).toBe('bond');
	});
});

describe('listingHref', () => {
	it('writes q/account onto the same path, preserving unrelated params', () => {
		expect(listingHref(at('/holdings?theme=dark'), { q: 'bond', account: 'Pension' })).toBe(
			'/holdings?theme=dark&q=bond&account=Pension'
		);
	});

	it('RESETS ?page= — a new query invalidates the old page number', () => {
		// Absent IS page 1 to clampPage, so the param is removed rather than set to 1.
		expect(listingHref(at('/holdings?page=3&q=old'), { q: 'bond' })).toBe('/holdings?q=bond');
	});

	it('drops empties — a cleared search leaves no ?q= husk', () => {
		expect(listingHref(at('/holdings?q=bond&account=Pension'), { account: 'Pension' })).toBe(
			'/holdings?account=Pension'
		);
		expect(listingHref(at('/holdings?q=bond'), { q: '   ' })).toBe('/holdings');
	});

	it('returns the bare path when nothing remains', () => {
		expect(listingHref(at('/activity?q=fee&page=2'), {})).toBe('/activity');
	});
});
