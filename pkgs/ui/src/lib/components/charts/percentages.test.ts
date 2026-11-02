import { describe, expect, it } from 'vitest';
import { allocationDisplay, roundPercentages } from './percentages.js';

describe('roundPercentages', () => {
	it('always sums to exactly 100 (the 33/33/33 legend lie)', () => {
		// Independent Math.round gave 33 + 33 + 33 = 99 here.
		expect(roundPercentages([1, 1, 1])).toEqual([34, 33, 33]);
		expect(roundPercentages([1, 1, 1]).reduce((s, v) => s + v, 0)).toBe(100);
	});

	it('hands the leftover points to the largest fractional remainders', () => {
		// Exact shares 40.5 / 29.75 / 29.75: floors 40+29+29=98, remainders .5/.75/.75 → the two
		// larger remainders take the two spare points.
		expect(roundPercentages([40.5, 29.75, 29.75])).toEqual([40, 30, 30]);
	});

	it('is deterministic on remainder ties (earlier index wins)', () => {
		expect(roundPercentages([1, 1])).toEqual([50, 50]);
		expect(roundPercentages([1, 1, 1, 1])).toEqual([25, 25, 25, 25]);
	});

	it('keeps exact splits exact', () => {
		expect(roundPercentages([64, 24, 7, 5])).toEqual([64, 24, 7, 5]);
	});

	it('returns all zeros for empty or all-zero input — no honest split of nothing', () => {
		expect(roundPercentages([])).toEqual([]);
		expect(roundPercentages([0, 0])).toEqual([0, 0]);
	});

	it('sums to 100 across awkward inputs (property sweep)', () => {
		const cases = [
			[3, 3, 3, 1],
			[1, 2, 4, 8, 16],
			[0.1, 0.1, 0.1],
			[99.4, 0.3, 0.3],
			[7, 11, 13, 17, 19, 23]
		];
		for (const values of cases) {
			expect(
				roundPercentages(values).reduce((s, v) => s + v, 0),
				String(values)
			).toBe(100);
		}
	});
});

describe('allocationDisplay', () => {
	const slice = (label: string, value: number) => ({ label, color: 'var(--brand)', value });

	it('excludes negative slices from the drawable set and reports the excluded £', () => {
		// Overdrawn cash: −5 of a Σall of 95 against a £95k book.
		const d = allocationDisplay(
			[slice('Equities', 70), slice('Bonds', 30), slice('Cash', -5)],
			95000
		);
		expect(d.slices.map((s) => s.label)).toEqual(['Equities', 'Bonds']);
		expect(d.excludedAny).toBe(true);
		// total × value / Σall: 95000 × −5 / 95 = −5000 — displayed + excluded reconcile to total.
		expect(d.excludedAmount).toBeCloseTo(-5000);
		const shown = d.slices.reduce((s, x) => s + (x.amount ?? 0), 0);
		expect(shown + (d.excludedAmount ?? 0)).toBeCloseTo(95000);
	});

	it('percentages are shares of the POSITIVE weights and sum to 100', () => {
		const d = allocationDisplay([slice('A', 1), slice('B', 1), slice('C', 1), slice('Cash', -1)]);
		expect(d.slices.map((s) => s.pct).reduce((s, v) => s + v, 0)).toBe(100);
	});

	it('never fabricates a £ figure without a total', () => {
		const d = allocationDisplay([slice('A', 60), slice('Cash', -10)]);
		expect(d.excludedAny).toBe(true);
		expect(d.excludedAmount).toBeNull();
		expect(d.slices[0]?.amount).toBeNull();
	});

	it('a clean all-positive book reports nothing excluded', () => {
		const d = allocationDisplay([slice('A', 60), slice('B', 40)], 1000);
		expect(d.excludedAny).toBe(false);
		expect(d.excludedAmount).toBeNull();
		expect(d.slices.map((s) => s.pct)).toEqual([60, 40]);
		expect(d.slices.map((s) => s.amount)).toEqual([600, 400]);
	});
});
