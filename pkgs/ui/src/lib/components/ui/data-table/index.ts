import DataTable from './data-table.svelte';

export { DataTable };
// `sortRows` is exported so a PAGINATED caller can sort the whole population before it slices.
// Without it the only thing a parent could hand DataTable was an already-cut page, which the
// table then sorted in isolation — reordering within the page and never across the book.
// `defaultSort` too: a caller sorting its own rows must fall back to the same column this table would.
export { defaultSort, nextSort, sortRows } from './sort.js';
export { visibleWhenHidden } from './hide.js';
export { escapeSegment, groupRows } from './group.js';
export type { DataTableDataRow, DataTableGroupKey, DataTableGroupRow } from './group.js';
export type { DataTableColumn, DataTableSort, SortDirection, SortValue } from './types.js';
