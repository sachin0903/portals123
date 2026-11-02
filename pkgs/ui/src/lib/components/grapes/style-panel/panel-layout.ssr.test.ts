import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import BorderControl from './border-control.svelte';
import PropertyControl from './property-control.svelte';
import BoxSides from '../../ui/box-sides/box-sides.svelte';
import { borderFamily } from './borders';
import { propertyView, type PropertyLike } from './read';

const HERE = import.meta.dirname;

/** Comments stripped before scanning (the `wiring.test.ts` idiom), so the scan sees CODE only. */
function stripComments(code: string): string {
	return code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(^|[^:'"`\w])\/\/[^\n]*/g, '$1');
}

/** The rail's SHAPE, pinned in the markup: the rule tests cannot see a heading or a repeated line. */

function sub(name: string, type: string, value: string, extra: object = {}): PropertyLike {
	return {
		getId: () => name,
		getName: () => name,
		getLabel: () => name,
		getType: () => type,
		getValue: () => value,
		hasValue: () => value !== '',
		canClear: () => value !== '',
		upValue: vi.fn(),
		clear: vi.fn(),
		getUnits: () => ['px', 'em'],
		getOptions: () => [{ id: 'solid' }, { id: 'dashed' }],
		getOptionId: (o: { id: string }) => o.id,
		getOptionLabel: (id: string) => id,
		getProperties: () => [] as PropertyLike[],
		...extra
	} as PropertyLike;
}

/** A per-edge composite as `css-borders.ts::borderSideProperty` declares it — unit a separate read. */
function side(name: string, width = '', style = '', colour = '') {
	const [, digits = '', unit = ''] = /^([+-]?[\d.]*)(.*)$/.exec(width) ?? [];
	return propertyView(
		sub(name, 'composite', '', {
			getProperties: () => [
				sub(`${name}-width`, 'number', digits, { getUnit: () => unit, getMin: () => 0 }),
				sub(`${name}-style`, 'select', style),
				sub(`${name}-color`, 'color', colour)
			]
		})
	);
}

const SIDES = ['border-top', 'border-right', 'border-bottom', 'border-left'];

const noteBox = () =>
	borderFamily([
		side('border-top'),
		side('border-right'),
		side('border-bottom'),
		side('border-left', '3px', 'solid', '#3b82f6')
	]);

describe('the border controls sit in their sector, not in a section of their own', () => {
	const body = () =>
		render(BorderControl, { props: { family: noteBox(), onchange: () => {} } }).body;

	it('draws no disclosure of its own', () => {
		// Against the SOURCE: the render carries `data-state` from the style row's own SelectMenu.
		const source = stripComments(readFileSync(join(HERE, 'border-control.svelte'), 'utf8'));
		expect(source).not.toContain('Collapsible');
		expect(body()).not.toMatch(/>\s*Borders\s*</);
	});

	it('names each row for itself, because nothing above it does any more', () => {
		// A bare `Width` beside a background colour reads as the ELEMENT's width.
		const html = body();
		for (const label of ['Border width', 'Border style', 'Border colour'])
			expect(html, label).toContain(label);
		expect(html).not.toMatch(/>\s*Width\s*</);
	});

	it('still reads back the shipped left stripe', () => {
		const html = body();
		expect(html).toMatch(/id="sp-border-width-left"[^>]*value="3"/);
		expect(html).toMatch(/id="sp-border-width-top"[^>]*value=""/);
		expect(html).toContain('#3b82f6');
	});

	it('shows an em width in em, not in px', () => {
		// The unit drop, visible only in the rendered field: read apart from `getUnit()`, a `1.5em` border
		// displayed as `1.5px` and was written back (`read.ts::composedValue`).
		const html = stripComments(
			render(BorderControl, {
				props: {
					family: borderFamily([side('border-left', '1.5em', 'solid', 'red')]),
					onchange: () => {}
				}
			}).body
		);
		expect(html).toMatch(/id="sp-border-width-left"[^>]*value="1.5"/);
		// Scoped to the left field, the only edge with a value: a blank length legitimately shows `px`.
		const left = html.slice(html.indexOf('id="sp-border-width-left"'));
		expect(left.match(/aria-label="Unit"/g)).toHaveLength(1);
		// `px` is the first offered unit, what a dropped one falls back to: the negative is the real half.
		expect(left).toMatch(/aria-label="Unit"[^>]*>\s*em\b/);
		expect(left).not.toMatch(/aria-label="Unit"[^>]*>\s*px\b/);
	});

	it('renders nothing at all when the rail holds no border property', () => {
		const html = render(BorderControl, {
			props: { family: borderFamily([]), onchange: () => {} }
		}).body;
		for (const label of ['Border width', 'Border style', 'Border colour'])
			expect(html, label).not.toContain(label);
	});

	it('offers a width field per edge, in CSS shorthand order', () => {
		const html = body();
		const ids = [...html.matchAll(/id="sp-border-width-(\w+)"/g)].map((m) => m[1]);
		expect(ids).toEqual(['top', 'right', 'bottom', 'left']);
		expect(SIDES).toHaveLength(ids.length);
	});
});

describe('a four-sided control does not restate its own parts', () => {
	const mixed = { top: '10px', right: '24px', bottom: '10px', left: '24px' };

	it('prints no summary line above the expanded parts', () => {
		// The summary sat above four labelled fields carrying those values; it is the `title` now.
		const html = render(BoxSides, { props: { values: mixed, onchange: () => {} } }).body;
		expect(html).not.toMatch(/>\s*10px · 24px · 10px · 24px\s*</);
		expect(html).toContain('title="10px · 24px · 10px · 24px"');
	});

	it('draws a field per part, so the values are still on screen', () => {
		const html = render(BoxSides, { props: { values: mixed, onchange: () => {} } }).body;
		expect(html).toContain('value="10px"');
		expect(html).toContain('value="24px"');
		const fields = [...html.matchAll(/id="box-(\w+)"/g)].map((m) => m[1]);
		expect(fields).toEqual(['top', 'right', 'bottom', 'left']);
	});

	it('a linked box is one field and carries no tooltip either', () => {
		const html = render(BoxSides, {
			props: {
				values: { top: '8px', right: '8px', bottom: '8px', left: '8px' },
				onchange: () => {}
			}
		}).body;
		expect(html).toContain('id="box-all"');
		expect(html).not.toContain('title="8px · 8px · 8px · 8px"');
	});
});

describe('a choice too wide for the rail', () => {
	/** A `radio` property with the labels a caller declares. */
	function choice(name: string, labels: string[]): PropertyLike {
		return sub(name, 'radio', '', {
			getOptions: () => labels.map((l) => ({ id: l.toLowerCase(), label: l })),
			getOptionId: (o: { id: string }) => o.id,
			getOptionLabel: (id: string) => labels.find((l) => l.toLowerCase() === id) ?? id
		});
	}
	const body = (name: string, labels: string[]) =>
		render(PropertyControl, {
			props: { view: propertyView(choice(name, labels)), onchange: () => {} }
		}).body;

	it('becomes a dropdown rather than a Segmented that scrolls', () => {
		// `font-weight` as the document editor declares it. A Segmented scrolls instead of clipping,
		// and a scrollbar inside a ~105px control is invisible — so Bold read as ABSENT, not
		// off-screen. Asserted on `role`, because that is the difference a user can act on.
		const html = body('font-weight', ['Light', 'Normal', 'Semi', 'Bold']);
		expect(html).not.toContain('role="tablist"');
		// A CLOSED dropdown renders its trigger and none of its options on the server, so the options
		// themselves are not observable here — the control's KIND is, and that is the whole change.
		expect(html).toContain('data-slot="select-trigger"');
	});

	it('keeps a Segmented when the words do fit', () => {
		const html = body('white-space', ['Normal', 'Pre']);
		expect(html).toContain('role="tablist"');
	});

	it('keeps a Segmented whenever there are ICONS, however long the labels', () => {
		// Icons are fixed-width, so the label length says nothing about whether the row fits — and
		// these are exactly the rows the icons were hand-written for.
		const html = body('text-align', ['Left', 'Center', 'Right', 'Justify']);
		expect(html).toContain('role="tablist"');
	});
});
