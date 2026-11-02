/** The allocation donut's geometry — extracted so the "the percentages add up to 100" claim can be
 *  asserted rather than asserted in a comment.
 *
 *  The donut used to be capped at six asset classes with the remainder simply dropped, so a client
 *  holding eight classes saw a ring that stated less than the whole portfolio while looking like it
 *  stated all of it. The tail is now a real "Other" segment.
 */

export const ALLOC_PALETTE = [
	'var(--brand)',
	'oklch(0.6 0.1 260)',
	'oklch(0.7 0.11 90)',
	'oklch(0.6 0.1 160)',
	'oklch(0.6 0.13 25)',
	'oklch(0.55 0.02 256)'
];

export type Allocation = { label: string; value: number };
export type AllocationSegment = Allocation & { pct: number; color: string; offset: number };

/** Five named classes plus an "Other" bucket for the tail. `offset` is the SVG stroke-dashoffset
 *  the ring is drawn with (25 puts the first segment at 12 o'clock). */
export function allocationSegments(
	allocations: Allocation[],
	palette: string[] = ALLOC_PALETTE
): AllocationSegment[] {
	const total = allocations.reduce((s, a) => s + a.value, 0) || 1;
	const head = allocations.slice(0, 5);
	const rest = allocations.slice(5).reduce((s, a) => s + a.value, 0);
	const parts = rest > 0 ? [...head, { label: 'Other', value: rest }] : head;
	let cumulative = 0;
	return parts.map((a, i) => {
		const pct = (a.value / total) * 100;
		const seg = {
			label: a.label,
			value: a.value,
			pct,
			// `?? …` for noUncheckedIndexedAccess: the modulo keeps the index in range, but the
			// compiler can't see that, and an empty palette must still not paint `undefined`.
			color: palette[i % palette.length] ?? 'var(--brand)',
			offset: 25 - cumulative
		};
		cumulative += pct;
		return seg;
	});
}
