import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
	allSides,
	anySideSet,
	commonSide,
	describeSides,
	firstSide,
	joinSides,
	partGlyph,
	SIDE_ORDER,
	SIDE_PARTS,
	sidesLinked,
	splitSides,
	type PartValues
} from './box-sides';

const box = (top: string, right = top, bottom = top, left = right): PartValues => ({
	top,
	right,
	bottom,
	left
});

/** The corner arrangement, proving the module is parts-generic: `border-radius` is the four CORNERS. */
const corners: PartValues = {
	'border-top-left-radius': '4px',
	'border-top-right-radius': '4px',
	'border-bottom-right-radius': '0',
	'border-bottom-left-radius': '0'
};

describe('SIDE_ORDER / SIDE_PARTS', () => {
	it('is CSS shorthand order', () => {
		expect([...SIDE_ORDER]).toEqual(['top', 'right', 'bottom', 'left']);
		expect(SIDE_PARTS.map((p) => p.key)).toEqual(['top', 'right', 'bottom', 'left']);
	});
});

describe('sidesLinked', () => {
	it('is true when every part matches', () => {
		expect(sidesLinked(box('10px'))).toBe(true);
	});

	it('is true for an all-blank box', () => {
		expect(sidesLinked(box(''))).toBe(true);
	});

	it('is true for an empty record', () => {
		// A property whose composite reported no sub-properties: nothing can disagree.
		expect(sidesLinked({})).toBe(true);
	});

	it('is false as soon as one part differs', () => {
		expect(sidesLinked({ top: '10px', right: '24px', bottom: '10px', left: '24px' })).toBe(false);
		expect(sidesLinked({ top: '10px', right: '10px', bottom: '10px', left: '' })).toBe(false);
		expect(sidesLinked(corners)).toBe(false);
	});

	it('does not treat different units as the same', () => {
		expect(sidesLinked({ top: '1em', right: '16px', bottom: '1em', left: '16px' })).toBe(false);
	});
});

describe('commonSide', () => {
	it('gives the shared value when the parts are linked', () => {
		expect(commonSide(box('10px'))).toBe('10px');
	});

	it('gives blank for a mixed box rather than the first part', () => {
		// Showing `10px` for a `10px 24px` box claims the box is 10px, and the next edit overwrites two sides the author never saw.
		expect(commonSide({ top: '10px', right: '24px', bottom: '10px', left: '24px' })).toBe('');
		expect(commonSide(corners)).toBe('');
	});
});

describe('allSides', () => {
	it('sets every part, keeping the keys it was given', () => {
		expect(allSides(box('0'), '8px')).toEqual({
			top: '8px',
			right: '8px',
			bottom: '8px',
			left: '8px'
		});
	});

	it('works for a non-side part set', () => {
		expect(Object.keys(allSides(corners, '2px'))).toEqual(Object.keys(corners));
		expect(Object.values(allSides(corners, '2px'))).toEqual(['2px', '2px', '2px', '2px']);
	});

	it('can clear every part', () => {
		expect(anySideSet(allSides(box('4px'), ''))).toBe(false);
	});
});

describe('anySideSet', () => {
	it('is true when one part carries a value', () => {
		expect(anySideSet({ top: '', right: '', bottom: '4px', left: '' })).toBe(true);
	});

	it('is false for blanks and whitespace', () => {
		expect(anySideSet(box(''))).toBe(false);
		expect(anySideSet(box('   '))).toBe(false);
		expect(anySideSet({})).toBe(false);
	});
});

describe('describeSides', () => {
	it('lists the parts in the record’s own order', () => {
		expect(describeSides({ top: '10px', right: '24px', bottom: '1px', left: '2px' })).toBe(
			'10px · 24px · 1px · 2px'
		);
	});

	it('holds a blank part open so the positions stay countable', () => {
		expect(describeSides({ top: '10px', right: '', bottom: '', left: '2px' })).toBe(
			'10px · — · — · 2px'
		);
	});
});

