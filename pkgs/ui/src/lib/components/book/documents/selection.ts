// Row selection for the documents listings: pure, so each keeps `selected` as its own state.
import { groupRows, type DataTableGroupKey } from '../../ui/data-table/group.js';
import type { DataTableColumn, DataTableSort } from '../../ui/data-table/types.js';
import type { LibraryRow } from './documents.js';

export type SelectionState = 'all' | 'some' | 'none';

type Uid = { uid: string };

/** How much of `rows` is selected; 'some' shows the box's indeterminate dash. */
export function selectionOf(selected: readonly string[], rows: readonly Uid[]): SelectionState {
	const set = new Set(selected);
	const picked = rows.filter((d) => set.has(d.uid)).length;
	if (picked === 0) return 'none';
	return picked === rows.length ? 'all' : 'some';
}

/** Tick all of `rows`, or clear them when all are ticked. Other rows keep their state. */
export function toggleMany(selected: readonly string[], rows: readonly Uid[]): string[] {
	const uids = rows.map((d) => d.uid);
	const drop = new Set(uids);
	return selectionOf(selected, rows) === 'all'
		? selected.filter((u) => !drop.has(u))
		: [...new Set([...selected, ...uids])];
}

/** The uids in the order `DataTable` draws them (shut folders skipped), or `rows` as given. */
export function renderedOrder<T extends Uid>(
	rows: readonly T[],
	groupBy: ((row: T) => DataTableGroupKey[]) | undefined,
	columns: DataTableColumn<T>[],
	sort: DataTableSort | null,
	collapsed: ReadonlySet<string>
): string[] {
	if (!groupBy) return rows.map((d) => d.uid);
	return groupRows(rows, groupBy, columns, sort, collapsed).flatMap((e) =>
		e.kind === 'row' ? [e.row.uid] : []
	);
}

/** One box's click; with `shift` and an `anchor` in `order`, the range takes its new state. */
export function pickRow(
	selected: readonly string[],
	order: readonly string[],
	uid: string,
	anchor: string | null,
	shift: boolean
): string[] {
	const on = !selected.includes(uid);
	const from = anchor === null ? -1 : order.indexOf(anchor);
	const to = order.indexOf(uid);
	if (shift && from >= 0 && to >= 0) {
		const range = order.slice(Math.min(from, to), Math.max(from, to) + 1);
		if (on) return [...new Set([...selected, ...range])];
		const drop = new Set(range);
		return selected.filter((u) => !drop.has(u));
	}
	return on ? [...selected, uid] : selected.filter((u) => u !== uid);
}

/** What the bulk bar deletes: selected rows still in `rows`, protected documents left out. */
export function bulkTargets(
	selected: readonly string[],
	rows: readonly LibraryRow[]
): { documents: string[]; files: string[] } {
	const set = new Set(selected);
	const picked = rows.filter((d) => set.has(d.uid));
	return {
		documents: picked.filter((d) => !d.isFile && d.deletable !== false).map((d) => d.uid),
		files: picked.filter((d) => d.isFile).map((d) => d.uid)
	};
}

/** A delete confirmation's title. */
export const deleteTitle = (kind: 'documents' | 'files', n: number): string =>
	n === 1 ? `Delete this ${kind.slice(0, -1)}?` : `Delete ${n} ${kind}?`;
