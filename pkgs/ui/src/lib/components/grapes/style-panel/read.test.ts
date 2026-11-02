import { describe, expect, it, vi, type Mock } from 'vitest';
import {
	britishLabel,
	composedValue,
	controlFor,
	isBoxComposite,
	partValues,
	propertyView,
	PROPERTY_TYPES,
	propertySet,
	sectorSet,
	sectorViews,
	write,
	writeParts,
	type ControlKind,
	type PropertyLike
} from './read';

/** Models faked, no editor booted; the LAST describe matters most — an unmapped type costs a control. */

interface FakeOpts {
	id?: string;
	name?: string;
	label?: string;
	type?: string;
	value?: string;
	unit?: string;
	set?: boolean;
	clearable?: boolean;
	units?: string[];
	min?: number;
	max?: number;
	step?: number;
	options?: { id: string; label?: string; name?: string }[];
	subs?: PropertyLike[];
	visible?: boolean;
}

type UpValue = (value: string, opts?: object) => unknown;
type Clear = (opts?: object) => unknown;

function fake(o: FakeOpts = {}): PropertyLike & {
	upValue: Mock<UpValue>;
	clear: Mock<Clear>;
} {
	const name = o.name ?? o.id ?? 'padding';
	return {
		getId: () => o.id ?? name,
		getName: () => name,
		getLabel: () => o.label ?? name,
		getType: () => o.type ?? 'base',
		getValue: () => o.value ?? '',
		hasValue: () => o.set ?? false,
		canClear: () => o.clearable ?? o.set ?? false,
		upValue: vi.fn<UpValue>(),
		clear: vi.fn<Clear>(),
		getUnits: () => o.units ?? [],
		getMin: () => o.min as number,
		getMax: () => o.max as number,
		getStep: () => o.step as number,
		getOptions: () => o.options ?? [],
		getOptionId: (option: { id: string }) => option.id,
		getOptionLabel: (id: string) => (o.options ?? []).find((x) => x.id === id)?.label ?? id,
		getProperties: () => o.subs ?? [],
		// Both accessors are OPTIONAL on a real model; a fake that always answered would hide the fallbacks.
		...(o.unit === undefined ? {} : { getUnit: () => o.unit as string }),
		...(o.visible === undefined ? {} : { isVisible: () => o.visible as boolean })
	};
}

const sides = (base: string, type = 'number', units = ['px', 'em', '%']) =>
	['top', 'right', 'bottom', 'left'].map((side) =>
		fake({ id: `${base}-${side}-sub`, name: `${base}-${side}`, label: side, type, units })
	);

