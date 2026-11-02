import { describe, expect, it } from 'vitest';
import { chartAriaLabel, chipFraction, nearestIndex } from './scrub.js';

describe('nearestIndex', () => {
	// A 100-wide chart, pad 4: points span x = 4 … 96.
	const W = 100;
	const PAD = 4;

	it('snaps to the nearest point, not the floor', () => {
		// 5 points at x = 4, 27, 50, 73, 96.
		expect(nearestIndex(4, W, 5, PAD)).toBe(0);
		expect(nearestIndex(17, W, 5, PAD)).toBe(1); // past the 0/1 midpoint (15.5)
		expect(nearestIndex(50, W, 5, PAD)).toBe(2);
		expect(nearestIndex(60, W, 5, PAD)).toBe(2); // before the 2/3 midpoint (61.5)
		expect(nearestIndex(96, W, 5, PAD)).toBe(4);
	});

	it('clamps out-of-range x to the endpoints — dragging past the edge holds the end', () => {
		expect(nearestIndex(-50, W, 5, PAD)).toBe(0);
		expect(nearestIndex(500, W, 5, PAD)).toBe(4);
	});

	it('a single point is always index 0', () => {
		expect(nearestIndex(90, W, 1, PAD)).toBe(0);
	});

	it('degenerate inputs return null (no points, no width, NaN)', () => {
		expect(nearestIndex(50, W, 0, PAD)).toBeNull();
		expect(nearestIndex(50, 0, 5, PAD)).toBeNull();
		expect(nearestIndex(Number.NaN, W, 5, PAD)).toBeNull();
	});
});

describe('chipFraction', () => {
	it('passes a mid-chart position through', () => {
		expect(chipFraction(0.5)).toBe(0.5);
	});

	it('insets the edges so a centre-anchored chip stays inside the card', () => {
		expect(chipFraction(0)).toBe(0.08);
		expect(chipFraction(1)).toBe(0.92);
	});

	it('junk lands at the leading inset rather than NaN-positioned CSS', () => {
		expect(chipFraction(Number.NaN)).toBe(0.08);
	});
});

describe('chartAriaLabel', () => {
	it('carries period, start/end values and the movement — whole pounds, no pence noise', () => {
		expect(
			chartAriaLabel('6-month', [
				{ label: '2026-01-29', value: 158_960 },
				{ label: '2026-07-29', value: 171_320 }
			])
		).toBe('6-month performance: £158,960 to £171,320, up £12,360');
	});

	it('says down for a loss and unchanged for flat — never spins no movement', () => {
		expect(
			chartAriaLabel('1-month', [
				{ label: 'a', value: 200 },
				{ label: 'b', value: 150 }
			])
		).toBe('1-month performance: £200 to £150, down £50');
		expect(
			chartAriaLabel('1-month', [
				{ label: 'a', value: 200 },
				{ label: 'b', value: 200 }
			])
		).toBe('1-month performance: £200 to £200, unchanged');
	});

	it('states honestly when there is nothing to describe', () => {
		expect(chartAriaLabel('1-year', [])).toBe('1-year performance: no data yet');
	});
});
