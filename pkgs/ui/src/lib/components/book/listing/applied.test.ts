import { describe, expect, it } from 'vitest';
import {
	appliedFilters,
	countFilters,
	capFromTo,
	handlesToPair,
	niceRange,
	rangeFromSlider,
	snapToTrack,
	sameFilters,
	withinRange,
	type FilterVocabulary
} from './applied.js';

// The arithmetic behind the applied-filter bar, the funnel's badge and the range slider track.

const opts = (...v: string[]) => v.map((x) => ({ value: x.toLowerCase(), label: x }));
const defs: FilterVocabulary[] = [
	{ key: 'status', label: 'Status', options: opts('Active', 'Onboarding') },
	{ key: 'adviser', label: 'Adviser', options: opts('Cole', 'Shaw', 'Vale', 'Pike') },
	{ key: 'd', label: 'Date', options: [], kind: 'daterange' },
	{ key: 'a', label: 'Amount', options: [], kind: 'numberrange' }
];
const say = (v: Record<string, string[]>, extra: Partial<FilterVocabulary> = {}) =>
	appliedFilters(
		defs.map((d) => (d.key === 'a' ? { ...d, ...extra } : d)),
		v
	)[0]?.text;

describe('the bar and the badge', () => {
	it('compares filter maps as sets, and never sorts the caller’s array in place', () => {
		expect(sameFilters({ s: ['a', 'b'] }, { s: ['b', 'a'] })).toBe(true);
		// But a RANGE is positional: sorting made "from 3,000" and "up to 3,000" identical, so
		// moving the figure from one end to the other looked like no change and never saved.
		expect(sameFilters({ v: ['3000', ''] }, { v: ['', '3000'] })).toBe(false);
		expect(sameFilters({ v: ['', '3000'] }, { v: ['', '3000'] })).toBe(true);
		expect(sameFilters({ a: ['1'], b: ['2'] }, { b: ['2'], a: ['1'] })).toBe(true);
		expect(sameFilters({ status: [] }, {})).toBe(true); // a URL hands over empty keys
		expect(sameFilters({ s: ['a'] }, { s: ['a', 'b'] })).toBe(false);
		expect(sameFilters({ s: ['a'] }, { t: ['a'] })).toBe(false);
		const held = { s: ['b', 'a'] }; // sorted in place, this reorders the draft being edited
		sameFilters(held, { s: ['a', 'b'] });
		expect(held.s).toEqual(['b', 'a']);
	});

	it('counts FILTERS, not values, and not a range whose bounds cannot be read', () => {
		// The badge said "1 applied" over a bar with no chip: a range key holds what was TYPED, and
		// the bar refuses to print a bound it cannot parse.
		expect(countFilters({ status: ['active', 'onboarding'] })).toBe(1);
		expect(countFilters({ status: [] })).toBe(0);
		expect(countFilters({ a: ['abc', ''] }, defs)).toBe(0);
		expect(countFilters({ a: ['', '3000'] }, defs)).toBe(1);
	});

	it('names each filter by its option labels, in the listing’s order, counting the overflow', () => {
		expect(appliedFilters(defs, { adviser: ['cole'], status: ['active'] })).toEqual([
			{ key: 'status', label: 'Status', text: 'Active' },
			{ key: 'adviser', label: 'Adviser', text: 'Cole' }
		]);
		expect(say({ adviser: ['cole', 'shaw', 'vale', 'pike'] })).toBe('Cole, Shaw +2');
		// A server-driven listing holds a value out of the URL before its facets have landed.
		expect(say({ status: ['archived'] })).toBe('archived');
		expect(appliedFilters(defs, { status: [], gone: ['x'] })).toEqual([]);
	});

	it('prints either range as ONE chip, naming the open end, ignoring a bound that is not one', () => {
		// The bounds come out of a URL a person can hand-edit: a typo must narrow nothing, not NaN.
		expect(say({ d: ['2026-09-13', '2026-09-20'] })).toBe('13 Sept 2026 – 20 Sept 2026');
		expect(say({ d: ['2026-09-13', ''] })).toBe('from 13 Sept 2026');
		expect(say({ d: ['', '2026-09-20'] })).toBe('until 20 Sept 2026');
		expect(say({ a: ['50', '300000'] })).toBe('50 – 300,000');
		expect(say({ a: ['50', ''] })).toBe('from 50');
		expect(say({ a: ['', '3000'] })).toBe('up to 3,000');
		expect(say({ a: ['50', '3000'] }, { currency: 'GBP' })).toBe('£50.00 – £3,000.00');
		expect(say({ a: ['0.5', '2'] }, { unit: '%' })).toBe('0.5% – 2%');
		expect(appliedFilters(defs, { d: ['', ''], a: ['abc', ''] })).toEqual([]);
		expect(countFilters({ d: ['2026-09-13', '2026-09-20'] }, defs)).toBe(1);
	});

	it('takes both ends of a numberrange inclusively, and ignores a bound that is not a number', () => {
		expect(withinRange(5, ['', ''])).toBe(true);
		expect(withinRange(5, ['0', '5'])).toBe(true);
		expect(withinRange(5.01, ['0', '5'])).toBe(false);
		expect(withinRange(1000, ['500', ''])).toBe(true);
		expect(withinRange(100, ['500', ''])).toBe(false);
		expect(withinRange(5, ['abc', ''])).toBe(true);
		expect(withinRange(Number.NaN, ['0', '5'])).toBe(false);
	});
});

