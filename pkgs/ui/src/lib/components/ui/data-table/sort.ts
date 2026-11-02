import type { DataTableColumn, DataTableSort, SortValue } from './types.js';

/**
 * Order two sortable values. Numbers compare numerically, dates chronologically, everything
 * else by locale collation with `numeric` (so "Account 2" precedes "Account 10") and `base`
 * sensitivity (so case doesn't split otherwise-identical names).
 */
export function compareValues(a: SortValue, b: SortValue): number {
	if (a instanceof Date || b instanceof Date) {
		return Number(a instanceof Date ? a : 0) - Number(b instanceof Date ? b : 0);
	}
	if (typeof a === 'number' && typeof b === 'number') return a - b;
	return String(a).localeCompare(String(b), 'en-GB', { numeric: true, sensitivity: 'base' });
}

// An Invalid Date is blank: a NaN comparison would scramble the whole column.
const isBlank = (v: SortValue) =>
	v == null || v === '' || (v instanceof Date && Number.isNaN(v.getTime()));

/**
 * Sort rows by the active column. Returns the input array unchanged when there is no sort or
 * the sorted key doesn't match a column, so a stale sort key can never blank a table.
 *
 * Blanks always sink to the bottom, in both directions — reversing the sort shouldn't promote
 * a column of em-dashes above real data. This mirrors the `NULLS LAST` the list queries use.
 */
export function sortRows<T>(
	rows: T[],
	columns: DataTableColumn<T>[],
	sort: DataTableSort | null | undefined
): T[] {
	if (!sort) return rows;
	const column = columns.find((c) => c.key === sort.key);
	if (!column) return rows;

	const read = column.sortValue ?? ((row: T) => (row as Record<string, SortValue>)[column.key]);
	const dir = sort.dir === 'asc' ? 1 : -1;

	return [...rows].sort((a, b) => {
		const av = read(a);
		const bv = read(b);
		const ab = isBlank(av);
		const bb = isBlank(bv);
		if (ab || bb) return ab && bb ? 0 : ab ? 1 : -1;
		return compareValues(av, bv) * dir;
	});
}

/** The sort state after clicking a column header: same column flips, a new column starts fresh. */
export function nextSort<T>(
	current: DataTableSort | null | undefined,
	column: DataTableColumn<T>
): DataTableSort {
	return current?.key === column.key
		? { key: column.key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
		: { key: column.key, dir: column.defaultDir ?? 'asc' };
}

/** The fallback order: the sortable `sortDefault` column, or null to keep the given order. */
export function defaultSort<T>(columns: DataTableColumn<T>[]): DataTableSort | null {
	const column = columns.find((c) => c.sortable && c.sortDefault);
	return column ? { key: column.key, dir: column.defaultDir ?? 'asc' } : null;
}
