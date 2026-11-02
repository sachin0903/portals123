/**
 * The pager's page maths, extracted so it can be unit-tested the way `data-table/sort.ts` and
 * `money-input/format.ts` are — a `$derived.by` inside a component is only reachable by mounting
 * one, and this is the part that decides what a reader can actually reach.
 *
 * The one behavioural change here is that a gap is no longer an anonymous `'ellipsis'` marker: it
 * carries the RANGE it hides. That is what turns the "…" from a decoration into a control with a
 * destination a reader can predict — the button can say "pages 3 to 22" rather than offering a
 * click with no stated outcome, which was the whole objection to making it interactive.
 */

/** A run of pages the pager has collapsed. Inclusive at both ends. */
export type Gap = { readonly from: number; readonly to: number };

/** One slot in the pager: a page number, or the gap standing in for a run of them. */
export type PageItem = number | Gap;

export function isGap(item: PageItem): item is Gap {
	return typeof item !== 'number';
}

/**
 * The slots to render: always the first and last page, the current page and its siblings, and a
 * gap for each run in between (e.g. `1 … 3 [4] 5 … 9`).
 *
 * A gap is only worth drawing when it hides more than one page — collapsing a single page behind
 * a control that then offers you that one page is strictly worse than printing the number, so a
 * small run is filled with numbers instead. That rule predates this module; it is kept exactly.
 */
export function pageItems(page: number, pageCount: number, siblingCount = 1): PageItem[] {
	const total = Math.max(1, pageCount);
	const maxSlots = siblingCount * 2 + 5; // first, last, current, both siblings, two gaps
	if (total <= maxSlots) return Array.from({ length: total }, (_, i) => i + 1);

	const left = Math.max(page - siblingCount, 1);
	const right = Math.min(page + siblingCount, total);
	const out: PageItem[] = [1];

	if (left > 3) out.push({ from: 2, to: left - 1 });
	else for (let i = 2; i < left; i++) out.push(i);

	for (let i = Math.max(left, 2); i <= Math.min(right, total - 1); i++) out.push(i);

	if (right < total - 2) out.push({ from: right + 1, to: total - 1 });
	else for (let i = right + 1; i < total; i++) out.push(i);

	out.push(total);
	return out;
}

/** A stable `{#each}` key. Two gaps in one pager never overlap, so their start is unique. */
export function itemKey(item: PageItem): string {
	return isGap(item) ? `gap-${item.from}` : `page-${item}`;
}

/**
 * The typed page, or `null` when there is nothing to go to.
 *
 * Digits only, and CLAMPED rather than refused — `go()` in the component has always clamped, so a
 * typed 99 in a 24-page set lands on 24 exactly as clicking the last page does. Refusing it would
 * be a different answer from the rest of the control for no gain: "99" in a pager plainly means
 * "the end", and there is no wrong record to write, only a page to land on.
 *
 * Anything that is not a run of digits is `null`, not 1: a stray letter is a typo, and quietly
 * sending someone to page 1 is the kind of silent wrong answer that reads as the control being
 * broken. `null` disables the submit instead, so nothing happens until the field says something.
 */
export function parsePageInput(raw: string, pageCount: number): number | null {
	const trimmed = raw.trim();
	if (!/^\d+$/.test(trimmed)) return null;
	const parsed = Number.parseInt(trimmed, 10);
	if (!Number.isFinite(parsed)) return null;
	return Math.min(Math.max(1, parsed), Math.max(1, pageCount));
}

/** The control's accessible name — it must state the destination, not just "more pages". */
export function gapLabel(gap: Gap): string {
	return gap.from === gap.to
		? `Go to page ${gap.from}`
		: `Go to a page between ${gap.from} and ${gap.to}`;
}