describe('the slider track', () => {
	it('rounds the top UP to two significant figures, and gives a step that divides it exactly', () => {
		// Straight from the data the track ends just below the largest row, so dragging the handle
		// to the end silently drops the biggest clients; and the remainder of a step that does not
		// divide the span is the same gap, one layer down.
		expect(niceRange(674_134, 1_975_910)?.max).toBe(2_000_000);
		expect(niceRange(0, 5_499_999)?.max).toBe(5_500_000);
		expect(niceRange(0, 631)?.max).toBe(640);
		expect(niceRange(674_134, 1_975_910)?.min).toBe(0); // non-negative data starts at zero
		expect(niceRange(-1234, 5000)?.min).toBe(-1300);
		expect(niceRange(5, 5)).toBeNull();
		expect(niceRange(Number.NaN, 10)).toBeNull();
		for (const [lo, hi] of [
			[674_134, 1_975_910],
			[-1234, 5000]
		]) {
			const t = niceRange(lo, hi)!;
			expect(t.min + 100 * t.step).toBeCloseTo(t.max, 6);
		}
	});

	it('reads a handle within half a step of an end as no bound at all', () => {
		// Otherwise the last stop before the end is a bound nobody chose, and the only way back is
		// emptying the box by hand.
		const track = { min: 0, max: 2_000_000, step: 20_000 };
		expect(handlesToPair([0, 2_000_000], track)).toEqual([]);
		expect(handlesToPair([9_000, 1_991_000], track)).toEqual([]);
		expect(handlesToPair([0, 500_000], track)).toEqual(['', '500000']);
		expect(handlesToPair([500_000, 2_000_000], track)).toEqual(['500000', '']);
		expect(handlesToPair([20_000, 1_980_000], track)).toEqual(['20000', '1980000']);
		expect(handlesToPair([1_234_567.891, 1_600_000], track)).toEqual(['1234568', '1600000']);
		expect(handlesToPair([0.25, 0.75], { min: 0, max: 1, step: 0.01 })).toEqual(['0.25', '0.75']);
	});

	it('keeps an exact amount the track would snap onto its end', () => {
		// A hundred-thousand span steps by 1,000, so half a step is 500. 100 snaps to 0 and
		// `handlesToPair` then says there is no bound — the field clears. 1,000 is itself a stop,
		// which is why that one used to survive the same write.
		const track = niceRange(0, 100_000)!;
		expect(track.step).toBe(1_000);
		expect(snapToTrack(100, track)).toBe(0);
		expect(snapToTrack(500, track)).toBe(0); // a tie belongs to the lower stop
		expect(snapToTrack(501, track)).toBe(1_000);
		expect(snapToTrack(1_000, track)).toBe(1_000);
		expect(handlesToPair([snapToTrack(100, track), track.max], track)).toEqual([]);
		// The snap of 100 is the low end. That write must not clear the box.
		expect(rangeFromSlider([0, track.max], ['100', ''], track)).toBeNull();
		expect(rangeFromSlider([1_000, track.max], ['1000', ''], track)).toBeNull();
		expect(rangeFromSlider([5_000, track.max], ['100', ''], track)).toEqual(['5000', '']);
		expect(rangeFromSlider([0, track.max], ['0', ''], track)).toBeNull();
		// Dragging the minimum back to the end clears it. Dragging only the maximum must not
		// take the exact minimum with it.
		expect(rangeFromSlider([0, track.max], ['5000', ''], track)).toEqual([]);
		expect(rangeFromSlider([0, 50_000], ['100', ''], track)).toEqual(['100', '50000']);
	});

	it('caps from at a to that is already set', () => {
		expect(capFromTo(['2500', '1000'])).toEqual(['1000', '1000']);
		expect(capFromTo(['400', '1000'])).toEqual(['400', '1000']);
		expect(capFromTo(['1000', '1000'])).toEqual(['1000', '1000']);
		expect(capFromTo(['1000.5', '1000'])).toEqual(['1000', '1000']);
		// No ceiling yet: from is whatever was typed. An empty from is not invented.
		expect(capFromTo(['2500', ''])).toEqual(['2500', '']);
		expect(capFromTo(['', '1000'])).toEqual(['', '1000']);
		expect(capFromTo(['', ''])).toEqual([]);
		// A lower To pulls From down with it, so the ceiling still holds.
		expect(capFromTo(['500', '100'])).toEqual(['100', '100']);
		const track = niceRange(0, 100_000)!;
		expect(rangeFromSlider([8_000, 5_000], ['100', '5000'], track)).toEqual(['5000', '5000']);
	});
});