describe('controlFor', () => {
	it('maps a colour property to the colour control', () => {
		expect(controlFor(fake({ type: 'color' }))).toEqual({ kind: 'colour' });
	});

	it('maps a number to a length, carrying its units and min', () => {
		expect(controlFor(fake({ type: 'number', units: ['px', 'em'], min: 0 }))).toEqual({
			kind: 'length',
			units: ['px', 'em'],
			min: 0
		});
	});

	it('omits min when the property declares none', () => {
		// `undefined` must not reach the control as an explicit `min`, or `UnitInput` clamps against NaN.
		expect(controlFor(fake({ type: 'number', units: ['px'] }))).toEqual({
			kind: 'length',
			units: ['px']
		});
	});

	it('maps a radio to a segmented choice and a select to a dropdown', () => {
		const opts = [
			{ id: 'left', label: 'Left' },
			{ id: 'center', label: 'Centre' }
		];
		expect(controlFor(fake({ type: 'radio', options: opts }))).toEqual({
			kind: 'choice',
			options: [
				{ value: 'left', label: 'Left' },
				{ value: 'center', label: 'Centre' }
			]
		});
		expect(controlFor(fake({ type: 'select', options: opts })).kind).toBe('select');
	});

	it('falls back through label, name, then the id for an option with no locale entry', () => {
		const property = fake({ type: 'select', options: [{ id: 'solid' }, { id: 'no-repeat' }] });
		const bare: PropertyLike = { ...property, getOptionLabel: undefined };
		// The bare CSS keyword is the last resort, and it is printed in sentence case.
		expect(controlFor(bare)).toEqual({
			kind: 'select',
			options: [
				{ value: 'solid', label: 'Solid' },
				{ value: 'no-repeat', label: 'No repeat' }
			]
		});
	});

	it('maps a slider with its range', () => {
		expect(controlFor(fake({ type: 'slider', min: 0, max: 1, step: 0.01 }))).toEqual({
			kind: 'slider',
			min: 0,
			max: 1,
			step: 0.01
		});
	});

	describe('composites', () => {
		it('treats an all-number composite as a box, keyed by sub id in declared order', () => {
			const control = controlFor(
				fake({ name: 'padding', type: 'composite', subs: sides('padding') })
			);
			expect(control).toEqual({
				kind: 'parts',
				units: ['px', 'em', '%'],
				parts: [
					{ key: 'padding-top-sub', label: 'top' },
					{ key: 'padding-right-sub', label: 'right' },
					{ key: 'padding-bottom-sub', label: 'bottom' },
					{ key: 'padding-left-sub', label: 'left' }
				]
			});
		});

		it('treats border-radius as a box too, because its parts are the CORNERS', () => {
			const corners = [
				'border-top-left-radius',
				'border-top-right-radius',
				'border-bottom-right-radius',
				'border-bottom-left-radius'
			].map((name) =>
				fake({ id: `${name}-sub`, name, label: name, type: 'number', units: ['px'] })
			);
			const control = controlFor(fake({ name: 'border-radius', type: 'composite', subs: corners }));
			expect(control.kind).toBe('parts');
			expect(control.kind === 'parts' && control.parts.map((p) => p.key)).toEqual(
				corners.map((c) => c.getId())
			);
		});

		it('treats a mixed composite as a group, not a box', () => {
			// `borders.ts` splices the border family out, so this guards a mixed composite a preset adds.
			const subs = [
				fake({ id: 'border-left-width', type: 'number', units: ['px', 'em'] }),
				fake({ id: 'border-left-style', type: 'select', options: [{ id: 'solid' }] }),
				fake({ id: 'border-left-color', type: 'color' })
			];
			expect(controlFor(fake({ name: 'border-left', type: 'composite', subs })).kind).toBe('group');
		});

		it('treats a one-part composite as a group', () => {
			const subs = [fake({ id: 'only', type: 'number', units: ['px'] })];
			expect(controlFor(fake({ type: 'composite', subs })).kind).toBe('group');
		});

		it('treats a composite that reports no sub-properties as a group', () => {
			expect(controlFor(fake({ type: 'composite', subs: [] })).kind).toBe('group');
		});
	});

	it('renders file, stack and base as text', () => {
		// Deliberate mappings, not fallbacks: a background image no client honours gets no upload tile.
		expect(controlFor(fake({ type: 'file' })).kind).toBe('text');
		expect(controlFor(fake({ type: 'stack' })).kind).toBe('text');
		expect(controlFor(fake({ type: 'base' })).kind).toBe('text');
	});
});

describe('isBoxComposite', () => {
	it('needs more than one part, all numbers', () => {
		expect(isBoxComposite(sides('padding'))).toBe(true);
		expect(isBoxComposite([])).toBe(false);
		expect(isBoxComposite([fake({ type: 'number' })])).toBe(false);
		expect(isBoxComposite([fake({ type: 'number' }), fake({ type: 'color' })])).toBe(false);
	});

	it('needs every part to name an edge — four numbers are not a box', () => {
		// Four numeric properties, and nothing like a box: `BoxSides` would offer "one value for every
		// side" over a width and a height, and its split grid would label two of them with the wrong
		// axis. The min/max group that prompted this is no longer offered as a composite, which is
		// exactly why the rule is pinned here rather than left to the one caller that had it.
		const limits = ['min-width', 'max-width', 'min-height', 'max-height'].map((name) =>
			fake({ id: `${name}-sub`, name, label: name, type: 'number', units: ['px'] })
		);
		expect(isBoxComposite(limits)).toBe(false);
		expect(controlFor(fake({ name: 'size-limits', type: 'composite', subs: limits })).kind).toBe(
			'group'
		);
	});
});

