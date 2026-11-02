import { describe, expect, it } from 'vitest';
import { allocationBaseOf, allocationShare } from './allocation.js';

describe('allocationShare', () => {
	it('is the row’s share of the base', () => {
		expect(allocationShare(2500, 10000)).toBe(0.25);
	});

	// The reason the base is a parameter at all: the account record pages holdings 25 at a time,
	// so a share over the rows in hand would be a share of the page.
	it('is computed against the whole, not the rows on screen', () => {
		const page = [{ value: 300 }, { value: 200 }];
		const whole = [...page, { value: 500 }];
		expect(allocationShare(300, allocationBaseOf(page))).toBe(0.6);
		expect(allocationShare(300, allocationBaseOf(whole))).toBeCloseTo(0.3, 10);
	});

	it('says nothing rather than Infinity when there is no whole to divide by', () => {
		expect(allocationShare(100, 0)).toBeNull();
		expect(allocationShare(100, null)).toBeNull();
		expect(allocationShare(100, undefined)).toBeNull();
		expect(allocationShare(100, -5)).toBeNull();
		expect(allocationShare(100, Number.NaN)).toBeNull();
		expect(allocationShare(Number.NaN, 100)).toBeNull();
	});

	// A short or an overdrawn cash line is a real position with a real negative weight.
	it('keeps a negative weight against a positive whole', () => {
		expect(allocationShare(-1000, 10000)).toBe(-0.1);
	});
});

describe('allocationBaseOf', () => {
	it('sums the rows given', () => {
		expect(allocationBaseOf([{ value: 1 }, { value: 2.5 }, { value: 96.5 }])).toBe(100);
	});

	it('is zero for an empty set, which reads as "no base" downstream', () => {
		expect(allocationBaseOf([])).toBe(0);
		expect(allocationShare(0, allocationBaseOf([]))).toBeNull();
	});
});
