import { describe, expect, it, vi } from 'vitest';
import { propertyView, type PropertyLike, type PropertyView } from './read';
import {
	BORDER_FAMILY,
	borderFamily,
	borderPlan,
	borderSub,
	borderWidthPresent,
	clearBorderPart,
	held,
	isBorderFamily,
	panelRows,
	readBorderState,
	type BorderAction,
	type BorderRole
} from './borders';
import { BORDER_STYLE_DEFAULT, BORDER_WIDTH_DEFAULT } from './css-borders.js';

/** The property MODELS faked rather than an editor booted: what the rows READ, and what an edit WRITES
 *  back — the half with teeth, since getting it wrong destroys a border or invents a box. */

const STYLE_OPTIONS = ['none', 'solid', 'dashed', 'dotted', 'double'].map((id) => ({ id }));

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
		getUnits: () => [] as string[],
		getOptions: () => [] as { id: string }[],
		getOptionId: (o: { id: string }) => o.id,
		getOptionLabel: (id: string) => id,
		getProperties: () => [] as PropertyLike[],
		...extra
	} as PropertyLike;
}

/** Faked as GrapesJS answers a number: a tidy `'3px'` string leaves `composedValue` unexercised. */
function number(name: string, value: string, units: string[], unset?: string): PropertyLike {
	const [, digits = '', unit = ''] = /^([+-]?[\d.]*)(.*)$/.exec(value) ?? [];
	return sub(name, 'number', digits, {
		getUnits: () => units,
		getMin: () => 0,
		getUnit: () => unit,
		// An unset `border-left-width` really answers `1px`; a fake returning `''` cannot reproduce the bug.
		...(value === '' && unset !== undefined
			? { getValue: () => unset, hasValue: () => false, canClear: () => false }
			: {})
	});
}

/** As `css-borders.ts::borderSideProperty` declares it: plain sub NAMES, each part answering its DEFAULT. */
function side(name: string, width = '', style = '', colour = ''): PropertyView {
	return propertyView(
		composite(name, [
			number(`${name}-width`, width, ['px', 'em'], BORDER_WIDTH_DEFAULT),
			sub(`${name}-style`, 'select', style, {
				getOptions: () => STYLE_OPTIONS,
				...(style === '' ? { getValue: () => BORDER_STYLE_DEFAULT, hasValue: () => false } : {})
			}),
			sub(`${name}-color`, 'color', colour, {
				...(colour === '' ? { getValue: () => 'black', hasValue: () => false } : {})
			})
		])
	);
}

/** The preset's `border` ids its subs `-sub` (grapes.mjs :66340) while the NAMES stay plain — built
 *  unlike the sides ON PURPOSE, being the shape an id-keyed lookup half-misses. */
function all(width = '', style = '', colour = ''): PropertyView {
	const named = (part: string, type: string, value: string, extra: object = {}): PropertyLike => ({
		...sub(`border-${part}`, type, value, extra),
		getId: () => `border-${part}-sub`
	});
	const renumber = (part: string, value: string): PropertyLike => ({
		...number(`border-${part}`, value, ['px', '%', 'em'], BORDER_WIDTH_DEFAULT),
		getId: () => `border-${part}-sub`
	});
	return propertyView(
		composite('border', [
			renumber('width', width),
			named('style', 'select', style, {
				getOptions: () => STYLE_OPTIONS,
				...(style === '' ? { getValue: () => BORDER_STYLE_DEFAULT, hasValue: () => false } : {})
			}),
			named('color', 'color', colour, {
				...(colour === '' ? { getValue: () => 'black', hasValue: () => false } : {})
			})
		])
	);
}

function composite(name: string, subs: PropertyLike[]): PropertyLike {
	return sub(name, 'composite', '', { getProperties: () => subs });
}

function other(name: string): PropertyView {
	return propertyView(sub(name, 'color', ''));
}

function rail(members: PropertyView[]): PropertyView[] {
	return [other('background-color'), ...members, other('border-radius')];
}

const SIDES = ['border-top', 'border-right', 'border-bottom', 'border-left'];

function describePlan(actions: BorderAction[]): string[] {
	return actions.map((action) =>
		'clear' in action
			? `${action.target.name} clear`
			: `${action.target.name} ${(['width', 'style', 'colour'] as BorderRole[])
					.map((role) => action.values[role])
					.filter((v) => v !== undefined)
					.join(' ')}`
	);
}