describe('firstSide', () => {
	it('is what a collapse chooses', () => {
		expect(firstSide({ top: '10px', right: '24px', bottom: '10px', left: '24px' })).toBe('10px');
		expect(firstSide({})).toBe('');
	});

	it('is the first part that HAS a value, not the first part', () => {
		// The one-sided box (a Note Box's `border-left`) has a blank Top, so `values.top` meant collapsing the control wiped the author's only value.
		expect(firstSide({ top: '', right: '', bottom: '', left: '3px' })).toBe('3px');
		expect(firstSide({ top: '', right: '2px', bottom: '', left: '3px' })).toBe('2px');
	});

	it('ignores a whitespace-only part, the way anySideSet does', () => {
		expect(firstSide({ top: '   ', left: '4px' })).toBe('4px');
		expect(firstSide({ top: '   ' })).toBe('');
	});

	it('reads a numeric part rather than returning a number', () => {
		expect(firstSide({ top: '', right: 12 as unknown as string })).toBe('12');
	});
});

describe('a part value that is not a string', () => {
	// Same class as the `splitDimension` bug: a style model answers a numeric property with a number.
	it('counts a numeric part as set, including zero', () => {
		expect(anySideSet({ top: 12 as unknown as string })).toBe(true);
		expect(anySideSet({ top: 0 as unknown as string })).toBe(true);
	});

	it('describes a numeric part rather than throwing', () => {
		expect(describeSides({ top: 12 as unknown as string, right: '' })).toBe('12 · —');
	});
});

describe('splitSides / joinSides — the page inset is a plain shorthand', () => {
	// The PAGE is not selectable, so it has no property models: its inset is a plain shorthand string.
	it('applies the CSS shorthand rules', () => {
		expect(splitSides('8px')).toEqual({ top: '8px', right: '8px', bottom: '8px', left: '8px' });
		expect(splitSides('8px 12px')).toEqual({
			top: '8px',
			right: '12px',
			bottom: '8px',
			left: '12px'
		});
		expect(splitSides('32px 32px 36px')).toEqual({
			top: '32px',
			right: '32px',
			bottom: '36px',
			left: '32px'
		});
		expect(splitSides('1px 2px 3px 4px')).toEqual({
			top: '1px',
			right: '2px',
			bottom: '3px',
			left: '4px'
		});
	});

	it('reads an absent or unreadable value as unset rather than guessing', () => {
		const blank = { top: '', right: '', bottom: '', left: '' };
		expect(splitSides('')).toEqual(blank);
		expect(splitSides(null)).toEqual(blank);
		expect(splitSides(undefined)).toEqual(blank);
		expect(splitSides('1px 2px 3px 4px 5px')).toEqual(blank);
	});

	it('tolerates the whitespace a hand-written value carries', () => {
		expect(splitSides('  32px   32px  36px ')).toEqual({
			top: '32px',
			right: '32px',
			bottom: '36px',
			left: '32px'
		});
	});

	it('writes the SHORTEST shorthand that means the same thing', () => {
		expect(joinSides({ top: '8px', right: '8px', bottom: '8px', left: '8px' })).toBe('8px');
		expect(joinSides({ top: '8px', right: '12px', bottom: '8px', left: '12px' })).toBe('8px 12px');
		expect(joinSides({ top: '32px', right: '32px', bottom: '36px', left: '32px' })).toBe(
			'32px 32px 36px'
		);
		expect(joinSides({ top: '1px', right: '2px', bottom: '3px', left: '4px' })).toBe(
			'1px 2px 3px 4px'
		);
	});

	it('answers empty for all-blank, so the caller can clear the declaration', () => {
		expect(joinSides({ top: '', right: '', bottom: '', left: '' })).toBe('');
	});

	it('fills a single blank side with 0 rather than emitting invalid CSS', () => {
		expect(joinSides({ top: '8px', right: '', bottom: '8px', left: '' })).toBe('8px 0');
	});

	it('round-trips every shape it emits', () => {
		for (const value of ['8px', '8px 12px', '32px 32px 36px', '1px 2px 3px 4px', '0']) {
			expect(joinSides(splitSides(value))).toBe(value);
		}
	});
});

