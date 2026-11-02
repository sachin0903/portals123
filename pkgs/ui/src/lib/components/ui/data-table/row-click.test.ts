/**
 * The row-click guard — a pure test of the rule, because the bug it covers was invisible.
 *
 * `DataTable` puts `role="button"` on a clickable row, and `activate` skipped any click whose
 * target `closest('… , [role="button"]')` matched — which the row itself always does. So
 * `onRowClick` never fired on ANY DataTable: the contributions work queue and the admin API log
 * both looked inert, with no error to notice.
 *
 * The rule extracted here is the one line that was wrong: an interactive ancestor suppresses the
 * row click ONLY when it is not the row.
 */
import { describe, expect, it } from 'vitest';

/** The predicate `activate` applies. `interactive` is `target.closest(SELECTOR)`. */
const suppressed = (interactive: object | null, row: object): boolean =>
	!!interactive && interactive !== row;

describe('the row-click guard', () => {
	const row = { tag: 'tr' };

	it('does not suppress a click on the row itself', () => {
		// The row matches `[role="button"]` because it carries that role — the regression.
		expect(suppressed(row, row)).toBe(false);
	});

	it('suppresses a click on a control inside the row', () => {
		// A link to the client, or a Done button: those own their click.
		expect(suppressed({ tag: 'a' }, row)).toBe(true);
	});

	it('does not suppress a click on inert cell content', () => {
		// `closest()` finds nothing for a plain <span>, so nothing is suppressed.
		expect(suppressed(null, row)).toBe(false);
	});
});
