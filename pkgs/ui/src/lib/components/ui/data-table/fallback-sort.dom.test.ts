import { afterEach, expect, it } from 'vitest';
import { createRawSnippet, mount, tick, unmount } from 'svelte';
import DataTable from './data-table.svelte';
import type { DataTableColumn } from './types.js';

type Row = { id: number; name: string };
const columns: DataTableColumn<Row>[] = [{ key: 'name', header: 'Name', sortable: true }];
const rows: Row[] = [
	{ id: 1, name: 'Zed' },
	{ id: 2, name: 'Ada' }
];

const live: ReturnType<typeof mount>[] = [];
afterEach(() => {
	while (live.length) unmount(live.pop()!);
	document.body.innerHTML = '';
});

async function mountTable(sortDefault: boolean) {
	const target = document.createElement('div');
	document.body.append(target);
	live.push(
		mount(DataTable, {
			target,
			props: {
				columns: [{ ...columns[0], sortDefault }],
				rows,
				getKey: (r: Row) => r.id,
				cell: createRawSnippet((row: () => Row, col: () => DataTableColumn<Row>) => ({
					render: () => `<span>${String((row() as never)[col().key as never])}</span>`
				}))
			} as never
		})
	);
	await tick();
	const header = [...target.querySelectorAll('button')].find(
		(b) => b.textContent?.trim() === 'Name'
	)!;
	const names = () => [...target.querySelectorAll('tbody span')].map((s) => s.textContent?.trim());
	return { header, names };
}

it('keeps the given row order, with no aria-sort, when no column is sortDefault', async () => {
	const { header, names } = await mountTable(false);
	expect(names()).toEqual(['Zed', 'Ada']);
	expect(header.closest('th')?.hasAttribute('aria-sort')).toBe(false);
});

it('reverses the sortDefault column on the first click, not the second', async () => {
	const { header, names } = await mountTable(true);
	expect(names()).toEqual(['Ada', 'Zed']);
	header.click();
	await tick();
	expect(header.closest('th')?.getAttribute('aria-sort')).toBe('descending');
	expect(names()).toEqual(['Zed', 'Ada']);
});