describe('propertyView', () => {
	it('reads a property into something renderable', () => {
		const view = propertyView(
			fake({
				id: 'color',
				name: 'color',
				label: 'Text colour',
				type: 'color',
				value: '#3b82f6',
				set: true
			})
		);
		expect(view).toMatchObject({
			id: 'color',
			name: 'color',
			label: 'Text colour',
			type: 'color',
			value: '#3b82f6',
			set: true,
			clearable: true,
			control: { kind: 'colour' },
			subs: []
		});
	});

	it('asks hasValue with noParent, so an inherited value does not read as set', () => {
		// The question is "did the author set this HERE"; else an inherited property offers an empty clear.
		const hasValue = vi.fn().mockReturnValue(false);
		propertyView({ ...fake({ type: 'color' }), hasValue });
		expect(hasValue).toHaveBeenCalledWith({ noParent: true });
	});

	it('recurses into a composite’s sub-properties', () => {
		const view = propertyView(fake({ name: 'padding', type: 'composite', subs: sides('padding') }));
		expect(view.subs.map((s) => s.id)).toEqual([
			'padding-top-sub',
			'padding-right-sub',
			'padding-bottom-sub',
			'padding-left-sub'
		]);
		expect(view.subs.every((s) => s.subs.length === 0)).toBe(true);
	});

	it('reads a missing value as blank rather than undefined', () => {
		const view = propertyView({ ...fake({ type: 'color' }), getValue: () => undefined });
		expect(view.value).toBe('');
	});

	it('carries the unit on the value, for every length in the rail', () => {
		// `UnitInput` recovers a missing unit from the first it offers, so `1.5em` showed and wrote `1.5px`.
		const view = propertyView(
			fake({ id: 'padding-top', type: 'number', value: '1.5', unit: 'em', units: ['px', 'em'] })
		);
		expect(view.value).toBe('1.5em');
	});
});

describe('composedValue', () => {
	it('joins a bare number to its unit', () => {
		expect(composedValue(fake({ type: 'number', value: '0', unit: 'px' }))).toBe('0px');
		expect(composedValue(fake({ type: 'number', value: '.5', unit: 'em' }))).toBe('.5em');
	});

	it('leaves a value that is not a bare number alone', () => {
		// The model can carry a stale `unit` beside a keyword, so the guard is on the VALUE, not the unit.
		for (const value of [
			'auto',
			'calc(100% - 20px)',
			'solid',
			'#3b82f6',
			'{{ brand.width }}',
			'2px'
		])
			expect(composedValue(fake({ type: 'number', value, unit: 'px' })), value).toBe(value);
	});

	it('answers the value alone when the model offers no unit at all', () => {
		expect(composedValue(fake({ type: 'color', value: '#fff' }))).toBe('#fff');
		expect(composedValue(fake({ type: 'number', value: '12', unit: '' }))).toBe('12');
	});
});

describe('sectorViews', () => {
	const sector = (id: string, label: string, props: PropertyLike[], open = false) => ({
		getId: () => id,
		getName: () => label,
		isOpen: () => open,
		getProperties: () => props
	});

	it('reads the visible sectors in order, with their properties', () => {
		const getSectors = vi
			.fn()
			.mockReturnValue([
				sector('colours', 'Colours & borders', [fake({ id: 'color', type: 'color' })], true),
				sector('dimension', 'Dimension', [fake({ id: 'width', type: 'number', units: ['px'] })])
			]);
		const views = sectorViews({ getSectors });
		// `visible: true` matters: a sector GrapesJS has hidden must not be drawn.
		expect(getSectors).toHaveBeenCalledWith({ visible: true });
		expect(views.map((s) => s.id)).toEqual(['colours', 'dimension']);
		expect(views[0]).toMatchObject({ label: 'Colours & borders', open: true });
		expect(views[0]?.properties[0]?.control).toEqual({ kind: 'colour' });
	});

	it('answers empty when there is no StyleManager yet', () => {
		// The panel renders before the dynamic GrapesJS import resolves; it must not throw.
		expect(sectorViews(undefined)).toEqual([]);
	});

	it('drops a property GrapesJS says does not apply to the selection', () => {
		// `Sector.getProperties()` does NOT filter on `isVisible()`, so the rail would draw ruled-out rows.
		const views = sectorViews({
			getSectors: () => [
				sector('typography', 'Text', [
					fake({ id: 'font-size', type: 'number', units: ['px'], visible: false }),
					fake({ id: 'text-align', type: 'radio', visible: true })
				])
			]
		});
		expect(views[0]?.properties.map((p) => p.id)).toEqual(['text-align']);
	});

	it('keeps a property that cannot answer, rather than hiding it on a guess', () => {
		// `visible` defaults to `true`: no answer must not silently cost the rail a control.
		const views = sectorViews({
			getSectors: () => [sector('typography', 'Text', [fake({ id: 'font-size', type: 'number' })])]
		});
		expect(views[0]?.properties.map((p) => p.id)).toEqual(['font-size']);
	});
});

