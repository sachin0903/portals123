import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import FilterPanel, { type FilterDef } from './filter-panel.svelte';

// Three bugs the panel shipped with. What it DOES on a click: filter-popover.dom.test.ts.
// Comments stripped — Svelte keeps them in SSR output and this component's prose names several of
// the strings asserted below.
const body = (available: FilterDef[], value: Record<string, string[]> = {}) =>
	render(FilterPanel, { props: { available, value, onsave: () => {} } }).body.replace(
		/<!--[\s\S]*?-->/g,
		''
	);

describe('FilterPanel (SSR)', () => {
	it('gives a number range labelled bounds, no spinner, and no bound it was not given', () => {
		// `type="number"` is banned: its scroll wheel silently edits the value under the pointer.
		// And the handles start on the ends, which mean "any" — when the round track did not line up
		// with them, merely opening the panel wrote bounds nobody chose.
		const def: FilterDef = { key: 'v', label: 'Value', options: [], kind: 'numberrange' };
		const set = body([def], { v: ['50', '3000'] });
		expect(set).toContain('aria-label="Value from"');
		expect(set).toContain('aria-label="Value to"');
		expect(set).toContain('inputmode="decimal"');
		expect(set).not.toContain('type="number"');

		const unset = body([{ ...def, min: '0', max: '100' }]);
		expect(unset.match(/placeholder="Any"/g)).toHaveLength(2);
		expect(unset).not.toContain('bg-primary/60');
	});

	it('offers a single-select the way back out, as a tick, without losing the option names', () => {
		// `allLabel` was on `FilterDef` and passed by two listings, and nothing rendered it — so a
		// choice was a one-way door: pick one and there is no route back to unfiltered. And with
		// the label `flex-1 truncate` against a `shrink-0` hint, a long description squeezed the
		// name it explains to nothing: the roles list read as four truncated sentences with no
		// Adviser or Owner on it.
		const single: FilterDef = {
			key: 'role',
			label: 'Role',
			single: true,
			allLabel: 'All roles',
			options: [{ value: 'a', label: 'Adviser', hint: 'Advises clients. Sees the book only.' }]
		};
		const html = body([single]);
		expect(html).not.toContain('type="radio"');
		expect(html).not.toContain('type="checkbox"');
		// The item's command value is also the words "All roles", so this has to be the marker on
		// the row itself: that row is the selected one while nothing is filtered.
		expect(html).toContain('data-selected="true"');
		expect(body([{ ...single, allLabel: undefined }])).toContain('Any');
		expect(html).toMatch(/shrink-0[^"]*truncate[^"]*"[^>]*title="Adviser"/);
		expect(html).toMatch(/min-w-0 flex-1 truncate[^"]*"[^>]*title="Advises clients\./);
	});

	it('lists every value in place, and adds a search box only from eight options', () => {
		const short = Array.from({ length: 7 }, (_, i) => ({
			value: `adviser-${i}`,
			label: `Adviser ${i}`,
			count: i + 1
		}));
		const compact = body([{ key: 'adviser', label: 'Adviser', options: short }]);
		expect(compact).toContain('Adviser 6');
		expect(compact).toContain('>7<');
		expect(compact).not.toContain('Search adviser');
		expect(compact).not.toContain('Choose adviser');
		expect(compact).not.toContain('type="checkbox"');

		const long = Array.from({ length: 8 }, (_, i) => ({
			value: `adviser-${i}`,
			label: `Adviser ${i}`
		}));
		const searchable = body([{ key: 'adviser', label: 'Adviser', options: long }]);
		expect(searchable).toContain('Adviser 7');
		expect(searchable).toContain('Search adviser');
		expect(searchable).not.toContain('Choose adviser');
	});
});
