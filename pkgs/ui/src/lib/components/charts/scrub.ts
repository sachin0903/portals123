/**
 * Pure geometry + labelling for the performance chart's scrubber, kept out of the component so
 * point-snapping and the accessible summary are unit-testable without a layout engine.
 */
import type { PerformancePoint } from './performance-chart.svelte';

/**
 * Snap a pointer x (in the chart's own coordinate space) to the nearest data-point index.
 * `pad` is the chart's horizontal inset — points span [pad, width − pad]. Out-of-range x
 * clamps to the first/last point (dragging past the edge holds the endpoint rather than
 * dropping the readout); a degenerate chart (no points, no width) returns null.
 */
export function nearestIndex(x: number, width: number, count: number, pad = 0): number | null {
	if (count <= 0 || !Number.isFinite(x) || !Number.isFinite(width) || width <= 0) return null;
	if (count === 1) return 0;
	const usable = width - pad * 2;
	if (usable <= 0) return 0;
	const frac = (x - pad) / usable;
	return Math.min(count - 1, Math.max(0, Math.round(frac * (count - 1))));
}

/**
 * Where the readout chip sits, as a fraction of the chart width — the point's own position,
 * clamped in from the edges so a chip over the first or last point doesn't hang outside the
 * card (it is centre-anchored via translateX(-50%)).
 */
export function chipFraction(pointFraction: number, inset = 0.08): number {
	if (!Number.isFinite(pointFraction)) return inset;
	return Math.min(1 - inset, Math.max(inset, pointFraction));
}

// Whole pounds for the spoken summary: a screen reader announcing ", point zero zero" after
// every figure buries the movement in noise. The on-screen chip keeps exact pence.
const GBP_WHOLE = new Intl.NumberFormat('en-GB', {
	style: 'currency',
	currency: 'GBP',
	maximumFractionDigits: 0
});

/**
 * The chart's honest aria-label: period, start/end values and the movement over the window —
 * e.g. "6-month performance: £158,960 to £171,320, up £12,360". Flat says "unchanged": a
 * summary must not spin no movement as either direction.
 */
export function chartAriaLabel(description: string, data: PerformancePoint[]): string {
	if (data.length === 0) return `${description} performance: no data yet`;
	const first = data[0]!.value;
	const last = data[data.length - 1]!.value;
	const diff = last - first;
	const movement =
		diff > 0
			? `up ${GBP_WHOLE.format(diff)}`
			: diff < 0
				? `down ${GBP_WHOLE.format(-diff)}`
				: 'unchanged';
	return `${description} performance: ${GBP_WHOLE.format(first)} to ${GBP_WHOLE.format(last)}, ${movement}`;
}