describe('the family', () => {
	it('is the all-sides shorthand and its four per-edge forms, and nothing else', () => {
		// Exactly the five the rows OWN: swallowing `border-radius` would hide a real property.
		expect([...BORDER_FAMILY]).toEqual(['border', ...SIDES]);
		expect(isBorderFamily(other('border-radius'))).toBe(false);
		expect(isBorderFamily(other('border-collapse'))).toBe(false);
		expect(isBorderFamily(side('border-left'))).toBe(true);
	});

	it('finds each part by its CSS NAME, across both id conventions', () => {
		// Ours are ided `border-left-width`, the preset's `border-width-sub`: either id alone half-misses.
		expect(held(borderSub(side('border-left', '3px'), 'width'))).toBe('3px');
		expect(held(borderSub(all('1px'), 'width'))).toBe('1px');
		expect(held(borderSub(all('', '', '#ccc'), 'colour'))).toBe('#ccc');
	});
});

describe('panelRows — five composites become one row', () => {
	it('puts one border row where the first family member sat, and drops the rest', () => {
		const views = rail([all(), ...SIDES.map((name) => side(name))]);
		const rows = panelRows(views);
		expect(rows.map((r) => (r.kind === 'borders' ? 'borders' : r.view.name))).toEqual([
			'background-color',
			'borders',
			'border-radius'
		]);
		const borders = rows.find((r) => r.kind === 'borders');
		expect(borders?.kind === 'borders' && borders.family.all?.name).toBe('border');
		expect(borders?.kind === 'borders' && Object.keys(borders.family.sides).sort()).toEqual([
			'bottom',
			'left',
			'right',
			'top'
		]);
	});

	it('leaves a sector with no border property completely alone', () => {
		const views = [other('color'), other('font-size')];
		expect(panelRows(views).map((r) => r.key)).toEqual(['color', 'font-size']);
	});

	it('still forms one section when the preset has dropped `border`', () => {
		// `configureStyleRail` degrades this way too: the four per-edge forms alone are still one border.
		const rows = panelRows(rail(SIDES.map((name) => side(name))));
		expect(rows.map((r) => r.kind)).toEqual(['property', 'borders', 'property']);
		const borders = rows[1];
		expect(borders?.kind === 'borders' && borders.family.all).toBeUndefined();
	});
});

describe('readBorderState — what the three controls show', () => {
	it("reads the Note box's shipped left stripe", () => {
		// The composite parses the shorthand into its parts (`__getPropsFromStyle`): one edge, two shared.
		const family = borderFamily([
			all(),
			side('border-top'),
			side('border-right'),
			side('border-bottom'),
			side('border-left', '3px', 'solid', '#3b82f6')
		]);
		expect(readBorderState(family)).toEqual({
			widths: { top: '', right: '', bottom: '', left: '3px' },
			style: 'solid',
			colour: '#3b82f6'
		});
		expect(Object.values(readBorderState(family).widths).some((w) => w !== '')).toBe(true);
	});

	it('lets a side with no width of its own inherit the all-sides width', () => {
		// Under `border: 1px solid #ccc` every edge IS 1px, and four blanks would contradict the canvas.
		const family = borderFamily([all('1px', 'dashed', '#ccc'), ...SIDES.map((n) => side(n))]);
		expect(readBorderState(family)).toEqual({
			widths: { top: '1px', right: '1px', bottom: '1px', left: '1px' },
			style: 'dashed',
			colour: '#ccc'
		});
	});

	it('never reads a property DEFAULT as a value', () => {
		// `getValue()` falls back to the DEFAULT, so `held` gates on `set` — `hasValue({ noParent: true })`.
		const family = borderFamily([
			all(),
			side('border-top', '1px', 'solid', '#e2e8f0'),
			side('border-right'),
			side('border-bottom'),
			side('border-left')
		]);
		expect(readBorderState(family)).toEqual({
			widths: { top: '1px', right: '', bottom: '', left: '' },
			style: 'solid',
			colour: '#e2e8f0'
		});
	});

	it('puts a numeric width back together with its unit', () => {
		// Unrecomposed, a `1.5em` border shows as `1.5px` and writes an invalid `border-left: 2 solid red`.
		const family = borderFamily([all(), ...SIDES.map((n) => side(n))]);
		expect(readBorderState(borderFamily([all(), side('border-left', '1.5em')])).widths.left).toBe(
			'1.5em'
		);
		expect(readBorderState(family).widths.left).toBe('');
		const plan = borderPlan(
			borderFamily([all(), side('border-left', '1.5em', 'dashed', 'red')]),
			readBorderState(borderFamily([all(), side('border-left', '1.5em', 'dashed', 'red')])),
			'colour'
		);
		expect(describePlan(plan).at(-1)).toBe('border-left 1.5em dashed red');
	});

	it('is empty for an element with no border at all', () => {
		// Every part answers its DEFAULT, so reading values rather than `set` invents a 1px solid box.
		const family = borderFamily([all(), ...SIDES.map((n) => side(n))]);
		expect(readBorderState(family)).toEqual({
			widths: { top: '', right: '', bottom: '', left: '' },
			style: '',
			colour: ''
		});
	});
});