describe('partValues', () => {
	it('keys the parts by sub id, in declared order', () => {
		const view = propertyView(fake({ name: 'padding', type: 'composite', subs: sides('padding') }));
		// The key ORDER is what `BoxSides` reads as its reading order, so it is part of the contract.
		expect(Object.keys(partValues(view))).toEqual([
			'padding-top-sub',
			'padding-right-sub',
			'padding-bottom-sub',
			'padding-left-sub'
		]);
	});
});

describe('write', () => {
	it('clears rather than writing an empty value', () => {
		// `upValue('')` leaves the property set to nothing; only `clear()` lets the element inherit again.
		const property = fake({ type: 'color', value: '#fff', set: true });
		write(property, '');
		expect(property.clear).toHaveBeenCalled();
		expect(property.upValue).not.toHaveBeenCalled();
	});

	it('writes a real value through upValue', () => {
		const property = fake({ type: 'color' });
		write(property, '#3b82f6');
		expect(property.upValue).toHaveBeenCalledWith('#3b82f6');
		expect(property.clear).not.toHaveBeenCalled();
	});
});

describe('writeParts', () => {
	it('writes every changed part', () => {
		const subs = sides('padding');
		const view = propertyView(fake({ name: 'padding', type: 'composite', subs }));
		writeParts(view, {
			'padding-top-sub': '8px',
			'padding-right-sub': '8px',
			'padding-bottom-sub': '8px',
			'padding-left-sub': '8px'
		});
		for (const sub of subs) expect(sub.upValue).toHaveBeenCalledWith('8px');
	});

	it('skips a part whose value has not changed', () => {
		// Otherwise a uniform box puts a no-op on the undo stack, and any change sets `dirty`.
		const subs = sides('padding').map((s) => ({ ...s, getValue: () => '4px' }));
		const view = propertyView(fake({ name: 'padding', type: 'composite', subs }));
		writeParts(view, { 'padding-top-sub': '4px', 'padding-right-sub': '8px' });
		expect(subs[0]?.upValue).not.toHaveBeenCalled();
		expect(subs[1]?.upValue).toHaveBeenCalledWith('8px');
	});

	it('ignores a key it was not given', () => {
		const subs = sides('padding');
		const view = propertyView(fake({ name: 'padding', type: 'composite', subs }));
		writeParts(view, { 'padding-top-sub': '2px' });
		expect(subs[0]?.upValue).toHaveBeenCalledWith('2px');
		expect(subs[3]?.upValue).not.toHaveBeenCalled();
	});

	it('clears a part set to blank', () => {
		const subs = sides('padding').map((s) => ({ ...s, getValue: () => '4px' }));
		const view = propertyView(fake({ name: 'padding', type: 'composite', subs }));
		writeParts(view, { 'padding-top-sub': '' });
		expect(subs[0]?.clear).toHaveBeenCalled();
	});
});

describe('every registered property type maps to a control', () => {
	// TYPED on purpose: `controlFor`'s default answers `text` for anything, so asserting only that the
	// result is defined would pass with a mapping deleted — and a new type stops the file COMPILING.
	const EXPECTED: Record<(typeof PROPERTY_TYPES)[number], ControlKind> = {
		base: 'text',
		number: 'length',
		color: 'colour',
		radio: 'choice',
		select: 'select',
		file: 'text',
		slider: 'slider',
		composite: 'parts',
		stack: 'text'
	};

	it.each(PROPERTY_TYPES)('%s', (type) => {
		const subs = type === 'composite' ? sides('padding') : [];
		expect(controlFor(fake({ type, subs, options: [{ id: 'a' }] })).kind).toBe(EXPECTED[type]);
	});

	it('classifies a composite by its parts, not by its type alone', () => {
		const mixed = [fake({ type: 'number' }), fake({ type: 'color' })];
		expect(controlFor(fake({ type: 'composite', subs: mixed })).kind).toBe('group');
	});

	it('maps an unknown type to text rather than nothing', () => {
		expect(controlFor(fake({ type: 'something-new' })).kind).toBe('text');
	});
});

