import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
	ADDED_STYLE_PROPERTIES,
	componentDisplayName,
	BORDER_ANCHOR,
	BORDER_SIDES,
	BORDER_STYLE_DEFAULT,
	BORDER_WIDTH_DEFAULT,
	canHoldText,
	COLOUR_SECTOR,
	COLOUR_SECTOR_LABEL,
	COMPONENT_NAMES,
	configureStyleRail,
	FOLDED_SECTOR,
	MAILABLE_LENGTH_UNITS,
	OPEN_SECTORS,
	TYPOGRAPHY_ORDER,
	SECTOR_ORDER,
	SECTOR_RENAMES,
	EMAIL_BLOCKS,
	fieldSpanHtml,
	describeUndoGroup,
	endTextEditing,
	installUndoShortcuts,
	loadEmailContent,
	MIN_FRAME_HEIGHT,
	MOVED_COLOUR_PROPERTIES,
	redoEmailEdit,
	STARTER_HTML,
	mirroredLabelInk,
	syncButtonLabelInk,
	syncedWidthAttribute,
	textPropertyVisible,
	undoEmailEdit,
	undoEmailEdits,
	undoHistory,
	matchesUndoChord,
	SUPERSEDED_KEYMAPS,
	UNDO_MANAGER_CONFIG,
	UNMAILABLE_STYLE_PROPERTIES,
	VOID_TAGS
} from './create';

// Real GrapesJS can't run here (it touches `window`; this suite runs in node), so these stubs model control flow and lifecycle — where every bug here has lived.
function stubEditor() {
	const calls: Array<{ fn: 'setStyle' | 'setComponents'; arg: string }> = [];
	return {
		calls,
		setStyle: (arg: string) => calls.push({ fn: 'setStyle', arg }),
		setComponents: (arg: string) => calls.push({ fn: 'setComponents', arg })
	};
}

describe('loadEmailContent', () => {
	it('installs styles BEFORE parsing components', () => {
		// `setStyle` is clear-then-add: calling it after the parse would wipe every rule the parse just derived from the markup.
		const ed = stubEditor();
		loadEmailContent(ed, { html: '<p id="a">hi</p>', css: '#a{color:red}' });
		expect(ed.calls.map((c) => c.fn)).toEqual(['setStyle', 'setComponents']);

		const starter = stubEditor();
		loadEmailContent(starter, { html: STARTER_HTML, css: '' });
		expect(starter.calls).toEqual([
			{ fn: 'setStyle', arg: '' },
			{ fn: 'setComponents', arg: STARTER_HTML }
		]);
	});

	it('blanks the canvas for empty and null content, so no sheet leaks between loads', () => {
		for (const data of [null, {}, { html: '' }]) {
			const ed = stubEditor();
			loadEmailContent(ed, data);
			expect(ed.calls).toEqual([
				{ fn: 'setStyle', arg: '' },
				{ fn: 'setComponents', arg: '' }
			]);
		}
	});
});

