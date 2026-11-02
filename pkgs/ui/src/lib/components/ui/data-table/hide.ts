/**
 * The classes that collapse a column below a width, and the inverse for content that stands in
 * for it while it is gone.
 *
 * A column's `hideBelow` / `hideBelowWidth` hides its cells with `hidden …:table-cell`. Anything
 * that should appear exactly then — the growth figure drawn under Value once the Growth column
 * has dropped out — needs the complementary class, or the two overlap on one side of the
 * breakpoint and both vanish on the other. One map per breakpoint, read both ways, so adding a
 * rung means adding it here and nowhere else.
 *
 * Static literals throughout: Tailwind generates only the classes it can see in source.
 */
import { cn } from '../../../utils.js';
import type { DataTableColumn } from './types.js';

const HIDE_BELOW = {
	sm: 'hidden sm:table-cell',
	md: 'hidden md:table-cell',
	lg: 'hidden lg:table-cell',
	xl: 'hidden xl:table-cell'
} as const;

// Container-width variants (the table's own width — Table.Root is the @container), so a column
// collapses by grid SPAN, not viewport. 5xl-7xl matter for a WIDE table: the ladder used to stop
// at 4xl (56rem), so a twelve-column ledger in a 1900px pane was past every rung and nothing ever
// dropped — the table overflowed sideways while the tiers looked configured.
const HIDE_BELOW_WIDTH = {
	sm: 'hidden @sm:table-cell',
	md: 'hidden @md:table-cell',
	lg: 'hidden @lg:table-cell',
	xl: 'hidden @xl:table-cell',
	'2xl': 'hidden @2xl:table-cell',
	'3xl': 'hidden @3xl:table-cell',
	'4xl': 'hidden @4xl:table-cell',
	'5xl': 'hidden @5xl:table-cell',
	'6xl': 'hidden @6xl:table-cell',
	'7xl': 'hidden @7xl:table-cell'
} as const;

/** `block`, so the stand-in takes its own line under the figure it qualifies rather than sitting
 *  beside it. */
const WHILE_HIDDEN = {
	sm: 'block sm:hidden',
	md: 'block md:hidden',
	lg: 'block lg:hidden',
	xl: 'block xl:hidden'
} as const;

const WHILE_HIDDEN_WIDTH = {
	sm: 'block @sm:hidden',
	md: 'block @md:hidden',
	lg: 'block @lg:hidden',
	xl: 'block @xl:hidden',
	'2xl': 'block @2xl:hidden',
	'3xl': 'block @3xl:hidden',
	'4xl': 'block @4xl:hidden',
	'5xl': 'block @5xl:hidden',
	'6xl': 'block @6xl:hidden',
	'7xl': 'block @7xl:hidden'
} as const;

/** The classes that hide a column's cells below its breakpoint. */
export function hideClasses<T>(column: DataTableColumn<T>): string | undefined {
	const out = cn(
		column.hideBelow && HIDE_BELOW[column.hideBelow],
		column.hideBelowWidth && HIDE_BELOW_WIDTH[column.hideBelowWidth]
	);
	return out || undefined;
}

/** The classes that show an element exactly while `column` is hidden — for the content that
 *  stands in for a collapsed column. Undefined when the column never hides, so the caller
 *  renders nothing rather than a second, permanent copy. */
export function visibleWhenHidden<T>(column: DataTableColumn<T> | undefined): string | undefined {
	if (!column) return undefined;
	const out = cn(
		column.hideBelow && WHILE_HIDDEN[column.hideBelow],
		column.hideBelowWidth && WHILE_HIDDEN_WIDTH[column.hideBelowWidth]
	);
	return out || undefined;
}
