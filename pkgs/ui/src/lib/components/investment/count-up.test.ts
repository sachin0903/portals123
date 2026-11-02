import { describe, expect, it } from 'vitest';
import { formatGBP } from '../../money.js';
import { COUNT_UP_FROM, countUpAt } from './count-up.js';

describe('countUpAt', () => {
	it('lands EXACTLY on the target at t=1 — the formatter must print the true figure', () => {
		expect(countUpAt(171_320, 1)).toBe(171_320);
		expect(formatGBP(countUpAt(171_320, 1))).toBe('£171,320.00');
		// A value whose 90% is a recurring float must still land exact.
		expect(countUpAt(0.1, 1)).toBe(0.1);
	});

	it('starts at 90% of the value — order of magnitude correct from the first frame', () => {
		expect(countUpAt(100_000, 0)).toBe(100_000 * COUNT_UP_FROM);
	});

	it('is monotone through the ramp', () => {
		expect(countUpAt(100_000, 0.5)).toBeGreaterThan(countUpAt(100_000, 0.25));
		expect(countUpAt(100_000, 0.5)).toBeLessThan(100_000);
	});

	it('clamps t outside [0, 1] — a tween overshoot must not overshoot the money', () => {
		expect(countUpAt(100_000, 1.2)).toBe(100_000);
		expect(countUpAt(100_000, -0.5)).toBe(90_000);
	});

	it('handles zero and negative totals (a fully-withdrawn or overdrawn book)', () => {
		expect(countUpAt(0, 0.5)).toBe(0);
		expect(countUpAt(-500, 1)).toBe(-500);
	});
});