describe('the shipped skeleton and the rail’s property set', () => {
	it('styles STARTER_HTML inline with table layout, so a duplicated block keeps its styling', () => {
		// A GrapesJS clone gets no id/rule, so class-styled markup renders naked; `cta` is the one exception — the MSO button hook, harmless to clone.
		expect(STARTER_HTML.match(/\sclass="[^"]*"/g)).toEqual([' class="cta"']);
		expect(STARTER_HTML).not.toMatch(/<style/);
		expect(STARTER_HTML.match(/style="/g)?.length ?? 0).toBeGreaterThan(3);
		expect(STARTER_HTML).toMatch(/<table/);
		expect(STARTER_HTML).not.toMatch(/display:\s*(flex|grid)/);
		// The frame wrapper is overflow:hidden, so a 0 floor would clip it blank.
		expect(MIN_FRAME_HEIGHT).toBeGreaterThanOrEqual(400);
	});

	it('prunes only properties with no defensible email use, never real technique', () => {
		// Each pruned property is unusable in email (background/text-shadow unsupported, min-height ignored by Word, border-collapse not a real choice); height/margin/padding stay.
		const pruned = UNMAILABLE_STYLE_PROPERTIES.map(([, p]) => p);
		expect([...pruned].sort()).toEqual([
			'background',
			'border-collapse',
			'min-height',
			'text-shadow'
		]);
		for (const keep of ['height', 'margin', 'padding', 'background-color', 'color'])
			expect(pruned, `${keep} must stay in the rail`).not.toContain(keep);
	});

	it('declares backgrounds as background-color, never the shorthand the rail cannot edit', () => {
		// The rail writes `background-color` only, so a block declaring the `background:` shorthand leaves an orphaned declaration the author can't clear.
		const offenders = [
			...EMAIL_BLOCKS.filter(
				(b) => typeof b.content === 'string' && /background:\s*(?!transparent)/.test(b.content)
			).map((b) => b.id),
			...(/background:\s*(?!transparent)/.test(STARTER_HTML) ? ['(STARTER_HTML)'] : [])
		];
		expect(offenders).toEqual([]);

		const withBackground = EMAIL_BLOCKS.filter(
			(b) => typeof b.content === 'string' && /background-color:/.test(b.content)
		).map((b) => b.id);
		expect(withBackground).toContain('button');
		expect(withBackground).toContain('container-note-box');
	});
});

function authoredProperties(): string[] {
	const found = new Set<string>();
	const fromStyleAttrs = (html: string): void => {
		for (const m of html.matchAll(/\sstyle="([^"]*)"/g)) {
			for (const decl of m[1].split(';')) {
				const prop = decl.slice(0, decl.indexOf(':')).trim();
				if (prop) found.add(prop);
			}
		}
	};
	for (const block of EMAIL_BLOCKS) {
		if (typeof block.content === 'string') fromStyleAttrs(block.content);
		else for (const prop of Object.keys(block.content.style ?? {})) found.add(prop);
	}
	fromStyleAttrs(STARTER_HTML);
	return [...found];
}

describe('per-side border controls', () => {
	// The preset's one composite `border` control can't read or write a single side, and opens a box on all four when opened; drawn as three rows, not five (`style-panel/borders.ts`).

	it('adds all four sides, shaped like the `border` control an author already understands', () => {
		expect(ADDED_STYLE_PROPERTIES.map(([sector]) => sector)).toEqual(
			Array(4).fill(COLOUR_SECTOR) as string[]
		);
		expect(ADDED_STYLE_PROPERTIES.map(([, p]) => p.property)).toEqual(SIDES);
		for (const [, property] of ADDED_STYLE_PROPERTIES) {
			const side = property.property.replace('border-', '');
			expect(property.type).toBe('composite');
			expect(property.properties?.map((p) => p.label)).toEqual(['Width', 'Style', 'Colour']);
			expect(property.properties?.map((p) => p.property)).toEqual([
				`border-${side}-width`,
				`border-${side}-style`,
				`border-${side}-color`
			]);
			// Border width default can't be `0` — a colour-only edit would then draw nothing (`border: 0 solid …`) — and the constant is shared with the panel's own join, so both sides agree on "no value chosen".
			expect(property.properties?.[0]?.default).toBeTruthy();
			expect(property.properties?.[0]?.default).not.toMatch(/^0/);
			// `none` only switches off a whole box; an edge is cleared via its width going to 0 (`style-panel/borders.ts`), so `border-left:0 solid …` can never be produced.
			expect(property.properties?.[1]?.options?.map((o) => o.id)).toContain('none');
			for (const style of property.properties?.[1]?.options?.map((o) => o.id) ?? [])
				expect(['none', 'solid', 'dashed', 'dotted', 'double']).toContain(style);
			expect(property.properties?.[0]?.units).toEqual(['px', 'em']);
		}
	});

	it('states the shorthand’s defaults ONCE, where the panel and the composite both read them', () => {
		for (const [, property] of ADDED_STYLE_PROPERTIES) {
			expect(property.properties?.[0]?.default).toBe(BORDER_WIDTH_DEFAULT);
			expect(property.properties?.[1]?.default).toBe(BORDER_STYLE_DEFAULT);
			expect(property.properties?.[1]?.options?.map((o) => o.id)).toContain(BORDER_STYLE_DEFAULT);
		}
		expect(BORDER_WIDTH_DEFAULT).not.toMatch(/^0/);
	});

	it('covers every per-side border the blocks author, as the SHORTHAND the composite writes', () => {
		// A non-detached composite writes `border-left: 3px solid …` and blanks the longhands, so `border-left-color:` alone is unreachable — the background/background-color trap, reversed.
		const reachable = new Set([
			BORDER_ANCHOR,
			'border-radius',
			'border-collapse',
			...ADDED_STYLE_PROPERTIES.map(([, p]) => p.property)
		]);
		const authored = authoredProperties();
		expect(authored.filter((p) => p.startsWith('border') && !reachable.has(p))).toEqual([]);
		expect(
			authored.filter((p) => /^border-(top|right|bottom|left)-(width|style|color)$/.test(p))
		).toEqual([]);
		expect(authored).toContain('border-left');
		expect(authored).toContain('border-top');
	});
});

// The preset installs its sectors from an `onReady` callback that RESETS the whole collection — the seam `configureStyleRail` must register into instead of running inline.
interface StubProperty {
	id: string;
	attrs: Record<string, unknown>;
	subs: StubProperty[];
	getId: () => string;
	get: (k: string) => unknown;
	/** Typed reads of the same bag, so a call site says what it expects instead of asserting
	 *  inline. `get` stays `unknown` because GrapesJS's own property API is untyped. */
	getList: <T>(k: string) => T[];
	getFn: (k: string) => (arg: unknown) => unknown;
	set: (k: string, v: unknown) => void;
	getProperty: (id: string) => StubProperty | undefined;
	getProperties: () => StubProperty[];
}

function stubProperty(id: string, subIds: string[] = [], units?: string[]): StubProperty {
	const subs = subIds.map((s) => stubProperty(s, [], units));
	const attrs: Record<string, unknown> = {};
	if (units) attrs.units = [...units];
	const set = (k: string, v: unknown) => void (attrs[k] = v);
	const getProperty = (sub: string) => subs.find((p) => p.id === sub);
	return {
		id,
		attrs,
		subs,
		getId: () => id,
		get: (k) => attrs[k],
		getList: <T>(k: string) => (attrs[k] as T[] | undefined) ?? [],
		getFn: (k: string) => attrs[k] as (arg: unknown) => unknown,
		set,
		getProperty,
		getProperties: () => subs
	};
}

const PRESET_UNITS = ['px', '%', 'em', 'rem', 'vh', 'vw'];
const PRESET_SUBS: Record<string, string[]> = {
	[BORDER_ANCHOR]: ['border-width'],
	margin: ['margin-top', 'margin-right', 'margin-bottom', 'margin-left'],
	padding: ['padding-top', 'padding-right', 'padding-bottom', 'padding-left']
};
// `letter-spacing` is deliberately absent: `RETYPED_PROPERTIES` makes it a select of named steps, so
// it carries no units to intersect.
const PRESET_LENGTHS = [
	'font-size',
	'line-height',
	'width',
	'height',
	'max-width',
	'min-height',
	'margin',
	'padding',
	'border-radius',
	'border-width'
];

function stubStyleEditor({
	deaf = false,
	anchor = true,
	trailing = [],
	typographyExtra = [],
	missing = []
}: {
	deaf?: boolean;
	anchor?: boolean;
	trailing?: string[];
	typographyExtra?: string[];
	missing?: string[];
} = {}) {
	const sectorMap = (spec: Record<string, string[]>) =>
		new Map<string, StubProperty[]>(
			Object.entries(spec).map(([sector, ids]) => [
				sector,
				ids
					.filter((i) => !missing.includes(i))
					.map((i) =>
						stubProperty(
							i,
							PRESET_SUBS[i] ?? [],
							PRESET_LENGTHS.includes(i) ? [...PRESET_UNITS] : undefined
						)
					)
			])
		);
	let sectors = sectorMap({
		typography: ['font-family', 'font-size', 'color', 'text-shadow'],
		decorations: ['background-color', 'border-radius', 'border', 'box-shadow', 'background']
	});
	const loadHandlers: Array<() => void> = [];
	const styleHandlers: Array<(c: unknown, p?: unknown) => void> = [];
	const i18nSectors: Record<string, string> = {};
	const lifted = new Map<string, unknown[]>();
	let sectorNames = new Map<string, string | undefined>();
	let sectorOpen = new Map<string, boolean | undefined>();
	const collection = (sector: string) => {
		const props = sectors.get(sector);
		if (!props) return undefined;
		return {
			map: <T>(fn: (p: StubProperty) => T) => props.map(fn),
			get length() {
				return props.length;
			},
			add: (model: StubProperty, opts: { at?: number } = {}) => {
				props.splice(opts.at ?? props.length, 0, model);
				return model;
			},
			remove: (model: StubProperty) => {
				const at = props.indexOf(model);
				if (at >= 0) props.splice(at, 1);
				return model;
			}
		};
	};
	const removedSectors: string[] = [];
	return {
		loadHandlerCount: () => loadHandlers.length,
		styleHandlerCount: () => styleHandlers.length,
		sectorOrder: () => [...sectors.keys()],
		removedSectors: () => [...removedSectors],
		sectorName: (sector: string) => sectorNames.get(sector),
		sectorLabel: (sector: string) => i18nSectors[sector],
		sectorOpen: (sector: string) => sectorOpen.get(sector),
		sectorIds: (sector: string) => (sectors.get(sector) ?? []).map((p) => p.id),
		property: (sector: string, id: string) => sectors.get(sector)?.find((p) => p.id === id),
		StyleManager: {
			getProperties: collection,
			getProperty: (sector: string, id: string) => sectors.get(sector)?.find((p) => p.id === id),
			removeProperty: (sector: string, id: string) => {
				const props = sectors.get(sector);
				const at = props?.findIndex((p) => p.id === id) ?? -1;
				if (!props || at < 0) return undefined;
				return props.splice(at, 1)[0];
			},
			addSector: (
				id: string,
				def: { name?: string; open?: boolean },
				opts: { at?: number } = {}
			) => {
				if (sectors.has(id)) return id;
				const entries = [...sectors.entries()];
				entries.splice(opts.at ?? entries.length, 0, [id, []]);
				sectors = new Map(entries);
				sectorNames.set(id, def.name);
				sectorOpen.set(id, def.open);
				return id;
			},
			removeSector: (id: string) => {
				if (!sectors.has(id)) return undefined;
				sectors.delete(id);
				removedSectors.push(id);
				return id;
			},
			getSectors: () => ({
				map: <T>(fn: (s: { id: string; getProperties: () => StubProperty[] }) => T) =>
					[...sectors.keys()].map((id) => fn({ id, getProperties: () => sectors.get(id) ?? [] })),
				remove: (sector: { id: string }) => {
					lifted.set(sector.id, sectors.get(sector.id) ?? []);
					sectors.delete(sector.id);
				},
				add: (sector: { id: string }, opts: { at?: number } = {}) => {
					const props = lifted.get(sector.id) ?? sectors.get(sector.id) ?? [];
					lifted.delete(sector.id);
					const entries = [...sectors.entries()].filter(([id]) => id !== sector.id);
					entries.splice(opts.at ?? entries.length, 0, [sector.id, props] as [
						string,
						StubProperty[]
					]);
					sectors = new Map(entries);
				}
			}),
			getSector: (id: string) =>
				sectors.has(id)
					? {
							id,
							set: (key: string, value: string | boolean) => {
								if (key === 'name') sectorNames.set(id, String(value));
								if (key === 'open') sectorOpen.set(id, !!value);
							}
						}
					: undefined,
			addProperty: (
				sector: string,
				definition: Record<string, unknown>,
				opts: { at?: number } = {}
			) => {
				const props = sectors.get(sector);
				if (!props) return undefined;
				const created = stubProperty((definition.property ?? definition.extend) as string);
				// EVERY key, not just `name`: a definition also carries `type`, `options` and `default`, and a
				// stub that drops them cannot tell a retyped control from the one it replaced.
				for (const [key, value] of Object.entries(definition))
					if (key !== 'property' && key !== 'extend') created.set(key, value);
				props.splice(opts.at ?? props.length, 0, created);
				return created;
			}
		},
		// `Sector.getName()` reads the i18n override before the model's own `name`, so a rename must set both or it's silently inert.
		I18n: {
			addMessages: (messages: { en?: { styleManager?: { sectors?: Record<string, string> } } }) => {
				Object.assign(i18nSectors, messages.en?.styleManager?.sectors ?? {});
			}
		},
		on: (event: string, cb: (c?: unknown, p?: unknown) => void) => {
			if (event === 'load' && !deaf) loadHandlers.push(cb);
			if (event === 'component:styleUpdate') styleHandlers.push(cb);
		},
		emitStyleUpdate: (component: unknown, props?: unknown) => {
			for (const cb of [...styleHandlers]) cb(component, props);
		},
		fireLoad: () => {
			sectors = sectorMap({
				dimension: ['width', 'height', 'max-width', 'min-height', 'margin', 'padding'],
				typography: [
					'font-family',
					'font-size',
					'font-weight',
					'letter-spacing',
					'color',
					'line-height',
					'text-align',
					'text-decoration',
					'font-style',
					'vertical-align',
					'text-shadow',
					...typographyExtra
				],
				decorations: [
					'background-color',
					'border-collapse',
					'border-radius',
					...(anchor ? [BORDER_ANCHOR] : []),
					'background',
					...trailing
				]
			});
			sectorNames = new Map();
			sectorOpen = new Map();
			for (const cb of [...loadHandlers]) cb();
		},
		emitLoad: () => {
			for (const cb of [...loadHandlers]) cb();
		}
	};
}

const SIDES = ['border-top', 'border-right', 'border-bottom', 'border-left'];
const FOLDED = ['border-radius'];
// `color` is deliberately not in COLOUR_RAIL: text colour lives in `typography` beside font size (see `MOVED_COLOUR_PROPERTIES`).
const COLOUR_RAIL = ['background-color', BORDER_ANCHOR, ...SIDES, ...FOLDED];

describe('configureStyleRail', () => {
	it('registers in the load-safe seam, so the preset’s sector reset cannot discard the work', () => {
		// In GrapesJS 0.21.13 a property belongs to exactly one sector, so moving one is a real subtraction — not a copy that could silently no-op.
		const ed = stubStyleEditor({ trailing: ['box-shadow'] });
		configureStyleRail(ed);
		expect(ed.loadHandlerCount()).toBe(1);

		ed.fireLoad();
		expect(ed.sectorIds(COLOUR_SECTOR)).toEqual(COLOUR_RAIL);
		expect(ed.sectorIds('decorations')).toEqual(['box-shadow']);
		expect(ed.sectorIds('typography')).toEqual([...TYPOGRAPHY_ORDER]);
		for (const side of BORDER_SIDES) expect(COLOUR_RAIL).toContain(`border-${side}`);
		expect(MOVED_COLOUR_PROPERTIES.at(-1)?.[1]).toBe(BORDER_ANCHOR);
	});

	it('does NOTHING if the work is only done inline — the trap, stated as a failure', () => {
		const ed = stubStyleEditor({ deaf: true });
		configureStyleRail(ed);
		ed.fireLoad();

		expect(ed.sectorOrder()).not.toContain(COLOUR_SECTOR);
		expect(ed.sectorIds('decorations')).not.toContain('border-left');
		expect(ed.sectorIds('typography')).toContain('text-shadow');
		expect(ed.sectorIds('typography')).toContain('color');
	});

	it('adds each control once, however many times it runs', () => {
		// A prune is naturally idempotent; an unguarded MOVE is not — a second run would leave the property registered in both sectors.
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		ed.emitLoad();
		expect(ed.sectorIds(COLOUR_SECTOR)).toEqual(COLOUR_RAIL);
	});

	it('degrades to appending when the anchor is gone, rather than throwing', () => {
		const ed = stubStyleEditor({ anchor: false });
		configureStyleRail(ed);
		ed.fireLoad();

		const ids = ed.sectorIds(COLOUR_SECTOR);
		expect(ids).not.toContain(BORDER_ANCHOR);
		expect(ids.slice(-FOLDED.length)).toEqual(FOLDED);
		expect(ids.slice(-FOLDED.length - SIDES.length, -FOLDED.length)).toEqual(SIDES);
		expect(ids[0]).toBe('background-color');
	});

	it('removes the folded sector once it is empty, rather than leaving a heading', () => {
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		expect(ed.sectorIds('decorations')).toEqual([]);
		expect(ed.removedSectors?.()).toContain('decorations');
	});

	it('keeps the sector when the preset still has something in it', () => {
		const ed = stubStyleEditor({ trailing: ['box-shadow'] });
		configureStyleRail(ed);
		ed.fireLoad();
		expect(ed.removedSectors?.()).not.toContain('decorations');
	});
});

describe('the background & borders sector', () => {
	it('is LAST and shut, and honestly labelled for what is left in it', () => {
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();

		expect(ed.sectorOrder().at(-1)).toBe(COLOUR_SECTOR);
		expect(ed.sectorOpen(COLOUR_SECTOR)).toBe(false);
		expect(ed.sectorName(COLOUR_SECTOR)).toBe(COLOUR_SECTOR_LABEL);
		expect(COLOUR_SECTOR_LABEL).toMatch(/border/i);
		expect(COLOUR_SECTOR_LABEL).not.toMatch(/color|decoration/i);
		expect(ed.property(COLOUR_SECTOR, 'color')).toBeUndefined();
		expect(ed.property('typography', 'color')?.get('name')).toBe('Text colour');
		expect(ed.property(COLOUR_SECTOR, 'background-color')?.get('name')).toBe('Background colour');
		expect(ed.property(COLOUR_SECTOR, BORDER_ANCHOR)?.get('name')).toBeUndefined();

		const width = ed.property(COLOUR_SECTOR, BORDER_ANCHOR)?.getProperty('border-width');
		expect(width?.get('default')).toBeTruthy();
		expect(width?.get('default')).not.toMatch(/^0/);
	});

	it('rebuilds the background control when the preset no longer has it to move', () => {
		const ed = stubStyleEditor({ missing: ['background-color'] });
		configureStyleRail(ed);
		ed.fireLoad();
		expect(ed.sectorIds(COLOUR_SECTOR)[0]).toBe('background-color');
		expect(ed.property(COLOUR_SECTOR, 'background-color')?.get('name')).toBe('Background colour');
	});

	it('puts Text first, because that is what an author clicked the block to change', () => {
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		expect(ed.sectorOrder()[0]).toBe('typography');
		expect(ed.sectorOrder()).toEqual(SECTOR_ORDER.filter((id) => ed.sectorOrder().includes(id)));
	});

	it('offers only the length units an email client honours', () => {
		// Outlook's Word engine supports none of `rem`/`vh`/`vw`, and inside a fixed-width card they're meaningless anyway — the same lie as `min-height`, stated in units.
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		// `letter-spacing` is not here: it is a select of named steps, so it has no units to intersect.
		// `line-height` is not here either, and for a sharper reason — see the assertion below it.
		expect(ed.property('typography', 'font-size')?.get('units')).toEqual(MAILABLE_LENGTH_UNITS);
		for (const id of ['width', 'height', 'max-width'])
			expect(ed.property('dimension', id)?.get('units'), id).toEqual(MAILABLE_LENGTH_UNITS);
		expect(ed.property('dimension', 'padding')?.getProperties()[0]?.get('units')).toEqual(
			MAILABLE_LENGTH_UNITS
		);
		expect(MAILABLE_LENGTH_UNITS).toContain('em');
		expect(MAILABLE_LENGTH_UNITS).not.toContain('rem');
	});

	it('offers NO unit for line height, because a ratio is not a length', () => {
		// This assertion used to read the other way — line height was expected to carry the mailable
		// LENGTH units, which is how the defect shipped. GrapesJS derives the property from letter
		// spacing (`['line-height', {}, 'letter-spacing']` in its preset) so it inherited a length's
		// units, and `UnitInput` substitutes the first offered unit for a value carrying none: a 1.8
		// the author typed committed as `1.8px`, collapsing the line box in every inbox. `1.8em` is no
		// better — it resolves against the element's own size rather than being inherited as a ratio.
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		expect(ed.property('typography', 'line-height')?.get('units')).toEqual([]);
		expect(ed.property('typography', 'line-height')?.get('type')).toBe('number');
	});

	it('offers letter spacing as a NUMBER and weight as numbered steps', () => {
		// Both read as a measurement and were selects (Tight/Slight/Wide, Normal/Bold). A named step
		// cannot express the value the design wants — the wordmark's 0.09em, a 600 button — and a select
		// whose stored value is not among its options renders EMPTY over a real declaration.
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();

		const spacing = ed.property('typography', 'letter-spacing');
		expect(spacing?.get('type')).toBe('number');
		// `em` first: spacing that tracks the type size survives a font-size change.
		expect(spacing?.getList<string>('units')[0]).toBe('em');
		expect(spacing?.getList<string>('units')).toContain('px');

		// Weight stays a DROPDOWN — the useful weights are a scale a typeface actually ships — but its
		// options are the numbers, and every value this editor's blocks declare is among them.
		const weight = ed.property('typography', 'font-weight');
		expect(weight?.get('type')).toBe('select');
		const weights = (weight?.getList<{ id: string }>('options') ?? []).map((o) => o.id);
		for (const shipped of ['400', '600', '700']) expect(weights).toContain(shipped);
	});

	it('files vertical alignment as layout, not as typography', () => {
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		expect(ed.sectorIds('typography')).not.toContain('vertical-align');
		expect(ed.sectorIds('dimension')).toContain('vertical-align');
	});

	it('leads Text with the measured controls and groups the switches after them', () => {
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		const ids = ed.sectorIds('typography');
		expect(ids.slice(0, TYPOGRAPHY_ORDER.length)).toEqual([...TYPOGRAPHY_ORDER]);
		expect(ids.indexOf('line-height')).toBe(ids.indexOf('font-size') + 1);
		expect(ids.indexOf('color')).toBeLessThan(ids.indexOf('letter-spacing'));
		const switches = ['font-weight', 'font-style', 'text-decoration', 'text-align'];
		for (const id of ['color', 'font-size', 'line-height', 'letter-spacing'])
			for (const button of switches)
				expect(ids.indexOf(id), `${id} vs ${button}`).toBeLessThan(ids.indexOf(button));
		const extra = stubStyleEditor({ typographyExtra: ['font-variant'] });
		configureStyleRail(extra);
		extra.fireLoad();
		expect(extra.sectorIds('typography').at(-1)).toBe('font-variant');
	});

	it('offers text styling only where there can be text', () => {
		// An `<img>` has no text, so its font/colour/alignment controls would write nowhere; hidden via GrapesJS's own `isVisible` predicate, re-checked per selection.
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		const predicates = ed
			.sectorIds('typography')
			.map((id) => ed.property('typography', id)?.get('isVisible'));
		expect(predicates.length).toBeGreaterThan(0);
		for (const [index, predicate] of predicates.entries())
			expect(typeof predicate, ed.sectorIds('typography')[index]).toBe('function');
		const extra = stubStyleEditor({ typographyExtra: ['font-variant'] });
		configureStyleRail(extra);
		extra.fireLoad();
		expect(typeof extra.property('typography', 'font-variant')?.get('isVisible')).toBe('function');
		for (const [sector, id] of [
			[COLOUR_SECTOR, 'background-color'],
			[COLOUR_SECTOR, 'border-left'],
			['dimension', 'padding']
		] as const)
			expect(ed.property(sector, id)?.get('isVisible'), id).toBeUndefined();
	});

	it('offers vertical alignment only where it decides anything', () => {
		// It lives in the LAYOUT sector, so the text rule above does not reach it — and on a heading it
		// is inert: an author changes `baseline` and nothing moves, on the canvas or in an inbox.
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		const predicate = ed.property('dimension', 'vertical-align')?.getFn('isVisible');
		expect(typeof predicate).toBe('function');
		const on = (tag: string) => predicate?.({ component: { get: () => tag } });
		expect(on('td')).toBe(true);
		expect(on('img')).toBe(true);
		expect(on('h1')).toBe(false);
		expect(on('p')).toBe(false);
		// An unreadable tag KEEPS it, the rule `canHoldText` follows: never hide a control on a guess.
		expect(predicate?.({})).toBe(true);
	});

	it('opens Text and nothing else, so a selection is answered by a control', () => {
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		for (const id of SECTOR_ORDER) expect(ed.sectorOpen(id)).toBe(OPEN_SECTORS.includes(id));
		expect(OPEN_SECTORS).toEqual(['typography']);
	});
});

describe('canHoldText — the rule behind hiding text styling', () => {
	const tagged = (tagName: unknown) => ({
		get: (key: string) => (key === 'tagName' ? tagName : undefined)
	});

	it('says no to a void element, because it has no content to style', () => {
		for (const tag of ['img', 'hr', 'br', 'input'])
			expect(canHoldText(tagged(tag)), tag).toBe(false);
		expect(VOID_TAGS).toContain('img');
		expect(VOID_TAGS).toContain('hr');
	});

	it('says yes to a cell, which styles the text inside it even when that text is an image', () => {
		// A `<td>` passes even when it only holds an image: `text-align`/`font-size` on it are how the image is centred and its gap is controlled.
		for (const tag of ['td', 'table', 'tr', 'p', 'a', 'div', 'span', 'h1'])
			expect(canHoldText(tagged(tag)), tag).toBe(true);
	});

	it('is case-insensitive, because markup is not', () => {
		expect(canHoldText(tagged('IMG'))).toBe(false);
	});

	it('says yes to anything whose tag it cannot read', () => {
		expect(canHoldText(undefined)).toBe(true);
		expect(canHoldText(null)).toBe(true);
		expect(canHoldText({})).toBe(true);
		expect(canHoldText(tagged(''))).toBe(true);
		expect(canHoldText(tagged(42))).toBe(true);
	});

	it('prefers the component over the rule the StyleManager is aimed at', () => {
		expect(textPropertyVisible({ component: tagged('img'), target: tagged('td') })).toBe(false);
		expect(textPropertyVisible({ component: null, target: tagged('img') })).toBe(false);
		expect(textPropertyVisible({ component: tagged('td'), target: tagged('img') })).toBe(true);
		expect(textPropertyVisible({})).toBe(true);
		expect(textPropertyVisible()).toBe(true);
	});
});

function namesIn(block: (typeof EMAIL_BLOCKS)[number]): string[] {
	if (typeof block.content !== 'string') {
		const name = block.content.name;
		return typeof name === 'string' ? [name] : [];
	}
	return [...block.content.matchAll(/data-gjs-name="([^"]+)"/g)].map((m) => m[1]);
}

describe('block names', () => {
	it('names the OUTERMOST element of every block, and every clickable nested one', () => {
		// An unnamed element falls back to GrapesJS's own type/tagName label ('Table cell' for most clicks) — the leak this suite exists to catch.
		expect(EMAIL_BLOCKS.length).toBeGreaterThanOrEqual(14);
		expect([...new Set(EMAIL_BLOCKS.map((b) => b.category))].sort()).toEqual([
			'Basic',
			'Media',
			'Message'
		]);

		const unnamed = EMAIL_BLOCKS.filter((b) => {
			if (typeof b.content !== 'string') return typeof b.content.name !== 'string';
			return !/^<[a-z][a-z0-9]*\s[^>]*\bdata-gjs-name="[^"]+"/.test(b.content);
		}).map((b) => b.id);
		expect(unnamed).toEqual([]);

		for (const b of EMAIL_BLOCKS) {
			if (typeof b.content !== 'string') continue;
			const cells = b.content.match(/<td\b/g)?.length ?? 0;
			const named = b.content.match(/<td\s[^>]*\bdata-gjs-name="/g)?.length ?? 0;
			expect(named, `${b.id} leaves a <td> unnamed`).toBe(cells);
		}
	});

	it('uses Capitalised human words, never a GrapesJS name key', () => {
		// `custom-name → i18n[name] → name` resolves i18n FIRST, so naming a block 'cell'/'link'/'text' would resolve straight back to GrapesJS's own label.
		const reserved = new Set(Object.keys(COMPONENT_NAMES).filter(Boolean));
		for (const block of EMAIL_BLOCKS) {
			for (const name of namesIn(block)) {
				expect(reserved.has(name), `${block.id}: "${name}" is a GrapesJS i18n key`).toBe(false);
				expect(name, `${block.id}: "${name}" should start with a capital`).toMatch(/^[A-Z]/);
			}
		}
	});

	it('carries no data-gjs attribute other than the two the parser is known to consume', () => {
		for (const block of EMAIL_BLOCKS) {
			if (typeof block.content !== 'string') continue;
			for (const attr of block.content.match(/data-gjs[a-z-]*/g) ?? [])
				expect(['data-gjs-name', 'data-gjs-type'], `${block.id} uses ${attr}`).toContain(attr);
		}
	});
});

describe('COMPONENT_NAMES (the fallback vocabulary a REOPENED template lands on)', () => {
	// `data-gjs-name` is session-only, never exported: reopening a saved template falls to `i18n[type]`, so every generic type needs a human name here too.

	it('covers every generic type an email is built from, in the author’s language', () => {
		for (const key of ['table', 'tbody', 'row', 'cell', 'wrapper', 'text', 'link', 'image'])
			expect(COMPONENT_NAMES[key], `no name for '${key}'`).toBeTruthy();
		for (const [key, name] of Object.entries(COMPONENT_NAMES)) {
			expect(name, `'${key}' still reads as HTML table vocabulary`).not.toMatch(/table/i);
			expect(name, `'${key}' should be a Capitalised human word`).toMatch(/^[A-Z]/);
		}
	});

	it('only renames keys GrapesJS actually resolves, so none is a silent no-op', () => {
		const known = new Set(
			' wrapper text comment image video label link map tfoot tbody thead table row cell'.split(' ')
		);
		for (const key of Object.keys(COMPONENT_NAMES))
			expect(known.has(key), `'${key}' is not a GrapesJS name key`).toBe(true);
	});
});

describe('the palette’s deliberate omissions', () => {
	it('offers no Spacer or Highlight Box, and no multi-column layout at all', () => {
		// No Spacer/Highlight Box (padding/Note Box already cover them) and no multi-column layout at all — it breaks unpredictably across clients in ways this rail can't fix.
		const ids = EMAIL_BLOCKS.map((b) => b.id);
		expect(ids).not.toContain('spacer');
		expect(ids).not.toContain('container-highlight');
		expect(ids).not.toContain('sec-split');
		expect(EMAIL_BLOCKS.some((b) => /split/i.test(b.label))).toBe(false);
		expect(ids).toContain('container-note-box');
		expect(ids).not.toContain('sec-full');
		expect(ids).not.toContain('sec-columns');
		expect(ids).not.toContain('sec-three-columns');
		expect(EMAIL_BLOCKS.some((b) => /column/i.test(b.label))).toBe(false);

		const card = EMAIL_BLOCKS.find((b) => b.id === 'sec-container');
		expect(card?.label).toBe('Email card');
		expect(EMAIL_BLOCKS.some((b) => b.label === 'Container')).toBe(false);
		expect(String(card?.content)).toMatch(/max-width:600px/);
		expect(String(card?.content)).toMatch(/align="center"/);
		// The card paints no page background: a hardcoded fill would be a grey band the author can't reach — page colour belongs to the Page panel.
		expect(/^<table[^>]*>/.exec(String(card?.content))?.[0]).not.toMatch(/background/);
	});
});

// Asserts the resulting attribute VALUE, not that `setAttributes` was called — an earlier version passed this way and broke in the browser.
function stubCell(attributes: Record<string, unknown>, style: Record<string, unknown>) {
	const state = { attributes: { ...attributes }, style: { ...style }, writes: 0 };
	return {
		state,
		get: (k: string) => (k === 'attributes' ? state.attributes : undefined),
		getStyle: () => ({ ...state.style }),
		setAttributes: (attrs: Record<string, unknown>) => {
			state.writes += 1;
			state.attributes = attrs;
		}
	};
}

function railWithSync() {
	const ed = stubStyleEditor();
	configureStyleRail(ed);
	ed.fireLoad();
	return ed;
}

describe('the width divergence', () => {
	// A cell can state its width twice (`<td width="50%" style="width:50%">`): Outlook reads the attribute, the rail edits only the CSS — unsynced, the canvas lies about what Outlook will show.

	it('follows the CSS width into the attribute, through the event the editor really binds', () => {
		const ed = railWithSync();
		const cell = stubCell({ width: '50%', 'data-x': 'keep' }, { width: '30%', padding: '0 8px' });
		ed.emitStyleUpdate(cell, { style: cell.getStyle() });
		expect(cell.state.attributes.width).toBe('30%');
		expect(cell.state.attributes['data-x']).toBe('keep');

		// Verbatim, fraction included: rounding a thirds-based width would put Outlook ~1% off across the row.
		const third = stubCell({ width: '33.33%' }, { width: '41.67%' });
		ed.emitStyleUpdate(third, { style: third.getStyle() });
		expect(third.state.attributes.width).toBe('41.67%');
	});

	it('subscribes exactly once, so one edit cannot write the attribute twice', () => {
		// `tune` runs twice by design; TWO handlers are expected now (width sync + `syncButtonLabelInk`), each subscribing exactly once.
		const ed = stubStyleEditor();
		configureStyleRail(ed);
		ed.fireLoad();
		ed.emitLoad();
		expect(ed.styleHandlerCount()).toBe(2);
	});

	it('removes the attribute for a width it cannot state, rather than writing a wrong one', () => {
		// The attribute has no unit-bearing form (`em`/`auto`/`calc()` can't be expressed as one), so with no safe translation the attribute is removed.
		for (const css of ['200px', '12em', 'auto', 'calc(100% - 16px)', 'inherit']) {
			const ed = railWithSync();
			const cell = stubCell({ width: '50%' }, { width: css });
			ed.emitStyleUpdate(cell, { style: cell.getStyle() });
			expect(cell.state.attributes, `width:${css}`).not.toHaveProperty('width');
		}
	});

	it('writes nothing when the two widths are not a restatement of each other', () => {
		const cases: Array<[string, Record<string, unknown>, Record<string, unknown>]> = [
			['no css width', { width: '100%' }, { 'background-color': 'red', padding: '24px 0' }],
			['hybrid card', { width: '600' }, { width: '100%', 'max-width': '600px' }],
			['already in step', { width: '33.33%' }, { width: '33.33%' }],
			// `__p` marks a partial, mid-drag update — `avoidStore`, off the undo stack — so writing beside one would be an entry reverting nothing visible.
			['mid-drag partial', { width: '50%' }, { width: '30%', __p: true }]
		];
		for (const [why, attrs, style] of cases) {
			const ed = railWithSync();
			const cell = stubCell(attrs, style);
			ed.emitStyleUpdate(cell, { style: cell.getStyle() });
			expect(cell.state.writes, why).toBe(0);
			expect(cell.state.attributes.width, why).toBe(attrs.width);
		}

		const committed = stubCell({ width: '50%' }, { width: '30%' });
		railWithSync().emitStyleUpdate(committed, { style: committed.getStyle() });
		expect(committed.state.attributes.width).toBe('30%');
	});

	it('reads the component rather than trusting the payload to be complete', () => {
		const ed = railWithSync();
		const cell = stubCell({ width: '50%' }, { width: '25%' });
		ed.emitStyleUpdate(cell, { style: { padding: '0' } });
		expect(cell.state.attributes.width).toBe('25%');
	});

	it('survives the shapes it can be handed without an editor’s guarantees', () => {
		const ed = railWithSync();
		expect(() => ed.emitStyleUpdate(null)).not.toThrow();
		expect(() => ed.emitStyleUpdate({})).not.toThrow();
		expect(() => ed.emitStyleUpdate({ get: () => undefined })).not.toThrow();
	});

	it('decides percentages only, verbatim — no rounding', () => {
		expect(syncedWidthAttribute('50%', '33.33%')).toBe('33.33%');
		expect(syncedWidthAttribute('50%', ' 30% ')).toBe('30%');
		expect(syncedWidthAttribute('50%', '50%')).toBeUndefined();
		expect(syncedWidthAttribute('50%', '200px')).toBeNull();
		expect(syncedWidthAttribute('50%', '')).toBeUndefined();
		expect(syncedWidthAttribute('50%', undefined)).toBeUndefined();
		expect(syncedWidthAttribute('600', '50%')).toBeUndefined();
		expect(syncedWidthAttribute(undefined, '50%')).toBeUndefined();
		expect(syncedWidthAttribute('-50%', '30%')).toBeUndefined();
		expect(syncedWidthAttribute('50%', 'calc(50% - 8px)')).toBeNull();
	});

	it('is the rule every block that ships a percentage width is authored to', () => {
		const mismatched: string[] = [];
		const checked: string[] = [];
		for (const block of EMAIL_BLOCKS) {
			if (typeof block.content !== 'string') continue;
			for (const [, attr, style] of block.content.matchAll(
				/width="(\d+(?:\.\d+)?%)"[^>]*style="([^"]*)"/g
			)) {
				checked.push(attr);
				const css = /(?:^|;)\s*width:\s*([^;]+)/.exec(style)?.[1]?.trim();
				if (css !== attr) mismatched.push(`${block.id}: ${attr} vs ${css}`);
			}
		}
		expect(mismatched).toEqual([]);
		expect(checked.filter((w) => w === '100%').length).toBeGreaterThanOrEqual(4);
		expect(checked.every((w) => w.endsWith('%'))).toBe(true);
	});
});