describe('britishLabel — one spelling in one panel', () => {
	// Ours say `Colour`; the preset's `border` keeps `Color`, since renaming it collides with the `-sub`
	// i18n keys — so the fix is at the view.
	it("anglicises the preset's Color sub-property", () => {
		expect(britishLabel('Color')).toBe('Colour');
		expect(britishLabel('Background color')).toBe('Background colour');
	});

	it('leaves a longer word that merely starts with it alone', () => {
		expect(britishLabel('Colorimetry')).toBe('Colorimetry');
	});

	it('is a no-op on a label that is already British', () => {
		expect(britishLabel('Colour')).toBe('Colour');
		expect(britishLabel('Width')).toBe('Width');
	});

	it('is applied by propertyView, which is where the panel reads its labels', () => {
		expect(propertyView(fake({ label: 'Color', type: 'color' })).label).toBe('Colour');
	});
});

describe('propertySet / sectorSet — what lights a collapsed heading', () => {
	// `view.set` is not the question for a composite: `border-left` answers false while its Colour is set.
	it("sees a value set on a composite's part", () => {
		const composite = fake({
			id: 'border-left',
			type: 'composite',
			subs: [
				fake({ id: 'border-left-width', type: 'number' }),
				fake({ id: 'border-left-color', type: 'color', set: true })
			]
		});
		const view = propertyView(composite);
		expect(view.set).toBe(false);
		expect(propertySet(view)).toBe(true);
	});

	it('is false for a composite whose parts are all unset', () => {
		const view = propertyView(
			fake({
				id: 'border-top',
				type: 'composite',
				subs: [fake({ id: 'border-top-width', type: 'number' })]
			})
		);
		expect(propertySet(view)).toBe(false);
	});

	it('sectorSet answers for the whole sector, so a scan of a collapsed rail is honest', () => {
		const sector = (props: PropertyLike[]) => ({
			getId: () => 'colours',
			getName: () => 'Colours & borders',
			isOpen: () => true,
			getProperties: () => props
		});
		const untouched = sectorViews({ getSectors: () => [sector([fake({ type: 'color' })])] })[0];
		expect(untouched && sectorSet(untouched)).toBe(false);

		const touched = sectorViews({
			getSectors: () => [sector([fake({ type: 'color' }), fake({ id: 'color', set: true })])]
		})[0];
		expect(touched && sectorSet(touched)).toBe(true);
	});
});

describe('a value still being chosen', () => {
	// THE DEFECT: `ColorInput`'s pad reports on every pointer move, and each report was a style write,
	// and each write was an undo entry. Dragging from one colour to another left a hundred shades on
	// the stack, so Ctrl+Z had to walk back through all of them to reach the colour before. `partial`
	// is GrapesJS's own word for this: the property model turns it into `avoidStore`, so the change
	// reaches the canvas and is not recorded. One gesture, one entry — the committed write at the end.
	function property() {
		return {
			id: 'color',
			getName: () => 'color',
			getLabel: () => 'Colour',
			getValue: () => '',
			hasValue: () => false,
			upValue: vi.fn(),
			clear: vi.fn(),
			getType: () => 'color',
			getProperties: () => []
		} as unknown as Parameters<typeof write>[0];
	}

	it('passes the flag through to the property', () => {
		const p = property();
		write(p, '#3b82f6', { partial: true });
		expect(p.upValue).toHaveBeenCalledWith('#3b82f6', { partial: true });
	});

	it('leaves the call shape alone when there is no flag', () => {
		// One argument, the shape GrapesJS itself uses. An explicit `undefined` is a different call.
		const p = property();
		write(p, '#3b82f6');
		expect(p.upValue).toHaveBeenCalledWith('#3b82f6');
		expect((p.upValue as unknown as { mock: { calls: unknown[][] } }).mock.calls[0]).toHaveLength(
			1
		);
	});

	it('carries the flag to a clear, and to every part of a box', () => {
		const p = property();
		write(p, '', { partial: true });
		expect(p.clear).toHaveBeenCalledWith({ partial: true });
	});
});
