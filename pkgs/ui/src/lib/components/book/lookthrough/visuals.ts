import type { LookthroughBreakdown, LookthroughPosition } from './types.js';

export const normaliseRatio = (value: number): number => {
	if (!Number.isFinite(value)) return 0;
	return Math.max(0, Math.min(1, value > 1 ? value / 100 : value));
};

export function rankedBreakdown(rows: LookthroughBreakdown[]): LookthroughBreakdown[] {
	return rows
		.filter((row) => row.label.trim() && Number.isFinite(row.value) && row.value > 0)
		.sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
}

export interface TopHoldings {
	/** The largest positions by weight, largest first. */
	rows: LookthroughPosition[];
	/** Share of the fund those rows account for, as a ratio. */
	share: number;
	/** The largest single weight, as a ratio — the bars scale to it, so the first bar is full. */
	max: number;
	/** How many positions carried a usable weight. */
	count: number;
}

/**
 * The fund's largest positions, as a factsheet prints them. Replaces a one-row treemap that
 * labelled a cell only when it was wide enough: in a real fund of several hundred positions
 * nothing was, and the picture was unlabelled slivers beside one block called "Other positions".
 */
export function topHoldings(positions: LookthroughPosition[], limit = 10): TopHoldings {
	const ranked = [...positions]
		.filter((position) => Number.isFinite(position.weight) && position.weight > 0)
		.sort((a, b) => b.weight - a.weight || a.ordinal - b.ordinal);
	const rows = ranked.slice(0, limit);
	const share = rows.reduce((sum, row) => sum + normaliseRatio(row.weight), 0);
	return {
		rows,
		share: Math.min(1, share),
		max: rows.length ? normaliseRatio(rows[0]!.weight) : 0,
		count: ranked.length
	};
}

export const indirectTotal = (positions: LookthroughPosition[]): number =>
	positions.reduce(
		(sum, position) =>
			sum + (Number.isFinite(position.indirect_value) ? position.indirect_value : 0),
		0
	);