// Order matters: end the rich-text session BEFORE undoing — `undo` records the editing flag as it sees it, so undoing first records `true` and fails.
function undoStubEditor(opts: { editing?: boolean; disableEditing?: boolean } = {}) {
	const calls: string[] = [];
	const undoSawEditing: boolean[] = [];
	const state: { editing: unknown } = { editing: null };
	const view: Record<string, unknown> = { model: {} };
	if (opts.disableEditing !== false) {
		view.disableEditing = async () => {
			// Async on purpose: GrapesJS's own teardown awaits the RTE and syncs content before the flag clears — a caller that forgets to await lands here.
			await Promise.resolve();
			calls.push('disableEditing');
			state.editing = null;
		};
	}
	if (opts.editing !== false) state.editing = view;
	return {
		calls,
		undoSawEditing,
		getModel: () => ({
			get: (k: string) => (k === 'editing' ? state.editing : undefined),
			isEditing: () => !!state.editing,
			setEditing: (v: unknown) => {
				calls.push(`setEditing:${!!v}`);
				state.editing = v || null;
			}
		}),
		getEditing: () => (state.editing ? { view } : undefined),
		UndoManager: {
			undo: () => {
				calls.push('undo');
				undoSawEditing.push(!!state.editing);
			},
			redo: () => {
				calls.push('redo');
				undoSawEditing.push(!!state.editing);
			}
		}
	};
}

