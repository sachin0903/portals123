/**
 * A table whose rows are being replaced draws skeleton rows where they will land.
 *
 * `loading` is how a table held on screen by `Streamed keep` (the dashboard, whose tiles survive a
 * page cut) shows that ITS rows are the previous page. The header, the frame and the pager around
 * it stay; only the body says "arriving". Rendered rather than source-matched: the row count, the
 * cell-per-column shape and the `aria-busy` flag all have to agree.
 */
import { createRawSnippet, type ComponentProps } from 'svelte';
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import DataTable from './data-table.svelte';
import type { DataTableColumn } from './types.js';

type Row = { id: number; name: string; value: number };

const columns: DataTableColumn<unknown>[] = [
	{ key: 'name', header: 'Name' },
	{ key: 'value', header: 'Value', align: 'right' }
];

const rows: Row[] = [
	{ id: 1, name: 'Alan Horne', value: 1 },
	{ id: 2, name: 'Alba Williams', value: 2 },
	{ id: 3, name: 'Alex Anderson', value: 3 }
];

const body = (props: Partial<ComponentProps<typeof DataTable>>) =>
	render(DataTable, {
		props: {
			rows,
			columns,
			getKey: (row) => (row as Row).id,
			cell: createRawSnippet((row: () => unknown) => ({
				render: () => `<span>${(row() as Row).name}</span>`
			})),
			...props
		} satisfies ComponentProps<typeof DataTable>
	}).body;

describe('DataTable loading', () => {
	it('replaces the rows with one skeleton row each, and says it is busy', () => {
		const html = body({ loading: true });
		expect(html).toContain('aria-busy="true"');
		expect(html).not.toContain('Alan Horne');
		expect(
			html.match(/data-slot="skeleton"/g),
			'one bar per cell: 3 rows × 2 columns'
		).toHaveLength(6);
		// Still a header — the reader keeps the column names while the rows are on their way.
		expect(html).toContain('Name');
	});

	it('draws a short page when there were no rows to measure by', () => {
		const html = body({ rows: [], loading: true });
		expect(html.match(/data-slot="skeleton"/g)).toHaveLength(8 * 2);
		expect(html).not.toContain('No rows.');
	});

	it('is the ordinary table otherwise', () => {
		const html = body({});
		expect(html).not.toContain('aria-busy');
		expect(html).not.toContain('data-slot="skeleton"');
		expect(html).toContain('Alan Horne');
	});
});