describe('borderWidthPresent — a zero width is not a border', () => {
	it('counts nothing, and zero in any unit, as absent', () => {
		// What lets an edge be switched off by clearing rather than writing `border-left: 0 solid …`.
		for (const value of ['', '   ', '0', '0px', '0em', '0.0px'])
			expect(borderWidthPresent(value)).toBe(false);
	});

	it('counts a real width, and anything it cannot parse, as present', () => {
		for (const value of ['1px', '3px', '0.5em', 'thin'])
			expect(borderWidthPresent(value)).toBe(true);
	});
});

describe('borderPlan — what an edit writes', () => {
	const noteBox = () =>
		borderFamily([
			all(),
			side('border-top'),
			side('border-right'),
			side('border-bottom'),
			side('border-left', '3px', 'solid', '#3b82f6')
		]);

	it('EVERY declaration it produces is a border shorthand, never a longhand', () => {
		// A non-detached composite joins its parts into `border-{side}: W S C` and blanks the longhands.
		const family = noteBox();
		const plans = [
			borderPlan(family, { ...readBorderState(family), colour: '#b91c1c' }, 'colour'),
			borderPlan(
				family,
				{
					widths: { top: '1px', right: '', bottom: '', left: '3px' },
					style: 'solid',
					colour: '#3b82f6'
				},
				'width'
			),
			borderPlan(
				family,
				{
					widths: { top: '2px', right: '2px', bottom: '2px', left: '2px' },
					style: 'dashed',
					colour: '#000'
				},
				'width'
			)
		];
		for (const plan of plans)
			for (const action of plan) expect(BORDER_FAMILY).toContain(action.target.name);
	});

	it('recolours the Note box stripe WITHOUT putting a box on the other three sides', () => {
		// One shared colour must not box a left stripe: widthless edges are CLEARED, never defaulted.
		const family = noteBox();
		const plan = borderPlan(family, { ...readBorderState(family), colour: '#b91c1c' }, 'colour');
		expect(describePlan(plan)).toEqual([
			'border clear',
			'border-top clear',
			'border-right clear',
			'border-bottom clear',
			'border-left 3px solid #b91c1c'
		]);
	});

	it('adds a second edge as its own per-edge shorthand, sharing style and colour', () => {
		const family = noteBox();
		const plan = borderPlan(
			family,
			{
				widths: { top: '1px', right: '', bottom: '', left: '3px' },
				style: 'solid',
				colour: '#3b82f6'
			},
			'width'
		);
		expect(describePlan(plan)).toEqual([
			'border clear',
			'border-top 1px solid #3b82f6',
			'border-right clear',
			'border-bottom clear',
			'border-left 3px solid #3b82f6'
		]);
	});

	it('collapses four equal widths to the ALL-SIDES shorthand, one declaration not four', () => {
		const plan = borderPlan(
			noteBox(),
			{
				widths: { top: '2px', right: '2px', bottom: '2px', left: '2px' },
				style: 'dashed',
				colour: '#000'
			},
			'width'
		);
		expect(describePlan(plan)).toEqual([
			'border-top clear',
			'border-right clear',
			'border-bottom clear',
			'border-left clear',
			'border 2px dashed #000'
		]);
	});

	it('falls back to the per-edge form when the rail has no `border` to write', () => {
		const family = borderFamily(SIDES.map((name) => side(name)));
		const plan = borderPlan(
			family,
			{
				widths: { top: '2px', right: '2px', bottom: '2px', left: '2px' },
				style: 'solid',
				colour: '#000'
			},
			'width'
		);
		expect(describePlan(plan)).toEqual([
			'border-top 2px solid #000',
			'border-right 2px solid #000',
			'border-bottom 2px solid #000',
			'border-left 2px solid #000'
		]);
	});

	it('turns a colour-only edit into a border, because a colour with no width draws nothing', () => {
		// A colour with no width draws nothing, so all four take `BORDER_WIDTH_DEFAULT` — hence uniform.
		const family = borderFamily([all(), ...SIDES.map((n) => side(n))]);
		const plan = borderPlan(
			family,
			{ widths: { top: '', right: '', bottom: '', left: '' }, style: '', colour: '#b91c1c' },
			'colour'
		);
		expect(describePlan(plan)).toEqual([
			'border-top clear',
			'border-right clear',
			'border-bottom clear',
			'border-left clear',
			`border ${BORDER_WIDTH_DEFAULT} ${BORDER_STYLE_DEFAULT} #b91c1c`
		]);
	});

	it('does NOT resurrect a border when the last width is zeroed', () => {
		// Why `borderPlan` is told WHICH control was used: zeroing the last width must not box the element.
		const family = noteBox();
		const plan = borderPlan(
			family,
			{ widths: { top: '', right: '', bottom: '', left: '0' }, style: 'solid', colour: '#3b82f6' },
			'width'
		);
		expect(describePlan(plan)).toEqual([
			'border clear',
			'border-top clear',
			'border-right clear',
			'border-bottom clear',
			'border-left clear'
		]);
	});

	it('WRITES a blank colour, rather than omitting the key', () => {
		// This pinned the opposite, and the opposite was a bug. `apply` iterates only the keys it is
		// handed, so omitting a blank colour left the PREVIOUS colour on the element: Clear in the
		// picker did nothing at all, the row still read as set, and there was no route back to the
		// composite's default. Writing `''` is how `clearBorderPart` unsets a part in this same file.
		//
		// The composite still fills its own `black` on the way out (`getStyleFromProps` joins
		// `withDefault`), which is what the old comment here was really about — that is a property of
		// the READ, and it never justified skipping the write.
		const family = borderFamily([all(), ...SIDES.map((n) => side(n))]);
		const plan = borderPlan(
			family,
			{ widths: { top: '', right: '', bottom: '', left: '2px' }, style: '', colour: '' },
			'width'
		);
		const left = plan.find((a) => a.target.name === 'border-left');
		expect(left && 'values' in left && left.values).toEqual({
			width: '2px',
			style: BORDER_STYLE_DEFAULT,
			colour: ''
		});
	});

	it('clears the colour on the all-sides shorthand too', () => {
		// The uniform branch shares `parts`, so the fix has to reach it as well — a uniform border is
		// the commonest shape, and it is the one an author is most likely to have coloured.
		const family = borderFamily([all(), ...SIDES.map((n) => side(n))]);
		const plan = borderPlan(
			family,
			{ widths: { top: '1px', right: '1px', bottom: '1px', left: '1px' }, style: '', colour: '' },
			'width'
		);
		const shorthand = plan.find((a) => a.target.name === 'border');
		expect(shorthand && 'values' in shorthand && shorthand.values).toEqual({
			width: '1px',
			style: BORDER_STYLE_DEFAULT,
			colour: ''
		});
	});
});

describe('clearBorderPart — the Style and Colour rows own clears', () => {
	it('unsets one part across the whole family, and never the widths', () => {
		// NOT `clear all`: unsetting the colour returns the visible `black` default; a width would delete it.
		const family = borderFamily([all('1px', 'dashed', '#ccc'), ...SIDES.map((n) => side(n))]);
		expect(describePlan(clearBorderPart(family, 'colour'))).toEqual([
			'border ',
			'border-top ',
			'border-right ',
			'border-bottom ',
			'border-left '
		]);
		for (const action of clearBorderPart(family, 'colour'))
			expect('values' in action && action.values).toEqual({ colour: '' });
		for (const action of clearBorderPart(family, 'style'))
			expect('values' in action && action.values).toEqual({ style: '' });
	});
});