describe('undo', () => {
	it('ends the text-editing session BEFORE undoing (and redoing), never while it is live', async () => {
		// `UndoManagerModule.undo` is `!em.isEditing() && um.undo()` — a silent no-op while a block-drop's text-editing flag is set, until this ends it first.
		const undone = undoStubEditor();
		await undoEmailEdit(undone);
		expect(undone.calls).toEqual(['disableEditing', 'undo']);
		expect(undone.undoSawEditing).toEqual([false]);

		const redone = undoStubEditor();
		await redoEmailEdit(redone);
		expect(redone.calls).toEqual(['disableEditing', 'redo']);
		expect(redone.undoSawEditing).toEqual([false]);
	});

	it('undoes straight away with no session open, and reports whether there was one', async () => {
		const ed = undoStubEditor({ editing: false });
		await undoEmailEdit(ed);
		expect(ed.calls).toEqual(['undo']);

		expect(await endTextEditing(undoStubEditor())).toBe(true);
		expect(await endTextEditing(undoStubEditor({ editing: false }))).toBe(false);
		expect(await endTextEditing(null)).toBe(false);
	});

	it('falls back to clearing the flag when the view cannot be torn down properly', async () => {
		const ed = undoStubEditor({ disableEditing: false });
		await undoEmailEdit(ed);
		expect(ed.calls).toEqual(['setEditing:false', 'undo']);
		expect(ed.undoSawEditing).toEqual([false]);
	});

	it('ends the session ONCE for a multi-step rewind (one step by default, none for ≤0)', async () => {
		const many = undoStubEditor();
		await undoEmailEdits(many, 3);
		expect(many.calls).toEqual(['disableEditing', 'undo', 'undo', 'undo']);
		expect(many.undoSawEditing).toEqual([false, false, false]);

		const one = undoStubEditor();
		await undoEmailEdits(one);
		expect(one.calls).toEqual(['disableEditing', 'undo']);

		const none = undoStubEditor({ editing: false });
		await undoEmailEdits(none, 0);
		expect(none.calls).toEqual([]);
	});
});

