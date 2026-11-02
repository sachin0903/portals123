// Folder grouping for DataTable. Collapsing a band only hides rows; it never filters.
import type { Component } from 'svelte';
import { sortRows } from './sort.js';
import type { DataTableColumn, DataTableSort } from './types.js';

type Icon = Component<{ class?: string }>;

/** One level of a row's folder path, outermost first. */
export interface DataTableGroupKey {
	/** Unique among its siblings. Any string is safe; it is escaped in the path. */
	key: string;
	label: string;
	last?: boolean; // pin below its siblings (the "Uncategorised" bucket)
	/** Explicit band order. Ordered bands come first; the rest go A→Z. */
	order?: number;
	icon?: Icon;
	/** The column this level groups by: a sort on it orders this level's bands by label. */
	column?: string;
}

export interface DataTableGroupRow<T = unknown> {
	kind: 'group';
	/** Escaped full path from the root: unique across the table, and the id `collapsed` holds. */
	path: string;
	label: string;
	depth: number;
	count: number;
	rows: readonly T[]; // every row beneath, nested or collapsed, for band-level actions
	collapsed: boolean;
	icon?: Icon;
	/** 1-based position among siblings, for `aria-posinset`. */
	position: number;
	setSize: number;
}

export interface DataTableDataRow<T> {
	kind: 'row';
	row: T;
	depth: number;
}

export type DataTableRenderRow<T> = DataTableGroupRow<T> | DataTableDataRow<T>;

interface Node<T> extends Omit<DataTableGroupKey, 'key'> {
	path: string;
	children: Map<string, Node<T>>;
	rows: T[]; // filed directly in this band
	all: T[]; // in this band and every band nested in it
}

/** A band key as one path segment. Escape `\` first, or the `/` escapes get escaped again. */
export const escapeSegment = (key: string): string =>
	key.replace(/\\/g, '\\\\').replace(/\//g, '\\/');

/** Pinned bands last, then `order` (unless sorting by label), then label; `path` breaks ties. */
const compareBands =
	<T>(dir: 1 | -1 | null) =>
	(a: Node<T>, b: Node<T>): number => {
		if (!!a.last !== !!b.last) return a.last ? 1 : -1;
		if (dir === null && a.order !== b.order)
			return (a.order ?? Number.MAX_SAFE_INTEGER) - (b.order ?? Number.MAX_SAFE_INTEGER);
		const label = a.label.localeCompare(b.label, 'en-GB', { numeric: true, sensitivity: 'base' });
		return label * (dir ?? 1) || a.path.localeCompare(b.path, 'en-GB');
	};

/**
 * Flatten rows into the render list: each band header, then its nested bands, then its rows. A
 * collapsed band emits its header and nothing beneath it. Rows with no path render above the bands.
 */
export function groupRows<T>(
	rows: readonly T[],
	groupBy: (row: T) => DataTableGroupKey[],
	columns: DataTableColumn<T>[],
	sort: DataTableSort | null,
	collapsed: ReadonlySet<string>,
	/** Skip the local sort when the server, or the caller, already ordered the rows. */
	presorted = false
): DataTableRenderRow<T>[] {
	const roots = new Map<string, Node<T>>();
	const loose: T[] = [];

	for (const row of rows) {
		let level = roots;
		let node: Node<T> | undefined;
		for (const { key, ...step } of groupBy(row)) {
			// Test for a parent, not an empty path: an empty key is a legal id.
			const path = node ? `${node.path}/${escapeSegment(key)}` : escapeSegment(key);
			node = level.get(key) ?? { ...step, path, children: new Map(), rows: [], all: [] };
			level.set(key, node);
			node.all.push(row);
			level = node.children;
		}
		(node ? node.rows : loose).push(row);
	}

	const order = (group: T[]) => (presorted ? group : sortRows(group, columns, sort));
	const out: DataTableRenderRow<T>[] = order(loose).map((row) => ({ kind: 'row', row, depth: 0 }));

	const walk = (level: Map<string, Node<T>>, depth: number): void => {
		const nodes = [...level.values()];
		const bySort = !!sort && nodes.some((node) => node.column === sort.key);
		nodes.sort(compareBands(bySort ? (sort?.dir === 'desc' ? -1 : 1) : null));
		nodes.forEach((node, index) => {
			const shut = collapsed.has(node.path);
			out.push({
				kind: 'group',
				path: node.path,
				label: node.label,
				depth,
				count: node.all.length,
				rows: node.all,
				collapsed: shut,
				icon: node.icon,
				position: index + 1,
				setSize: nodes.length
			});
			if (shut) return;
			// Sub-folders before files, as in a file manager.
			walk(node.children, depth + 1);
			for (const row of order(node.rows)) out.push({ kind: 'row', row, depth: depth + 1 });
		});
	};
	walk(roots, 0);
	return out;
}
