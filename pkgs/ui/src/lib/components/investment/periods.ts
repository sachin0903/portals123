/**
 * Period selection for the portfolio headers — extracted from portfolio-header.svelte's module
 * script so all three big looks (hero, canvas, ledger) share ONE implementation of the period
 * keys, the series slicing and the change-sign logic instead of re-deriving them per variant.
 * Pure and dependency-free on purpose: unit-tested directly, imported by .svelte files.
 */
import type { PerformancePoint } from '../charts/performance-chart.svelte';

export const periodKeys = ['1M', '3M', '6M', '1Y'] as const;
export type PeriodKey = (typeof periodKeys)[number];

const PERIOD_MONTHS: Record<PeriodKey, number> = { '1M': 1, '3M': 3, '6M': 6, '1Y': 12 };

/** The inline label a header prints next to the change figure ("· past 6 months"). */
export const PERIOD_LABELS: Record<PeriodKey, string> = {
	'1M': 'past month',
	'3M': 'past 3 months',
	'6M': 'past 6 months',
	'1Y': 'past year'
};

/** The adjective form the chart's aria-label leads with ("6-month performance: …"). */
/** The heading over a header's period change ("Last year" above "+£137,514.44"). */
export const PERIOD_TITLES: Record<PeriodKey, string> = {
	'1M': 'Last month',
	'3M': 'Last 3 months',
	'6M': 'Last 6 months',
	'1Y': 'Last year'
};

export const PERIOD_DESCRIPTIONS: Record<PeriodKey, string> = {
	'1M': '1-month',
	'3M': '3-month',
	'6M': '6-month',
	'1Y': '1-year'
};

/**
 * A stored `default_period` setting, defensively normalised. Settings ride the wire as untyped
 * JSON, so junk — a stale value, a hand-edited theme, a number — must degrade to the historic
 * default ('6M') rather than crash or seed an impossible period.
 */
export function normalisePeriod(raw: unknown): PeriodKey {
	return typeof raw === 'string' && (periodKeys as readonly string[]).includes(raw)
		? (raw as PeriodKey)
		: '6M';
}

// Strict ISO check on purpose: `Date.parse` on a bare month name ("May") is engine-dependent,
// so "does this label parse" alone would make the demo/real split flaky across platforms.
const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

/**
 * Slice a performance series down to the selected period.
 *
 * Real series from the API label their points with ISO dates, so the slice is by DATE: the
 * `period` months ending at the series' LAST point — a daily series' "1M" is ~30 points, a
 * weekly one's ~4. The window is anchored on the series' end rather than on the clock because
 * the series is the custodian's, not today's: a book whose feed stopped a year ago still has a
 * last year of data, and the header beside this labels the window's real basis ("to 25 Sept
 * 2025"). Anchored on `now`, that same series fell entirely outside every window and the hero
 * printed "£0.00 (0.0%)" over a blank chart for a client whose book had gained 7% — a silent
 * zero, which reads as a fact. `now` only caps the anchor: a point dated in the future (a clock
 * skew, a forward-dated valuation) does not drag the window past today.
 *
 * The count fallback exists only for undated labels (the demo fixture's bare month names),
 * where the original hardcoded point counts — calibrated to that 8-point monthly series —
 * remain the best guess available.
 */
export function slicePeriod(
	performance: PerformancePoint[],
	period: PeriodKey,
	now: Date = new Date()
): PerformancePoint[] {
	const dated =
		performance.length > 0 &&
		performance.every((p) => ISO_DATE.test(p.label) && !Number.isNaN(Date.parse(p.label)));
	if (dated) {
		const end = Math.min(now.getTime(), Math.max(...performance.map((p) => Date.parse(p.label))));
		const cutoff = new Date(end);
		cutoff.setMonth(cutoff.getMonth() - PERIOD_MONTHS[period]);
		const cut = cutoff.getTime();
		return performance.filter((p) => Date.parse(p.label) >= cut);
	}
	// Undated labels: the demo's 8 monthly points — the historical count heuristic.
	const counts: Record<PeriodKey, number> = {
		'1M': 2,
		'3M': 4,
		'6M': 7,
		'1Y': performance.length
	};
	return performance.slice(Math.max(0, performance.length - counts[period]));
}

/**
 * Three-state sign for a change figure. Zero is its own state: a flat (or day-one) portfolio
 * rendered with a green up-arrow and "+£0.00" claims a gain that never happened.
 */
export type ChangeDirection = 'up' | 'down' | 'flat';
export function changeDirection(change: number): ChangeDirection {
	if (change > 0) return 'up';
	if (change < 0) return 'down';
	return 'flat';
}

/** On-hero ink for a change figure — shared by the three big looks so "what colour is a loss"
 * is answered once. Neutral for flat: no change is not a gain. */
export const DIRECTION_COLOR: Record<ChangeDirection, string> = {
	up: 'var(--hero-positive)',
	down: 'var(--hero-negative)',
	flat: 'var(--hero-muted)'
};