describe('installUndoShortcuts', () => {
	// `DocLike` is what `installUndoShortcuts` actually takes — a Pick of two methods — and this
	// stub satisfies that rather than `Document`'s full overload set, which no hand-written object
	// can match. Declared so the four call sites below need no cast each.
	type DocLike = Pick<Document, 'addEventListener' | 'removeEventListener'>;

	function stubDoc() {
		type Listener = (ev: KeyboardEvent) => void;
		const listeners: { type: string; fn: Listener; capture: unknown }[] = [];
		const doc = {
			listeners,
			addEventListener: (type: string, fn: Listener, capture: unknown) =>
				listeners.push({ type, fn, capture }),
			removeEventListener: (type: string, fn: Listener) => {
				const i = listeners.findIndex((l) => l.type === type && l.fn === fn);
				if (i >= 0) listeners.splice(i, 1);
			},
			press: (ev: Record<string, unknown>) => {
				const event = { key: 'z', ctrlKey: true, preventDefault: () => {}, ...ev };
				for (const l of [...listeners])
					if (l.type === 'keydown') l.fn(event as unknown as KeyboardEvent);
				return event;
			}
		};
		// The two methods `installUndoShortcuts` reads, typed as it declares them.
		return doc as typeof doc & DocLike;
	}

	function shortcutEditor() {
		const removed: string[] = [];
		const loadHandlers: (() => void)[] = [];
		const container = { isConnected: true };
		return Object.assign(undoStubEditor(), {
			removed,
			container,
			loadHandlers,
			Keymaps: { remove: (id: string) => removed.push(id) },
			on: (ev: string, fn: () => void) => {
				if (ev === 'load') loadHandlers.push(fn);
			},
			off: (ev: string, fn: () => void) => {
				const i = ev === 'load' ? loadHandlers.indexOf(fn) : -1;
				if (i >= 0) loadHandlers.splice(i, 1);
			},
			getContainer: () => container
		});
	}

	const settle = () => new Promise((r) => setTimeout(r, 0));

	it('undoes and redoes EVEN THOUGH GrapesJS keymap dispatch is unavailable', async () => {
		// `Keymaps.removeAll()` empties keymaster's module-level `handlers`, shared by every editor on the page — destroying ANY editor would silently unbind every other's keystrokes if we relied on it.
		const doc = stubDoc();
		const ed = shortcutEditor();
		installUndoShortcuts(ed, doc);

		doc.press({});
		await settle();
		expect(ed.calls).toEqual(['disableEditing', 'undo']);
		expect(ed.undoSawEditing).toEqual([false]);

		doc.press({ shiftKey: true });
		await settle();
		expect(ed.calls).toEqual(['disableEditing', 'undo', 'redo']);
	});

	it('takes GrapesJS undo/redo keymaps out of service, at init and again on load', () => {
		const ed = shortcutEditor();
		installUndoShortcuts(ed, stubDoc());
		expect(ed.removed).toEqual(['core:undo', 'core:redo']);

		ed.loadHandlers.forEach((fn) => fn());
		expect(ed.removed).toEqual(['core:undo', 'core:redo', 'core:undo', 'core:redo']);
	});

	it('binds one capture-phase keydown listener, removes it on dispose, and suppresses the browser’s own undo', async () => {
		const doc = stubDoc();
		const ed = shortcutEditor();
		const dispose = installUndoShortcuts(ed, doc);

		expect(doc.listeners).toHaveLength(1);
		expect(doc.listeners[0].type).toBe('keydown');
		expect(doc.listeners[0].capture).toBe(true);
		let prevented = 0;
		doc.press({ preventDefault: () => (prevented += 1) });
		expect(prevented).toBe(1);
		await settle();

		dispose();
		expect(doc.listeners).toHaveLength(0);
		expect(ed.loadHandlers).toHaveLength(0);
		ed.calls.length = 0;
		doc.press({});
		await settle();
		expect(ed.calls).toEqual([]);
	});

	it('ignores a stale editor whose canvas has been detached', async () => {
		// During an HMR remount two editors can be briefly alive with a listener each; without this guard one keypress would undo twice.
		const doc = stubDoc();
		const ed = shortcutEditor();
		installUndoShortcuts(ed, doc);
		ed.container.isConnected = false;
		doc.press({});
		await settle();
		expect(ed.calls).toEqual([]);
	});
});

describe('matchesUndoChord', () => {
	it('accepts ctrl and cmd, reads keyCode when the cloned event has no key, declines the rest', () => {
		// The canvas event is a CLONE with keyCode/which reattached by defineProperty; Shift-Z can arrive upper-case, and alt+ctrl+z is undo nowhere.
		expect(matchesUndoChord({ key: 'z', ctrlKey: true })).toBe('undo');
		expect(matchesUndoChord({ key: 'z', metaKey: true })).toBe('undo');
		expect(matchesUndoChord({ keyCode: 90, ctrlKey: true })).toBe('undo');
		expect(matchesUndoChord({ key: 'Z', ctrlKey: true, shiftKey: true })).toBe('redo');
		expect(matchesUndoChord({ key: 'z', metaKey: true, shiftKey: true })).toBe('redo');
		expect(matchesUndoChord({ key: 'z' })).toBeNull();
		expect(matchesUndoChord({ key: 'y', ctrlKey: true })).toBeNull();
		expect(matchesUndoChord({ key: 'z', ctrlKey: true, altKey: true })).toBeNull();
	});

	it('leaves our own rail fields to the browser, but never the canvas', () => {
		for (const tagName of ['INPUT', 'TEXTAREA', 'SELECT'])
			expect(matchesUndoChord({ key: 'z', ctrlKey: true, target: { tagName } })).toBeNull();
		expect(matchesUndoChord({ key: 'z', ctrlKey: true, target: { tagName: 'IFRAME' } })).toBe(
			'undo'
		);
		expect(matchesUndoChord({ key: 'z', ctrlKey: true, target: { tagName: 'BODY' } })).toBe('undo');
	});
});

describe('the GrapesJS-side configuration', () => {
	it('names the superseded keymaps verbatim, and keeps selection off the undo stack', () => {
		// `Keymaps.remove` unbinds by the exact `keys.split(', ')` string, so a typo would leave GrapesJS's gated handler live; selection tracking is off so it can't eat a ctrl+z of its own.
		expect(SUPERSEDED_KEYMAPS).toEqual([
			{ id: 'core:undo', keys: '⌘+z, ctrl+z' },
			{ id: 'core:redo', keys: '⌘+shift+z, ctrl+shift+z' }
		]);
		expect(UNDO_MANAGER_CONFIG.trackSelection).toBe(false);
	});
});

