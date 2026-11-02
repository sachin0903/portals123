/**
 * How a listing table behaves in a narrow pane.
 *
 * `table-fixed` plus percentage `headerClass` plus nowrap cells is even spacing on a desktop
 * pane and overlapping glyphs on a phone: a 22% column of a 360px card is 80px, and the cell
 * cannot wrap. The table must keep `auto` layout below `md`, grow with its content (`min-w-full`,
 * not `w-full`), and let its wrapper scroll sideways. Inner Y-scroll is a desktop listing
 * posture — on mobile the page is the scroller.
 *
 * Source-shape because the classes are strings in a template: there is no behaviour to drive
 * without a browser that does layout, and dropping `md:` is silent.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (name: string) => readFileSync(join(import.meta.dirname, name), 'utf8');
const table = read(join('..', 'table', 'table.svelte'));
const dataTable = read('data-table.svelte');

describe('listing tables on a narrow viewport', () => {
	it('lets the table grow past the pane instead of forcing 100% width', () => {
		// `w-full` on the <table> is a used width of 100%, so min-w-full cannot help: the
		// columns have nowhere to go and nowrap cells paint over each other.
		expect(table).toMatch(/min-w-full caption-bottom/);
		expect(table).toMatch(/md:w-full/);
		expect(table).not.toMatch(/cn\('w-full caption-bottom/);
	});

	it('keeps table-fixed off the phone breakpoint', () => {
		expect(dataTable).toMatch(/fixed && 'md:table-fixed'/);
		expect(dataTable).not.toMatch(/fixed && 'table-fixed'/);
	});

	it('scrolls rows inside the table only from md, unless a maxHeight bound is set', () => {
		expect(dataTable).toContain('md:min-h-0 md:flex-1 md:overflow-y-auto');
		expect(dataTable).toContain("cn('min-h-0 overflow-y-auto', maxHeight)");
		expect(dataTable).not.toMatch(/scroll \? cn\('min-h-0 flex-1 overflow-y-auto'/);
	});

	it('sticks the header only in the inner scroller', () => {
		expect(dataTable).toMatch(/maxHeight \? 'sticky top-0 z-10' : 'md:sticky md:top-0 md:z-10'/);
	});

	it('expands a detail row under the data row, spanning every column', () => {
		// An expandable listing (Reviews) must stay on DataTable. A second raw <table> is how
		// sort, density and the empty state drift from the rest of the book.
		expect(dataTable).toContain('{#if detail && expanded}');
		expect(dataTable).toContain('colspan={columns.length}');
		expect(dataTable).toContain('aria-expanded={detail ? expanded : undefined}');
	});
});
