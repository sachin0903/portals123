/**
 * Percentage labelling for the allocation views (donut + bars) — pure, shared, unit-tested.
 *
 * Two long-standing lies fixed here:
 *  - Independent `Math.round` per slice let a legend read 33 + 33 + 33 = 99% (or 101%);
 *    largest-remainder rounding guarantees the printed figures sum to exactly 100.
 *  - A negative slice (overdrawn cash is real data) fed d3's pie/bar geometry garbage — arcs that
 *    overlap or vanish, a bar with negative width. Negative slices are excluded from the drawn
 *    geometry and disclosed in a footnote instead of being drawn wrongly.
 */

/**
 * Round non-negative weights to integer percentages that sum to EXACTLY 100 (largest-remainder /
 * Hamilton method): floor every share, then hand the leftover points to the largest fractional
 * remainders (ties broken by earlier index, so the result is deterministic). All-zero (or empty)
 * input returns all zeros — there is no honest split of nothing.
 */
export function roundPercentages(values: number[]): number[] {
	const sum = values.reduce((s, v) => s + v, 0);
	if (!(sum > 0)) return values.map(() => 0);
	const exact = values.map((v) => (v / sum) * 100);
	const floored = exact.map(Math.floor);
	let leftover = 100 - floored.reduce((s, v) => s + v, 0);
	// Indices by descending fractional remainder; stable sort keeps earlier indices first on ties.
	const byRemainder = exact
		.map((v, i) => ({ i, rem: v - Math.floor(v) }))
		.sort((a, b) => b.rem - a.rem);
	for (const { i } of byRemainder) {
		if (leftover <= 0) break;
		floored[i] = (floored[i] ?? 0) + 1;
		leftover -= 1;
	}
	return floored;
}

/** What both allocation views consume: label + colour + weight. */
export interface AllocationInput {
	label: string;
	color: string;
	value: number;
}

/** One displayable slice: an integer percentage (the set sums to 100) + optional £ amount. */
export interface AllocationDisplaySlice extends AllocationInput {
	pct: number;
	amount: number | null;
}

export interface AllocationDisplay {
	/** The drawable slices — non-negative weights only, in input order. */
	slices: AllocationDisplaySlice[];
	/** Σ £ of the excluded negative slices (a NEGATIVE number), or null when nothing was
	 * excluded / no honest £ figure exists. Feeds the "Excludes negative balances (−£X)"
	 * footnote. */
	excludedAmount: number | null;
	/** True when any negative slice was excluded — the footnote renders even if the £ figure
	 * could not be derived (no portfolio total to anchor it). */
	excludedAny: boolean;
}

/**
 * Derive the drawable slices for an allocation view.
 *
 * Percentages are shares of the POSITIVE weights (largest-remainder, so the legend sums to 100).
 * £ amounts keep the components' historic anchor — `total × value / Σall` — so the displayed
 * amounts plus the excluded (negative) amount still reconcile to the portfolio total; without a
 * `total`, or with a non-positive Σall (a book that is net nothing), no £ figure is fabricated.
 */
export function allocationDisplay(data: AllocationInput[], total?: number): AllocationDisplay {
	const included = data.filter((d) => d.value >= 0);
	const sumAll = data.reduce((s, d) => s + d.value, 0);
	const negSum = data.reduce((s, d) => (d.value < 0 ? s + d.value : s), 0);
	const canPrice = total != null && sumAll > 0;
	const pcts = roundPercentages(included.map((d) => d.value));
	return {
		slices: included.map((d, i) => ({
			...d,
			pct: pcts[i] ?? 0,
			amount: canPrice ? (total * d.value) / sumAll : null
		})),
		excludedAmount: negSum < 0 && canPrice ? (total * negSum) / sumAll : null,
		excludedAny: negSum < 0
	};
}