/** The four-up is a 2×2 GRID with the toggle beside it. A labelled column was four rows tall; a
 *  labelled two-up lost the numbers, because a 6.5rem label left each column room for the unit and
 *  nothing else. Both reasons were the LABEL — the side is a glyph inside the field now, and the row
 *  stacks, so the grid has the panel's whole width. */
describe('the split four-up', () => {
	const control = readFileSync(new URL('./box-sides.svelte', import.meta.url), 'utf8');

	it('is a plain two-up: the toggle is not in the grid at all', () => {
		expect(control).toMatch(/grid grid-cols-2/);
		expect(control).toMatch(/\{#snippet modeToggle\(\)\}/);
	});

	it('wears the side INSIDE the field, and keeps the word as the accessible name', () => {
		expect(control).toMatch(/\{#snippet adornment\(\)\}/);
		expect(control).toMatch(/aria-label=\{part\.label\}/);
	});

	it('does not ask its row to restack — that is what split the label from the toggle', () => {
		expect(control).not.toMatch(/onsplit/);
	});

	it('draws its OWN row, and every caller lets it', () => {
		// The two modes want different rows and only the box knows which it is in, so a caller that
		// wrapped it in a `PropertyRow` would pick one of them and be wrong half the time.
		expect(control).toMatch(/import PropertyRow/);
		expect(control).toMatch(/headerEnd=\{modeToggle\}/);
		// Paths from `components/`, because the callers no longer share a parent: the style rail is
		// shared machinery under `grapes/` and only the email page-style panel is editor-local.
		const dir = new URL('../../', import.meta.url);
		for (const file of [
			'email-editor/page-style.svelte',
			'grapes/style-panel/border-control.svelte',
			'grapes/style-panel/property-control.svelte'
		]) {
			const source = readFileSync(new URL(file, dir), 'utf8');
			// `<BoxSides` is mounted with a label of its own and NOT inside a row. Nesting is read by
			// balance rather than proximity: page-style has an unrelated row shortly above this one.
			const at = source.indexOf('<BoxSides');
			expect(at, file).toBeGreaterThan(-1);
			expect(source.slice(at, at + 400), file).toMatch(/label=/);
			const before = source.slice(0, at);
			// Equal at -1 is the honest pass: a file whose box is its first row has no row to be inside.
			expect(before.lastIndexOf('</PropertyRow>') >= before.lastIndexOf('<PropertyRow'), file).toBe(
				true
			);
		}
	});
});

/** The glyph is read out of the part's own key and label: the keys are GrapesJS property ids, so a
 *  table of them here would go stale the moment the preset declared another composite. */
describe('partGlyph', () => {
	it('names a side from a bare side part', () => {
		expect(partGlyph({ key: 'top', label: 'top' })).toEqual({ kind: 'side', side: 'top' });
		expect(partGlyph({ key: 'left', label: 'left' })).toEqual({ kind: 'side', side: 'left' });
	});

	it('names a CORNER, short, from a border-radius sub-property', () => {
		expect(
			partGlyph({ key: 'border-top-left-radius-sub', label: 'border-top-left-radius' })
		).toEqual({ kind: 'corner', short: 'TL' });
		expect(
			partGlyph({ key: 'border-bottom-right-radius-sub', label: 'border-bottom-right-radius' })
		).toEqual({ kind: 'corner', short: 'BR' });
	});

	it('answers null for a part that names no side, so its label survives', () => {
		expect(partGlyph({ key: 'gap', label: 'gap' })).toBeNull();
	});
});
