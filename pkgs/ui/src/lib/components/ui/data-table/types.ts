// Conventions: a table that hides columns sets `fixed`, and every column a `w-[n%]` headerClass.
// Never hide the identifying or the actions column (`align: 'right'`). Prefer `hideBelowWidth`
// (container) for reusable tables; its scale differs from `hideBelow` (`2xl` 672px vs `xl` 1280px).

import type { Snippet } from 'svelte';

/** A sortable value. `null`/`undefined`/`''` sort last regardless of direction. */
export type SortValue = string | number | Date | null | undefined;

export type SortDirection = 'asc' | 'desc';

export interface DataTableSort {
	key: string;
	dir: SortDirection;
}

export interface DataTableColumn<T> {
	/** Stable id; also the default row property read for sorting. */
	key: string;
	header: string;
	/** Header content in place of `header` text. On a sortable column it is the sort button's
	 *  label, so it must not hold a control. */
	head?: Snippet<[DataTableColumn<T>]>;
	/** Omit or set false for columns with no meaningful order (actions, icons, badges). */
	sortable?: boolean;
	/**
	 * Horizontal alignment. `right` for a column of FIGURES, where a shared decimal point is what
	 * makes them comparable down the column (the rule `MoneyInput` states for the same reason).
	 *
	 * `center` only for a switch, a checkbox or a short count; pills and badges align left with text.
	 */
	align?: 'left' | 'center' | 'right';
	/**
	 * Value this column sorts on. Defaults to `row[key]`. Give it explicitly whenever the
	 * cell renders something other than the raw field — a formatted amount, a joined list
	 * of advisers, a derived label — otherwise the visible order won't match the column.
	 */
	sortValue?: (row: T) => SortValue;
	/** First click on this header sorts this way. Amounts usually want 'desc'. */
	defaultDir?: SortDirection;
	/** The column the table opens sorted on. Without one, rows keep the order given. */
	sortDefault?: boolean;
	headerClass?: string;
	/**
	 * Inline style on the header cell. For a width the caller COMPUTES — and only that.
	 *
	 * A Tailwind class has to exist in the source for Tailwind to emit a rule for it, and a class
	 * assembled at runtime (`w-[${n}%]`) is in no source file. It silently produces no CSS, which
	 * under `table-fixed` means the column takes an even share instead of the one that was
	 * calculated — a layout bug with no error and nothing to grep for. A declared, literal width
	 * still belongs in `headerClass`; this is for the arithmetic case.
	 */
	headerStyle?: string;
	cellClass?: string;
	/** Hide below the given VIEWPORT breakpoint, e.g. 'md' → only visible from md up. Prefer
	 * `hideBelowWidth` for tables that can land in a narrow grid block — a viewport variant
	 * renders desktop layouts into a 350px span. */
	hideBelow?: 'sm' | 'md' | 'lg' | 'xl';
	/**
	 * Hide below the given CONTAINER width — the table's own width, which Table.Root
	 * establishes as a size container. Tailwind's container scale: sm 24rem/384px, md 28rem/448,
	 * lg 32rem/512, xl 36rem/576, 2xl 42rem/672, 3xl 48rem/768, 4xl 56rem/896. This is how a
	 * column collapses when the table sits in a narrow grid SPAN on a wide screen, where a
	 * viewport `hideBelow` would keep it and clip the money columns instead.
	 */
	hideBelowWidth?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | '4xl' | '5xl' | '6xl' | '7xl';
	/** Carries the `groupBy` indent (default: the first column). Not a right-aligned or hideable one. */
	indent?: boolean;
	/**
	 * The row's control (a select box). It and every column up to `indent` render as ONE indented
	 * cell, on `groupLead`'s ruler. Comes before `indent`; none of the merged columns may be hidden.
	 */
	lead?: boolean;
}
