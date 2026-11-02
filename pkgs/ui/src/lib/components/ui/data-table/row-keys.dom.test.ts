import { expect, it, vi } from 'vitest';
import { createRawSnippet, mount, tick, unmount } from 'svelte';
import DataTable from './data-table.svelte';

it('opens a row on Enter only when the row itself has focus, not a link inside it', async () => {
	const onRowClick = vi.fn();
	const app = mount(DataTable, {
		target: document.body,
		props: {
			columns: [{ key: 'name', header: 'Name' }],
			rows: [{ id: 1 }],
			getKey: (r: { id: number }) => r.id,
			onRowClick,
			cell: createRawSnippet(() => ({ render: () => `<a href="#doc">Ada</a>` }))
		} as never
	});
	await tick();
	const row = document.querySelector('tbody tr[role="button"]')!;
	const press = (el: Element) => {
		const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
		el.dispatchEvent(event);
		return event.defaultPrevented;
	};
	expect(press(row.querySelector('a')!)).toBe(false);
	expect(onRowClick).not.toHaveBeenCalled();
	expect(press(row)).toBe(true);
	expect(onRowClick).toHaveBeenCalledOnce();
	unmount(app);
});
