import { createRawSnippet, type ComponentProps } from 'svelte';
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import DataTable from './data-table.svelte';
import type { DataTableGroupRow } from './group.js';
import type { DataTableColumn } from './types.js';

type Row = { id: number; name: string; kind: string };

const rows: Row[] = [
	{ id: 1, name: 'Alpha', kind: 'Letter' },
	{ id: 2, name: 'Bravo', kind: 'Letter' },
	{ id: 3, name: 'Charlie', kind: 'Report' }
];

const HEAD = createRawSnippet(() => ({ render: () => '<b data-head="1">all</b>' }));

// Typed at `unknown`: `render` gives TypeScript no call site to infer the table's T from.
const columns: DataTableColumn<unknown>[] = [
	{ key: 'select', header: 'Select', head: HEAD },
	{ key: 'name', header: 'Name', sortable: true, head: HEAD },
	{ key: 'kind', header: 'Kind', sortable: true }
];

const html = (props: Partial<ComponentProps<typeof DataTable>> = {}) =>
	render(DataTable, {
		props: {
			rows,
			columns,
			getKey: (row) => (row as Row).id,
			cell: createRawSnippet(() => ({ render: () => '<span>cell</span>' })),
			...props
		} satisfies ComponentProps<typeof DataTable>
	}).body;

it('a column `head` replaces the header text, and keeps a sortable column’s button', () => {
	const body = html();
	const head = body.slice(body.indexOf('<thead'), body.indexOf('</thead>'));
	expect(head.match(/data-head="1"/g)).toHaveLength(2);
	expect(head).not.toMatch(/Select|Name/);
	expect(head).toMatch(/<button[^>]*>(?:(?!<\/button>).)*data-head="1"/s);
});

it('draws `groupLead` before the disclosure and hands it every row beneath the band', () => {
	const groupBy = (row: unknown) => [{ key: (row as Row).kind, label: (row as Row).kind }];
	const lead = createRawSnippet((band: () => DataTableGroupRow<unknown>) => ({
		render: () =>
			`<i data-lead="${band().label}" data-ids="${band().rows.map((r) => (r as Row).id)}"></i>`
	}));
	const body = html({ groupBy, groupLead: lead, collapsed: new Set(['Letter']) });
	expect(body.replace(/<!--.*?-->/g, '')).toMatch(
		/<td[^>]*colspan="3"[^>]*><div[^>]*><div[^>]*><i data-lead="Letter" data-ids="1,2"><\/i><\/div>\s*<button[^>]*aria-expanded="false"/
	);
	expect(body).toContain('data-ids="3"');
});

describe('DataTable `lead` column', () => {
	type Doc = { id: number; name: string; client: string; kind: string };
	const docs: Doc[] = [{ id: 1, name: 'Alpha', client: 'Ann', kind: 'Letter' }];
	const leadColumns: DataTableColumn<unknown>[] = [
		{ key: 'select', header: 'Select', head: HEAD, lead: true, cellClass: 'pl-5' },
		{ key: 'name', header: 'Name', sortable: true, indent: true },
		{ key: 'kind', header: 'Kind', sortable: true }
	];
	const cell = createRawSnippet((_row: () => unknown, column: () => DataTableColumn<unknown>) => ({
		render: () => `<b data-cell="${column().key}"></b>`
	}));
	const box = createRawSnippet(() => ({ render: () => '<i data-lead="1"></i>' }));
	const nested = (row: unknown) => [
		{ key: (row as Doc).client, label: (row as Doc).client },
		{ key: (row as Doc).kind, label: (row as Doc).kind }
	];
	const body = (props: Partial<ComponentProps<typeof DataTable>> = {}) =>
		html({ rows: docs, columns: leadColumns, cell, ...props }).replace(/<!--.*?-->/g, '');

	it('renders the lead and the indent column as one cell, and keeps both headers', () => {
		const out = body();
		expect(out.match(/data-slot="table-head"/g)).toHaveLength(3);
		expect(out).toMatch(
			/<td[^>]*colspan="2"[^>]*><div[^>]*><div[^>]*><b data-cell="select"><\/b><\/div>\s*<div[^>]*><b data-cell="name"><\/b><\/div>/
		);
	});

	/** The wrapper's step around the marker at `at`, in rem. */
	const stepAt = (out: string, at: number): number => {
		const open = out.lastIndexOf('<div class="flex min-w-0 items-center gap-2"', at);
		const tag = out.slice(open, out.indexOf('>', open));
		return Number(/padding-left: ([\d.]+)rem/.exec(tag)?.[1] ?? 0);
	};

	it('steps a depth-2 row’s box in past its depth-1 band’s, which steps in past depth 0', () => {
		const out = body({ groupBy: nested, groupLead: box });
		const leads = [...out.matchAll(/data-lead="1"/g)].map((m) => m.index!);
		const [client, kind] = leads.map((at) => stepAt(out, at));
		const row = stepAt(out, out.indexOf('data-cell="select"'));
		expect([client, row - kind]).toEqual([0, kind]);
		expect(kind).toBeGreaterThan(0);
	});
});
