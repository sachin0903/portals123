/**
 * What a listing table says about its own sorting BEFORE anyone clicks it.
 *
 * Two questions a reader has on arrival: "which way is this sorted?" and "what else can I sort
 * by?". The first was already answered — a seeded `sort` renders a solid arrow on that column from
 * the first paint. The second was not: the inactive columns' chevron was `opacity-0` until the
 * pointer landed on the header, so a sortable column and a fixed one looked identical, and on a
 * touch screen there is no hover to reveal it with.
 *
 * Rendered rather than source-matched: the conditional, the `active` derivation and the class all
 * have to agree, and a source regex over any one of them can pass while the header renders wrong.
 */
import { createRawSnippet, type ComponentProps } from 'svelte';
import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import DataTable from './data-table.svelte';
import type { DataTableColumn, DataTableSort } from './types.js';

type Row = { id: number; name: string; value: number };

// No rows: the header is the whole subject here, and a body would need a `cell` snippet that says
// nothing about sorting. The header renders the same either way.
const rows: Row[] = [];

// Typed at `unknown`, not at `Row`: DataTable is generic and `render` gives TypeScript no call
// site to infer T from, so the props instantiate at `unknown`. Nothing is lost — not one of these
// columns reads a row.
const columns: DataTableColumn<unknown>[] = [
	{ key: 'name', header: 'Name', sortable: true },
	{ key: 'value', header: 'Value', sortable: true },
	{ key: 'ref', header: 'Reference' }
];

// `getKey` and `cell` are required props that an empty table never calls. They are here to satisfy
// the contract; the header is what these tests read.
const body = (sort: DataTableSort | null) =>
	render(DataTable, {
		props: {
			rows,
			columns,
			getKey: (row) => (row as Row).id,
			cell: createRawSnippet(() => ({ render: () => '<td></td>' })),
			sort
		} satisfies ComponentProps<typeof DataTable>
	}).body;

describe('DataTable sort indication', () => {
	it('shows the default sort as an arrow on first paint, with no click', () => {
		const html = body({ key: 'value', dir: 'desc' });
		expect(html).toContain('aria-sort="descending"');
		expect(html).toContain('lucide-arrow-down');
		// Exactly one column claims the sort.
		expect(html.match(/aria-sort="descending"/g)).toHaveLength(1);
		expect(html).not.toContain('aria-sort="ascending"');
	});

	it('points the arrow the way the sort actually runs', () => {
		const html = body({ key: 'name', dir: 'asc' });
		expect(html).toContain('aria-sort="ascending"');
		expect(html).toContain('lucide-arrow-up');
		expect(html).not.toContain('lucide-arrow-down');
	});

	it('marks the OTHER sortable columns as sortable, visibly and not on hover', () => {
		const html = body({ key: 'value', dir: 'desc' });
		// The one inactive sortable column still renders its chevron…
		expect(html).toContain('lucide-chevrons-up-down');
		// …at a dim but real opacity. `opacity-0` made the affordance hover-only, which a touch
		// screen never delivers.
		expect(html).toContain('opacity-30');
		expect(html).toContain('group-hover:opacity-70');
		expect(html).not.toContain('opacity-0 transition-opacity');
		// Keyboard too: tabbing to the header brightens it, the same as the documents tables.
		expect(html).toContain('group-focus-visible:opacity-70');
	});

	it('gives a non-sortable column no control and no aria-sort', () => {
		const html = body({ key: 'value', dir: 'desc' });
		// Two sortable columns become buttons; Reference stays plain text.
		expect(html.match(/<button type="button"/g)).toHaveLength(2);
		expect(html).toContain('Reference');
	});

	it('with no sort chosen and no sortDefault column, claims no sort', () => {
		const html = body(null);
		expect(html).not.toContain('aria-sort="');
		expect(html.match(/lucide-chevrons-up-down/g)).toHaveLength(2);
	});
});