describe('createEmailEditor undo wiring', () => {
	// Checked at the source level because the two branches must stay mutually exclusive — reordering them could hand a read-only principal a working undo with nothing on screen to notice.
	const source = readFileSync(new URL('./create.ts', import.meta.url), 'utf8');

	it('installs the undo shortcuts only on the non-read-only branch, and nowhere else', () => {
		const wiring = source.slice(source.indexOf('if (opts.readOnly) {'));
		expect(wiring).toMatch(
			/if \(opts\.readOnly\) \{\s*applyReadOnly\(editor\);\s*\} else \{[\s\S]*?installUndoShortcuts\(editor\)/
		);
		expect(source.match(/installUndoShortcuts\(editor\)/g)).toHaveLength(1);
	});

	it('hands the disposer to the component before destroy, so no listener outlives its editor', () => {
		expect(source).toMatch(/__disposeUndoShortcuts = installUndoShortcuts\(editor\)/);
		const cmp = readFileSync(new URL('./email-editor.svelte', import.meta.url), 'utf8');
		const dispose = cmp.indexOf('__disposeUndoShortcuts');
		expect(dispose, 'the component never disposes the undo shortcuts').toBeGreaterThan(0);
		expect(dispose).toBeLessThan(cmp.indexOf('editor.destroy()'));
	});

	it('passes the undo-manager config to grapesjs.init', () => {
		expect(source).toMatch(/undoManager:\s*\{\s*\.\.\.UNDO_MANAGER_CONFIG\s*\}/);
	});

	it('empties `protectedCss` so no editor chrome can be inlined into a delivered email', () => {
		// Observed in a real inbox: GrapesJS's `protectedCss` default (`* { box-sizing: border-box; }`) is PREPENDED to `getCss()` and gets inlined onto every element by `to_email_html`.
		expect(source).toMatch(/^\t\tprotectedCss: '',$/m);
		// Moved, not dropped: the same rule goes to `canvasCss` (canvas frame only, not part of `getCss()`) — without it the canvas silently changes box model.
		expect(source).toMatch(
			/^\t\tcanvasCss: '\* \{ box-sizing: border-box; \} body \{ margin: 0; \}',$/m
		);
		const authored = [
			...EMAIL_BLOCKS.map((b) => (typeof b.content === 'string' ? b.content : '')),
			STARTER_HTML
		].join('');
		expect(authored).not.toMatch(/box-sizing/);
	});
});

const cmp = (attrs: Record<string, unknown>, name?: string) => ({
	get: (k: string) => attrs[k],
	getName: () => name
});

describe('componentDisplayName', () => {
	it('prefers the block’s own name, then falls down the chain title-casing GrapesJS’s ids', () => {
		// `data-gjs-name` lands in `custom-name`, which is why a Button's cell reads 'Button fill' rather than GrapesJS's own 'Table cell'.
		expect(componentDisplayName(cmp({ 'custom-name': 'Button fill', type: 'cell' }))).toBe(
			'Button fill'
		);
		expect(componentDisplayName(cmp({ type: 'text' }, 'Heading'))).toBe('Heading');
		expect(componentDisplayName(cmp({ type: 'text' }))).toBe('Text');
		expect(componentDisplayName(cmp({ tagName: 'a' }))).toBe('A');
		expect(componentDisplayName(cmp({ type: 'text-node' }))).toBe('Text node');
		expect(componentDisplayName(undefined)).toBe('Component');
	});
});

describe('describeUndoGroup', () => {
	const model = (name: string) => cmp({ 'custom-name': name });

	it('names what was added, removed or moved', () => {
		// GrapesJS re-inserts rather than relocates: a drag is fused as an add AND a remove, so reporting it as 'Added' alone would be half the story.
		expect(
			describeUndoGroup({ index: 0, actions: [{ type: 'add', after: model('Heading') }] })
		).toBe('Added Heading');
		expect(
			describeUndoGroup({ index: 0, actions: [{ type: 'remove', before: model('Button') }] })
		).toBe('Removed Button');
		expect(
			describeUndoGroup({
				index: 0,
				actions: [
					{ type: 'remove', before: model('Divider') },
					{ type: 'add', after: model('Divider') }
				]
			})
		).toBe('Moved Divider');
	});

	it('stays neutral for a change, never leaks an event slug, and degrades without throwing', () => {
		// A `change` action's `after` is the WHOLE component, so a colour edit and an href edit carry identical keys — labelling by key (as the doc editor does) would call both "Style change".
		expect(
			describeUndoGroup({ index: 0, actions: [{ type: 'change', object: model('Heading') }] })
		).toBe('Edited Heading');
		const labelled = {
			index: 0,
			labels: ['component:add', 'move-component'],
			actions: [{ type: 'add', after: model('Heading') }]
		} as Parameters<typeof describeUndoGroup>[0] & { labels: string[] };
		expect(describeUndoGroup(labelled)).toBe('Added Heading');
		expect(describeUndoGroup({ index: 0, actions: [] })).toBe('Edit');
		expect(describeUndoGroup(undefined as unknown as Parameters<typeof describeUndoGroup>[0])).toBe(
			'Edit'
		);
	});
});

describe('undoHistory', () => {
	it('lists only APPLIED steps, newest first, and nothing without a stack', () => {
		const group = (index: number, name: string) => ({
			index,
			actions: [{ type: 'add', after: cmp({ 'custom-name': name }) }]
		});
		const editor = {
			UndoManager: {
				getPointer: () => 1,
				getGroupedStack: () => [group(0, 'Heading'), group(1, 'Button'), group(2, 'Divider')]
			}
		};
		expect(undoHistory(editor).map((e) => e.label)).toEqual(['Added Button', 'Added Heading']);
		expect(undoHistory(null)).toEqual([]);
		expect(undoHistory({ UndoManager: {} })).toEqual([]);
	});
});

describe('the undo/redo toolbar', () => {
	const cmpSource = readFileSync(new URL('./email-editor.svelte', import.meta.url), 'utf8');
	// Comments are stripped before the "must not contain" scan — the note explaining why we avoid `UndoManager.undo()` contains that very string.
	const cmpCode = cmpSource.replace(/<!--[\s\S]*?-->/g, '').replace(/^\s*\/\/.*$/gm, '');

	it('routes every button through the helpers, never GrapesJS’s gated door', () => {
		// The regression this guards: copying the document editor's `editor?.UndoManager?.undo()` is gated on `em.isEditing()`, which a block drop leaves set — the button would silently do nothing.
		expect(cmpSource).toMatch(/undoEmailEdit\(editor\)/);
		expect(cmpSource).toMatch(/redoEmailEdit\(editor\)/);
		expect(cmpSource).toMatch(/undoEmailEdits\(editor, i \+ 1\)/);
		expect(cmpCode).not.toMatch(/UndoManager\??\.?\s*\.?(undo|redo)\s*\(/);
	});

	it('does not render for a read-only principal, and teaches the chords', () => {
		const at = cmpSource.indexOf('aria-label="Undo"');
		expect(at, 'the Undo button is gone').toBeGreaterThan(0);
		const guards = [...cmpSource.slice(0, at).matchAll(/\{#if ([^}]*)\}/g)];
		expect(guards.at(-1)?.[1], 'the undo cluster is not guarded by !readOnly').toBe('!readOnly');
		expect(cmpSource).toMatch(/title="Undo \(Ctrl\+Z\)"/);
		expect(cmpSource).toMatch(/title="Redo \(Ctrl\+Shift\+Z\)"/);
	});

	it('refreshes enablement on load, where GrapesJS silently empties the stack', () => {
		// GrapesJS clears the undo stack silently on `change:ready` (parsing a template puts entries on it first) — a reading taken before that leaves Undo live forever.
		const listeners = cmpSource.match(
			/for \(const ev of \[([\s\S]*?)\]\)[\s\S]{0,40}?ed\.on\(ev, refreshUndo\);/
		)?.[1];
		expect(listeners, 'the refreshUndo listener loop was not found').toBeTruthy();
		for (const ev of ['load', 'undo', 'redo', 'component:add'])
			expect(listeners, `no enablement refresh on '${ev}'`).toContain(`'${ev}'`);
	});
});

// ── WHERE A DROP LANDS ───────────────────────────────────────────────────────────────────────────
import {
	correctedDropTarget,
	resolveDropDestination,
	SECTION_HTML,
	WIDENED_DROP_TARGETS
} from './create';

/* eslint-disable @typescript-eslint/no-explicit-any */

function node(tagName: string, children: any[] = []): any {
	const self: any = {
		children,
		get: (k: string) =>
			k === 'tagName' ? tagName : k === 'type' && tagName === 'body' ? 'wrapper' : undefined,
		components: () => ({ map: (f: any) => children.map(f), length: children.length }),
		parent: () => self.owner,
		index: () => self.owner?.children.indexOf(self) ?? 0
	};
	for (const child of children) child.owner = self;
	return self;
}

function landed(parent: any, child: any, at: number): any {
	parent.children.splice(at, 0, child);
	child.owner = parent;
	return child;
}

describe('correctedDropTarget', () => {
	it('sends content down to the cell one hop at a time, and leaves plumbing alone', () => {
		const cases: [string, Parameters<typeof correctedDropTarget>[0], unknown][] = [
			['a cell', { parent: 'td', child: 'p', at: 0, siblings: ['h1'] }, { action: 'keep' }],
			['a list', { parent: 'ul', child: 'li', at: 1, siblings: ['li'] }, { action: 'keep' }],
			// Plumbing (table/tbody/tr) is never relocated — every band the correction builds IS a table/row/cell, so "correcting" one would be infinite regress.
			[
				'a row in a body',
				{ parent: 'tbody', child: 'tr', at: 0, siblings: ['tr'] },
				{ action: 'keep' }
			],
			[
				'a body in a table',
				{ parent: 'table', child: 'tbody', at: 0, siblings: [] },
				{ action: 'keep' }
			],
			[
				'above the section',
				{ parent: 'table', child: 'p', at: 0, siblings: ['tbody'] },
				{ action: 'into', sibling: 0, end: false }
			],
			[
				'below the section',
				{ parent: 'table', child: 'p', at: 1, siblings: ['tbody'] },
				{ action: 'into', sibling: 0, end: true }
			],
			[
				'past a colgroup',
				{ parent: 'tbody', child: 'p', at: 0, siblings: ['colgroup', 'tr'] },
				{ action: 'into', sibling: 1, end: false }
			],
			[
				'between two columns',
				{ parent: 'tr', child: 'p', at: 1, siblings: ['td', 'td'] },
				{ action: 'into', sibling: 1, end: false }
			],
			[
				'past the last column',
				{ parent: 'tr', child: 'p', at: 2, siblings: ['td', 'td'] },
				{ action: 'into', sibling: 1, end: true }
			],
			['an empty row', { parent: 'tr', child: 'p', at: 0, siblings: [] }, { action: 'keep' }],
			// On the body, content gets a full-width band of its own; a table (every Layout/Divider/Button/Footer block) doesn't — it would double-wrap.
			[
				'content on the body',
				{ parent: 'wrapper', child: 'p', at: 1, siblings: ['table'] },
				{ action: 'wrap' }
			],
			[
				'a section on the body',
				{ parent: 'wrapper', child: 'table', at: 1, siblings: ['table'] },
				{ action: 'keep' }
			]
		];
		for (const [what, site, expected] of cases)
			expect(correctedDropTarget(site), what).toEqual(expected);
	});
});

describe('resolveDropDestination', () => {
	it('walks a table down to the cell, and answers null when nothing is wrong', () => {
		const top = node('p');
		const topCell = node('td', [node('h1'), node('p')]);
		landed(node('table', [node('tbody', [node('tr', [topCell])])]), top, 0);
		expect(resolveDropDestination(top)).toMatchObject({ destination: topCell, at: 0, wrap: false });

		const bottom = node('p');
		const bottomCell = node('td', [node('h1'), node('p')]);
		landed(node('table', [node('tbody', [node('tr', [bottomCell])])]), bottom, 1);
		expect(resolveDropDestination(bottom)).toMatchObject({
			destination: bottomCell,
			at: 2,
			wrap: false
		});

		const inCell = node('p');
		landed(node('td', [node('h1')]), inCell, 1);
		expect(resolveDropDestination(inCell)).toBeNull();

		const onBody = node('p');
		const body = node('body', [node('table')]);
		landed(body, onBody, 1);
		expect(resolveDropDestination(onBody)).toMatchObject({ destination: body, at: 1, wrap: true });

		const section = node('table');
		landed(node('body', [node('table')]), section, 1);
		expect(resolveDropDestination(section)).toBeNull();
	});

	it('wraps a body drop in a single-cell presentation band', () => {
		// `SECTION_HTML` has no palette block — it exists only for the editor's own body-level drop-wrap — so its shape is pinned directly here.
		expect(SECTION_HTML.match(/<td/g)).toHaveLength(1);
		expect(SECTION_HTML).toMatch(/^<table[^>]*role="presentation"/);
		expect(EMAIL_BLOCKS.some((b) => b.content === SECTION_HTML)).toBe(false);
	});

	it('never hands a read-only viewer a drop target', () => {
		// Neither `widenDropTargets` nor `installDropCorrection` may run in the read-only branch — relying on `applyReadOnly` clearing `droppable` after the fact would be the fragile arrangement this replaced.
		const source = readFileSync(new URL('./create.ts', import.meta.url), 'utf8');
		const from = source.indexOf('if (opts.readOnly) {');
		expect(from, 'the read-only branch moved').toBeGreaterThan(0);
		const to = source.indexOf('} else {', from);
		const readOnly = source.slice(from, to);
		expect(readOnly).toContain('applyReadOnly(editor)');
		for (const fn of ['widenDropTargets', 'installDropCorrection'])
			expect(readOnly, `${fn} runs for a read-only principal`).not.toContain(fn);
		expect(source.slice(to)).toContain('widenDropTargets(editor);');
		expect(source.slice(to)).toContain('installDropCorrection(editor);');
		expect(WIDENED_DROP_TARGETS).not.toContain('cell');
	});
});

describe('describeUndoGroup, for a corrected drop', () => {
	it('counts rather than pairs, so a relocated drop still reads as an addition', () => {
		// A corrected drop fuses sorter-add + our-remove + our-re-add, same component throughout — counting (not identity) is what tells a relocation from a move.
		const dropped = cmp({ 'custom-name': 'Text' });
		expect(
			describeUndoGroup({
				index: 0,
				actions: [
					{ type: 'add', after: dropped },
					{ type: 'remove', before: dropped },
					{ type: 'add', after: dropped }
				]
			})
		).toBe('Added Text');
		expect(
			describeUndoGroup({
				index: 0,
				actions: [
					{ type: 'remove', before: dropped },
					{ type: 'add', after: dropped },
					{ type: 'remove', before: dropped },
					{ type: 'add', after: dropped }
				]
			})
		).toBe('Moved Text');
	});
});

// ── THE BULLETPROOF BUTTON ───────────────────────────────────────────────────────────────────────
// Word ignores `display:inline-block`, so padding must sit on the CELL, not the `<a>`. Word also INVALIDATES an inline `!important` colour (renders black), so ink is stated three ways: an inner label span, the anchor's own unflagged colour, and `a.cta` in the MSO stylesheet.

function decls(style: string): Record<string, string> {
	const out: Record<string, string> = {};
	for (const decl of style.split(';')) {
		const at = decl.indexOf(':');
		if (at > 0) out[decl.slice(0, at).trim()] = decl.slice(at + 1).trim();
	}
	return out;
}

function authoredAnchors(): Record<string, string>[] {
	const html = [
		...EMAIL_BLOCKS.map((b) => (typeof b.content === 'string' ? b.content : '')),
		STARTER_HTML
	].join('');
	return [...html.matchAll(/<a\b[^>]*\sstyle="([^"]*)"/g)].map((m) => decls(m[1]));
}

function buttonLevels(html: string): {
	cell: Record<string, string>;
	anchor: Record<string, string>;
	label: Record<string, string>;
} {
	return {
		cell: decls(/<td\b[^>]*\sstyle="([^"]*background-color[^"]*)"/.exec(html)?.[1] ?? ''),
		anchor: decls(/<a\b[^>]*\sstyle="([^"]*)"/.exec(html)?.[1] ?? ''),
		label: decls(/<a\b[^>]*>\s*<span\b[^>]*\sstyle="([^"]*)"/.exec(html)?.[1] ?? '')
	};
}

describe('the bulletproof button', () => {
	const block = String(EMAIL_BLOCKS.find((b) => b.id === 'button')?.content);

	it('sizes the pill on the CELL, and leaves nothing box-like on the anchor', () => {
		const { cell, anchor } = buttonLevels(block);
		expect(cell.padding).toBe('12px 24px');
		expect(Object.keys(anchor).filter((p) => p.startsWith('padding'))).toEqual([]);
		expect(cell['background-color']).toBe('#0f172a');
		expect(cell['border-radius']).toBe('6px');
		expect(anchor).not.toHaveProperty('background-color');
		expect(anchor).not.toHaveProperty('border-radius');
		expect(Object.keys(anchor).sort()).toEqual([
			'color',
			'font-family',
			'font-size',
			'font-weight',
			'line-height',
			'text-decoration'
		]);
		expect(anchor.color).toBe('#ffffff');
		expect(anchor['text-decoration']).toBe('none');
	});

	it('puts the ink an Outlook honours on an inner label span, in both copies', () => {
		for (const [where, html] of [
			['the Button block', block],
			['the starter skeleton', STARTER_HTML]
		] as const) {
			const { anchor, label } = buttonLevels(html);
			expect(label.color, `${where}: the CTA label has no span carrying its own ink`).toBe(
				'#ffffff'
			);
			expect(Object.keys(label)).toEqual(['color']);
			expect(label.color, `${where}: the two ink layers disagree as authored`).toBe(anchor.color);
		}
	});

	it('carries the MSO class hook on the anchor, and nothing in the palette styles it', () => {
		// LAYER 3's hook: `app/email/document.py::MSO_CTA_INK` puts `a.cta{color:#fff !important}` behind `[if mso]` — the one place Word honours the flag.
		for (const [where, html] of [
			['the Button block', block],
			['the starter skeleton', STARTER_HTML]
		] as const) {
			expect(/<a\b[^>]*\sclass="cta"/.test(html), `${where}: no MSO class hook on the CTA`).toBe(
				true
			);
		}
		const classes = [
			...EMAIL_BLOCKS.map((b) => (typeof b.content === 'string' ? b.content : '')),
			STARTER_HTML
		]
			.join('')
			.match(/\sclass="([^"]*)"/g);
		expect(classes).toEqual([' class="cta"', ' class="cta"']);
	});

	it('reaches for `!important` NOWHERE — it is the declaration Word deletes', () => {
		// Counter-intuitively `!important` is the WRONG fix: Outlook 2007–2019 invalidates a flagged inline declaration (hteumeuleu/email-bugs#31), rendering it black instead.
		const authored = [
			...EMAIL_BLOCKS.map((b) => (typeof b.content === 'string' ? b.content : '')),
			STARTER_HTML
		].join('');
		expect(authored).not.toMatch(/!\s*important/i);
		for (const anchor of authoredAnchors())
			for (const [property, value] of Object.entries(anchor))
				expect(value, `an anchor flags ${property}`).not.toMatch(/!\s*important/i);
	});

	it('leaves no display:inline-block load-bearing for the hit area, here or on any other anchor', () => {
		expect(block).not.toMatch(/display\s*:/);
		expect(STARTER_HTML).not.toMatch(/display\s*:/);
		const anchors = authoredAnchors();
		expect(anchors.length).toBeGreaterThanOrEqual(4);
		for (const anchor of anchors)
			for (const property of Object.keys(anchor))
				expect(property, `an anchor declares ${property}`).not.toMatch(/^(display|padding)/);
	});

	it('is the SAME button in the starter skeleton, not a second copy of it', () => {
		expect(buttonLevels(STARTER_HTML)).toEqual(buttonLevels(block));
		expect(buttonLevels(STARTER_HTML).cell.padding).toBe('12px 24px');
		expect(buttonLevels(STARTER_HTML).anchor.color).toBe('#ffffff');
		expect(buttonLevels(STARTER_HTML).label.color).toBe('#ffffff');
	});

	it('names all FIVE levels, in the relationship reading the rail heads itself with', () => {
		expect([...block.matchAll(/data-gjs-name="([^"]+)"/g)].map((m) => m[1])).toEqual([
			'Button',
			'Button row',
			'Button fill',
			'Button link',
			'Button label'
		]);
	});

	it('gives the width sync nothing to act on, before or after an author restyles the cell', () => {
		// No level carries a `width` attribute: a button is sized by padding + label, and `width="100%"` would put the attribute and CSS out of step the moment the rail edited either.
		expect(block).not.toMatch(/\swidth=/);
		const { cell } = buttonLevels(block);
		const fill = stubCell({}, { ...cell, padding: '16px 32px' });
		railWithSync().emitStyleUpdate(fill, { style: fill.getStyle() });
		expect(fill.state.writes).toBe(0);
	});

	it('is a shape the drop correction reads without descending oddly', () => {
		const outermost = /^<([a-z]+)/.exec(block)?.[1] ?? '';
		expect(outermost).toBe('table');
		expect(
			correctedDropTarget({ parent: 'wrapper', child: outermost, at: 0, siblings: [] })
		).toEqual({ action: 'keep' });

		const fill = node('td', [node('a')]);
		const dropped = node('p');
		landed(node('table', [node('tbody', [node('tr', [fill])])]), dropped, 1);
		expect(resolveDropDestination(dropped)).toMatchObject({
			destination: fill,
			at: 1,
			wrap: false
		});

		expect(correctedDropTarget({ parent: 'a', child: 'p', at: 0, siblings: [] })).toEqual({
			action: 'keep'
		});
	});
});

// ── THE CTA INK, STATED TWICE ────────────────────────────────────────────────────────────────────
// The label's colour is declared on both the anchor AND its inner span (no single one survives every client) — same shape as the width divergence, same failure mode if the rail edits only one.

function stubInk(tagName: string, style: Record<string, unknown>, children: any[] = []): any {
	const self: any = {
		style: { ...style },
		writes: 0,
		get: (k: string) => (k === 'tagName' ? tagName : undefined),
		getStyle: () => ({ ...self.style }),
		components: () => ({ map: (f: any) => children.map(f), length: children.length }),
		parent: () => self.owner,
		addStyle: (patch: Record<string, unknown>) => {
			self.writes += 1;
			self.style = { ...self.style, ...patch };
		}
	};
	for (const child of children) child.owner = self;
	return self;
}

function inkPair(anchorColour = '#ffffff', labelColour = '#ffffff') {
	const label = stubInk('span', { color: labelColour });
	const anchor = stubInk('a', { color: anchorColour, 'font-size': '15px' }, [label]);
	return { anchor, label };
}

describe('mirroredLabelInk', () => {
	it('answers the new colour, bare, only when the two levels actually differ', () => {
		expect(mirroredLabelInk('#ff0000', '#ffffff')).toBe('#ff0000');
		// Already-equal colours return nothing — the write it DOES return re-triggers the partner's own pass, which is what stops it ping-ponging.
		expect(mirroredLabelInk('#ffffff', '#ffffff')).toBeUndefined();
	});

	it('strips the flag GrapesJS makes sticky, in the value AND in the comparison', () => {
		// GrapesJS's `Property.parseValue` makes `!important` STICKY (re-appended by `getFullValue` to every later write), so it must be stripped from both the value and the comparison.
		expect(mirroredLabelInk('#ff0000 !important', '#ffffff')).toBe('#ff0000');
		expect(mirroredLabelInk('#ff0000!important', '#ffffff')).toBe('#ff0000');
		expect(mirroredLabelInk('#ffffff !important', '#ffffff')).toBeUndefined();
		expect(mirroredLabelInk('#ffffff', '#ffffff !important')).toBeUndefined();
	});

	it('does nothing when there is no colour to mirror', () => {
		// No colour to mirror does nothing — writing the empty string across would CLEAR the partner's ink, worse than the two merely disagreeing.
		for (const empty of ['', '   ', ' !important', undefined, null, 42])
			expect(mirroredLabelInk(empty, '#ffffff'), String(empty)).toBeUndefined();
	});
});

describe('syncButtonLabelInk', () => {
	it('carries an anchor recolour down to the label span', () => {
		const { anchor, label } = inkPair();
		anchor.style.color = '#ff0000 !important'; // what the rail leaves behind, flag and all
		syncButtonLabelInk(anchor, { style: anchor.getStyle() });
		expect(label.style.color).toBe('#ff0000');
		expect(Object.keys(label.style)).toEqual(['color']);
		expect(anchor.style['font-size']).toBe('15px');
	});

	it('carries a label recolour back up to the anchor', () => {
		const { anchor, label } = inkPair();
		label.style.color = '#00ff00';
		syncButtonLabelInk(label, { style: label.getStyle() });
		expect(anchor.style.color).toBe('#00ff00');
	});

	it('settles after one hop rather than ping-ponging between the two levels', () => {
		const { anchor, label } = inkPair();
		anchor.style.color = '#ff0000';
		syncButtonLabelInk(anchor, { style: anchor.getStyle() });
		syncButtonLabelInk(label, { style: label.getStyle() }); // the echo
		expect(label.writes).toBe(1);
		expect(anchor.writes).toBe(0);
	});

	it('leaves everything that is not a CTA anchor/label pair alone', () => {
		// Matched STRUCTURALLY (an anchor whose only child is a span), not by `data-gjs-name` — which is session-scoped and absent on a reopened template.
		const plainLink = stubInk('a', { color: '#1d4ed8' }); // the Link block: no span, nothing to do
		syncButtonLabelInk(plainLink, { style: plainLink.getStyle() });
		expect(plainLink.style.color).toBe('#1d4ed8');

		const loose = stubInk('span', { color: '#94a3b8' });
		const paragraph = stubInk('p', {}, [loose]);
		syncButtonLabelInk(loose, { style: loose.getStyle() });
		expect(paragraph.writes).toBe(0);

		const first = stubInk('span', { color: '#ffffff' });
		const second = stubInk('span', { color: '#ffffff' });
		const wide = stubInk('a', { color: '#ff0000' }, [first, second]);
		syncButtonLabelInk(wide, { style: wide.getStyle() });
		expect(first.writes + second.writes).toBe(0);
	});

	it('skips the uncommitted writes the rail emits mid-drag', () => {
		const { anchor, label } = inkPair();
		anchor.style = { color: '#ff0000', __p: true };
		syncButtonLabelInk(anchor, { style: anchor.getStyle() });
		expect(label.writes).toBe(0);
	});

	it('is wired to the rail the editor really builds', () => {
		const ed = railWithSync();
		const { anchor, label } = inkPair();
		anchor.style.color = '#0f766e';
		ed.emitStyleUpdate(anchor, { style: anchor.getStyle() });
		expect(label.style.color).toBe('#0f766e');
	});
});

describe('the sectors are named for what an author is doing', () => {
	// A rail arranged by intent must not leave its sector NAMES as CSS taxonomy: 'Dimension'/'Typography' are a stylesheet's words, not an author's.
	it('renames the preset’s three', () => {
		const ed = stubStyleEditor({});
		configureStyleRail(ed);
		ed.fireLoad();
		expect(ed.sectorLabel('dimension')).toBe('Spacing & size');
		expect(ed.sectorLabel('typography')).toBe('Text');
		expect(ed.sectorName('dimension')).toBe('Spacing & size');
		expect(ed.sectorName('typography')).toBe('Text');
	});

	it('overrides the LOCALE, not just the model — the model alone renamed nothing', () => {
		const ed = stubStyleEditor({});
		configureStyleRail(ed);
		ed.fireLoad();
		for (const [id, name] of SECTOR_RENAMES) expect(ed.sectorLabel(id)).toBe(name);
	});

	it('does NOT rename the folded sector, which by then does not exist', () => {
		expect(SECTOR_RENAMES.map(([id]) => id)).not.toContain(FOLDED_SECTOR);
	});

	it('names none of them in CSS’s vocabulary', () => {
		for (const [, name] of SECTOR_RENAMES) {
			expect(name.toLowerCase()).not.toMatch(/dimension|typograph|decoration/);
		}
	});

	it('is idempotent, because `tune` runs twice by design', () => {
		const ed = stubStyleEditor({});
		configureStyleRail(ed);
		ed.fireLoad();
		ed.emitLoad();
		expect(ed.sectorName('typography')).toBe('Text');
		expect(ed.sectorLabel('typography')).toBe('Text');
	});

	it('leaves a sector the preset no longer has alone rather than throwing', () => {
		const ed = stubStyleEditor({});
		configureStyleRail(ed);
		expect(() => ed.emitLoad()).not.toThrow();
	});
});

// The palette is the only route to the house look for a never-seeded template — a block that quietly differs from `app/email/transport.py`'s own output is a second, invisible house look.
describe('the palette can rebuild the shipped look', () => {
	const block = (id: string) => String(EMAIL_BLOCKS.find((b) => b.id === id)?.content ?? '');

	it('has NO block that ships hand-written Jinja control flow', () => {
		// No block ships hand-written `{% if %}`: it would be an on-canvas conditional the Visibility panel can't edit. `{{ }}` merge fields are fine.
		const offenders = EMAIL_BLOCKS.filter((b) => /\{%/.test(String(b.content))).map((b) => b.id);
		expect(offenders).toEqual([]);
	});

	it('guards the footer’s support address as an authored condition', () => {
		const footer = block('msg-footer');
		expect(footer).toContain('data-if=');
		expect(footer).toContain('data-if-label="If brand.support_email has a value"');
		// The SPAN is guarded, not the anchor: gating the `<a>` alone would leave the " · " separator dangling when the address is absent.
		expect(footer).toMatch(/<span[^>]*data-if=[^>]*> · <a/);
	});

	it('gives the greeting a fallback, so a nameless send is not addressed to nobody', () => {
		// Without the fallback a nameless send renders "Hello ," — the notifier has real send sites with no recipient name, so this isn't hypothetical.
		const greeting = block('msg-greeting');
		expect(greeting).toContain('Hi ');
		expect(greeting).toContain("{{ first_name | fallback('there') }}");
		expect(greeting).toContain('data-field="first_name"');
		expect(greeting).toContain('data-field-fallback="there"');
	});

	it('gives the Email card the accent bar the shipped shell opens with', () => {
		// Outlook floors a cell at its line box, so a 4px accent rule needs `height` + `font-size:0` + `line-height` together, or it renders ~16px tall.
		const card = block('sec-container');
		expect(card).toContain('background-color:{{ brand.accent }}');
		expect(card).toMatch(/height:4px[^"]*font-size:0[^"]*line-height:4px/);
		expect(card).toContain('border-radius:10px 10px 0 0');
		expect(card).toContain('background-color:#ffffff;border-radius:10px"');
		expect(card).toContain('padding:32px 32px 36px');
	});
});

// Without a real label, every merge field would read as generic "Field" in the canvas badge, panel heading and layer tree alike.
describe('a merge field names itself', () => {
	it('uses the declared label', () => {
		expect(fieldSpanHtml('first_name')).toContain('data-gjs-name="First name"');
	});

	it('falls back to the path for a token nothing declares', () => {
		expect(fieldSpanHtml('some.unknown_thing')).toContain('data-gjs-name="some.unknown_thing"');
	});

	it('names the field, not the filtered form', () => {
		expect(fieldSpanHtml('due_date', 'date')).toContain('data-gjs-name="Due date"');
	});
});

describe('the block palette', () => {
	it('is sentence case throughout', () => {
		const offenders = EMAIL_BLOCKS.map((b) => b.label).filter((label) =>
			/\s[A-Z][a-z]/.test(label)
		);
		expect(offenders).toEqual([]);
	});
});
