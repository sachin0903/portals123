import { describe, expect, it } from 'vitest';
import { allocationSegments } from './allocation';

const alloc = (n: number) =>
	Array.from({ length: n }, (_, i) => ({ label: `class ${i}`, value: (i + 1) * 100 }));

describe('allocationSegments', () => {
	it('states the whole portfolio: the percentages add up to 100', () => {
		// The donut used to drop everything past the sixth class, so a ring that looked like the
		// whole portfolio silently described part of it.
		for (const n of [1, 3, 5, 6, 9]) {
			const segs = allocationSegments(alloc(n));
			const sum = segs.reduce((s, x) => s + x.pct, 0);
			expect(sum, `${n} classes`).toBeCloseTo(100, 6);
		}
	});

	it('buckets the tail into "Other" only when there is a tail', () => {
		expect(allocationSegments(alloc(5)).map((s) => s.label)).not.toContain('Other');
		const nine = allocationSegments(alloc(9));
		expect(nine).toHaveLength(6);
		expect(nine.at(-1)!.label).toBe('Other');
		// Other carries the sum of classes 6..9, not just one of them.
		expect(nine.at(-1)!.value).toBe(600 + 700 + 800 + 900);
	});

	it('lays the ring out end to end, starting at twelve o’clock', () => {
		const segs = allocationSegments([
			{ label: 'a', value: 75 },
			{ label: 'b', value: 25 }
		]);
		expect(segs[0]).toMatchObject({ pct: 75, offset: 25 });
		expect(segs[1]).toMatchObject({ pct: 25, offset: 25 - 75 });
	});

	it('survives an empty or zero-valued allocation without dividing by zero', () => {
		expect(allocationSegments([])).toEqual([]);
		const zero = allocationSegments([{ label: 'a', value: 0 }]);
		expect(zero[0].pct).toBe(0);
	});
});
