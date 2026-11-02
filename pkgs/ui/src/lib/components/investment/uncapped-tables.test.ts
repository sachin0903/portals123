/**
 * The two table variants that take NO slice cap must bound their own height.
 *
 * `sliceCap` deliberately exempts `holdings/table` and `activity/table`: they are the "show me
 * everything" variants, and capping them would be the lie the cap exists to avoid. The consequence
 * is that they are the only two blocks that can grow without limit — a long book or a long ledger
 * rendered every row and the page ran off the bottom, taking the toolbar above and the pager below
 * with it.
 *
 * So they scroll their own rows instead, against a VIEWPORT bound. That is the part worth pinning
 * and the part that looks wrong until you know why: the masonry cell is sized FROM the card, so a
 * card that asked for a percentage of its cell would close the feedback loop `portal-page.svelte`
 * records at 147,000px. `100svh` is not a percentage of anything the grid computes, so the cell
 * measures a child that has stopped growing.
 *
 * A source-shape test because the bound is a string in a template: there is no behaviour to drive
 * without a browser that does layout, and dropping the prop is silent — the table simply grows
 * again, which reads as a content problem rather than a regression.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (name: string) => readFileSync(join(import.meta.dirname, name), 'utf8');
const page = read('portal-page.svelte');
const holdings = read('holdings-full-table.svelte');
const activity = read('activity-table.svelte');

/** The bound both blocks use — a floor so it can never fight the height a firm authored. */
const BOUND = 'max-h-[max(24rem,calc(100svh-22rem))]';

describe('the uncapped table variants are bounded', () => {
	it('exempts exactly these two TABLE variants from the slice cap', () => {
		// If a third table joins the exemption it needs the same bound, and this is where that
		// shows up. The other uncapped forms — a toolbar'd block, grouped holdings, the documents
		// and news libraries — are a page's own flowing content (the home card's list, taller), not
		// a table with a toolbar and pager to keep in reach, so they scroll with the page instead.
		expect(page).toContain("b.settings?.['toolbar'] === true ||");
		expect(page).toContain("(b.type === 'holdings' && (v === 'table' || v === 'grouped'))");
		expect(page).toContain("(b.type === 'activity' && v === 'table')");
		expect(page).not.toMatch(/b\.type === 'accounts' && v === 'table'/);
	});

	it('bounds the holdings table', () => {
		expect(page).toContain(BOUND);
	});

	it('bounds the activity table too', () => {
		// Both occurrences: one per block. The activity one is the later addition, and a single
		// `toContain` would pass on the holdings one alone.
		expect(page.split(BOUND).length - 1).toBeGreaterThanOrEqual(2);
	});

	it('bounds in VIEWPORT units, never a percentage of the cell', () => {
		// A percentage would close the masonry feedback loop. `svh` cannot, because the grid is not
		// computing it.
		expect(BOUND).toContain('100svh');
		expect(page).not.toMatch(/max-h-\[\d+%\]/);
	});

	it('keeps a floor, so the bound cannot fight an authored height', () => {
		expect(BOUND).toContain('max(24rem');
	});

	it('both tables accept the bound and turn scrolling on with it', () => {
		for (const [name, src] of [
			['holdings-full-table', holdings],
			['activity-table', activity]
		] as const) {
			expect(src, name).toContain('maxHeight');
			// `scroll` derives from the bound rather than being a second switch to remember: a
			// bound with no scroll clips the rows away instead of letting you reach them.
			expect(src, name).toContain('scroll={!!maxHeight}');
		}
	});
});
