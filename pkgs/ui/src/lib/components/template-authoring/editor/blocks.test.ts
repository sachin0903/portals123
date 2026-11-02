import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// ── the Tables palette ──────────────────────────────────────────────────────
describe('the Tables palette', () => {
	// ONE TEXT TABLE AND ONE DATA TABLE. A third entry, "Projection", was not a third kind — the
	// same `doc-jtable` pre-bound to a dataset with four columns — so the palette offered a choice
	// where there was none, and an author had to know that picking it was picking Data table and
	// rebinding. A preset is not a component.
	//
	// Read off the source because the palette is registered against a live GrapesJS editor. What is
	// pinned is the OFFER; the `data_table` component (a separate, server-rendered thing) stays in
	// the code for any template that already holds one, and is deliberately not offered anywhere.
	const src = readFileSync(fileURLToPath(new URL('./blocks.ts', import.meta.url)), 'utf8');

	it('offers exactly two tables', () => {
		const labels = [...src.matchAll(/category: TABLES,[\s\S]{0,120}?/g)];
		expect(labels.length, 'the TABLES category has changed shape').toBeGreaterThan(0);
		const ids = [...src.matchAll(/add\('(table-[\w-]+)'/g)].map((m) => m[1]);
		expect(ids).toEqual(['table-text', 'table-jinja']);
	});

	it('no longer offers a preset dressed as a component', () => {
		expect(src).not.toContain("add('table-projection'");
		expect(src).not.toContain("label: 'Projection'");
		// And nothing else quietly binds that dataset on the author's behalf.
		expect(src).not.toContain('data-dataset="projections"');
	});

	it('offers no second data table', () => {
		// `data_table` is the server-rendered chart-type variant. It is reachable from no block and
		// from no chart-type list, so an author cannot make one — which is what stops the editor
		// having two data tables with two different config dialogs.
		expect(src).not.toContain('data_table');
	});
});
