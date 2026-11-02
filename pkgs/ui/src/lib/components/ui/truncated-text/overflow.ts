/**
 * Whether a box is actually clipping its content.
 *
 * Extracted as a pure function for the same reason `data-table/sort.ts` and `money-input/format.ts`
 * are: it is the one bit of `TruncatedText` that decides behaviour, and it is testable without a DOM.
 *
 * The tolerance is the point. `scrollWidth` and `clientWidth` are integers rounded from fractional
 * layout, so a box that fits exactly can report a 1px overflow — and without slack every cell in the
 * table would claim to be truncated and show a tooltip repeating text you can already read. A
 * tooltip on text that is not clipped is noise, which is the whole reason this is gated rather than
 * always-on.
 */
export function overflows(scrollWidth: number, clientWidth: number, tolerance = 1): boolean {
	return scrollWidth - clientWidth > tolerance;
}
