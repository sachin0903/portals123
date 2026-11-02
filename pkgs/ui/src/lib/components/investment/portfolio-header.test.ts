import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { PerformancePoint } from '../charts/performance-chart.svelte';
import { performance as demoPerformance } from '../../fixtures/demo.js';
import { changeDirection, normalisePeriod, slicePeriod } from './periods.js';
import PortfolioHeader from './portfolio-header.svelte';
import PortfolioHeaderCompact from './portfolio-header-compact.svelte';

describe('slicePeriod', () => {
	// A real API series: daily points, ISO-dated labels, 90 days ending 30 Jul 2026.
	const daily: PerformancePoint[] = Array.from({ length: 90 }, (_, i) => {
		const d = new Date(2026, 6, 30); // 30 Jul 2026, local
		d.setDate(d.getDate() - (89 - i));
		const pad = (n: number) => String(n).padStart(2, '0');
		return {
			label: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`,
			value: 100000 + i * 10
		};
	});
	const now = new Date(2026, 6, 30, 12);

	it('slices an ISO-dated series by DATE — a daily 1M is ~30 points, not 2', () => {
		const sliced = slicePeriod(daily, '1M', now);
		// The old hardcoded count (calibrated to the 8-point monthly demo) returned 2 points.
		expect(sliced.length).toBeGreaterThanOrEqual(29);
		expect(sliced.length).toBeLessThanOrEqual(32);
		// The window ends at the latest point and every label is on or after the cutoff.
		expect(sliced.at(-1)!.label).toBe('2026-07-30');
		expect(sliced.every((p) => p.label >= '2026-06-29')).toBe(true);
	});

	it('windows grow with the period and 1Y keeps everything in range', () => {
		const m1 = slicePeriod(daily, '1M', now).length;
		const m3 = slicePeriod(daily, '3M', now).length;
		const y1 = slicePeriod(daily, '1Y', now).length;
		expect(m1).toBeLessThan(m3);
		// The 90-day series sits entirely inside the past year.
		expect(y1).toBe(daily.length);
	});

	it('falls back to the count heuristic for undated labels (the demo series, unchanged)', () => {
		expect(slicePeriod(demoPerformance, '1M')).toEqual(demoPerformance.slice(-2));
		expect(slicePeriod(demoPerformance, '3M')).toEqual(demoPerformance.slice(-4));
		expect(slicePeriod(demoPerformance, '6M')).toEqual(demoPerformance.slice(-7));
		expect(slicePeriod(demoPerformance, '1Y')).toEqual(demoPerformance);
	});

	it('a single undated label anywhere sends the whole series down the count path', () => {
		const mixed = [...daily.slice(0, 5), { label: 'Jun', value: 1 }];
		expect(slicePeriod(mixed, '1M', now)).toHaveLength(2);
	});

	it('handles an empty series', () => {
		expect(slicePeriod([], '1M', now)).toEqual([]);
	});

	it('anchors the window on the series end, so a stale feed still shows its last period', () => {
		// The same 90-day series read a year after its last point. Anchored on the clock, every
		// window was empty and the hero printed "£0.00 (0.0%)" over a blank chart — a silent zero
		// for a book that had gained. The header labels the real basis ("to 30 Jul 2026") beside it.
		const aYearLater = new Date(2027, 6, 30, 12);
		expect(slicePeriod(daily, '1Y', aYearLater)).toHaveLength(daily.length);
		const m1 = slicePeriod(daily, '1M', aYearLater);
		expect(m1.length).toBeGreaterThanOrEqual(29);
		expect(m1.at(-1)!.label).toBe('2026-07-30');
		// And the same call on the day of the last point is unchanged by the anchoring.
		expect(slicePeriod(daily, '1M', aYearLater)).toEqual(slicePeriod(daily, '1M', now));
	});

	it('never anchors past the clock: a forward-dated point does not drag the window into the future', () => {
		const skewed = [...daily, { label: '2027-01-01', value: 1 }];
		// Window = the month up to `now` (30 Jul 2026); the future point is still inside it
		// (it is after the cutoff), but the cutoff itself is measured from today, not from 2027.
		const sliced = slicePeriod(skewed, '1M', now);
		expect(sliced[0].label >= '2026-06-29').toBe(true);
		expect(sliced.length).toBeGreaterThanOrEqual(30);
	});
});

describe('changeDirection', () => {
	it('is three-state: zero is flat, not up', () => {
		expect(changeDirection(120.5)).toBe('up');
		expect(changeDirection(-3)).toBe('down');
		expect(changeDirection(0)).toBe('flat');
	});
});

describe('normalisePeriod', () => {
	it('keeps a real period key', () => {
		expect(normalisePeriod('1M')).toBe('1M');
		expect(normalisePeriod('1Y')).toBe('1Y');
	});

	it("junk degrades to the historic default '6M' — settings are untyped wire JSON", () => {
		expect(normalisePeriod('2W')).toBe('6M');
		expect(normalisePeriod('')).toBe('6M');
		expect(normalisePeriod(6)).toBe('6M');
		expect(normalisePeriod(null)).toBe('6M');
		expect(normalisePeriod(undefined)).toBe('6M');
	});
});

describe('PortfolioHeader (SSR)', () => {
	const flatSeries: PerformancePoint[] = Array.from({ length: 8 }, (_, i) => ({
		label: ['Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun'][i]!,
		value: 100000
	}));

	it('renders a flat portfolio neutral: unsigned £0.00, no direction arrow', () => {
		const { body } = render(PortfolioHeader, {
			props: { total: 100000, gain: 0, gainPct: 0, performance: flatSeries }
		});
		expect(body).toMatch(/£0<span[^>]*>\.00<\/span><\/span>(<!--[^>]*-->)*\s*\(0\.0%\)/);
		// A green "+£0.00 (+0.0%)" presented no change as a gain.
		expect(body).not.toContain('+£0');
		expect(body).not.toContain('lucide-arrow-up-right');
		expect(body).not.toContain('lucide-arrow-down-right');
	});

	it('still arrows a genuine gain', () => {
		const { body } = render(PortfolioHeader, {
			props: { total: 195690, gain: 12000, gainPct: 0.065, performance: demoPerformance }
		});
		expect(body).toContain('lucide-arrow-up-right');
		expect(body).toContain('+£');
	});

	it("names the footer stat 'Growth since investment', not 'Return'", () => {
		const { body } = render(PortfolioHeader, {
			props: { total: 195690, gain: 12000, gainPct: 0.065, performance: demoPerformance }
		});
		expect(body).toContain('Growth since investment');
		expect(body).not.toMatch(/\bReturn\b/);
	});

	it('omits Invested when withdrawals push total − gain negative', () => {
		const { body } = render(PortfolioHeader, {
			props: { total: 1000, gain: 2000, gainPct: 2, performance: demoPerformance }
		});
		expect(body).not.toContain('Invested');
		expect(body).toContain('Growth since investment');
	});

	it('shows Invested when the basis is non-negative', () => {
		const { body } = render(PortfolioHeader, {
			props: { total: 195690, gain: 12000, gainPct: 0.065, performance: demoPerformance }
		});
		expect(body).toContain('Invested');
	});
});

describe('PortfolioHeaderCompact (SSR)', () => {
	it('renders a zero gain neutral: unsigned £0.00, no arrow in the pill', () => {
		const { body } = render(PortfolioHeaderCompact, {
			props: { total: 100000, gain: 0, gainPct: 0 }
		});
		// The pence sit in their own muted span now, so the pill reads "£0" + ".00" + " (0.0%)".
		expect(body).toMatch(/£0<span[^>]*>\.00<\/span><\/span>(<!--[^>]*-->)*\s*\(0\.0%\)/);
		expect(body).not.toContain('+£0');
		expect(body).not.toContain('lucide-arrow-up-right');
		expect(body).not.toContain('lucide-arrow-down-right');
	});

	it('keeps the signed arrowed pill for real movement', () => {
		const up = render(PortfolioHeaderCompact, {
			props: { total: 100000, gain: 2610, gainPct: 0.027 }
		}).body;
		expect(up).toContain('lucide-arrow-up-right');
		expect(up).toMatch(/\+£2,610<span[^>]*>\.00<\/span>/);
		const down = render(PortfolioHeaderCompact, {
			props: { total: 100000, gain: -2610, gainPct: -0.025 }
		}).body;
		expect(down).toContain('lucide-arrow-down-right');
	});
});
