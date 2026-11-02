import { describe, expect, it } from 'vitest';
import type { BrandingBlock } from '@graphene/auth';
import { CLIENT_BLOCK_BY_TYPE, type SettingDescriptor } from './client-blocks.js';
import { defaultPages, PAGE_CAPS } from './portal-pages.js';
import { duplicateBlock } from './page-ops.js';
import { PERSONAS, personaByKey } from '../../fixtures/personas.js';
import type { VariantContext } from './variants.js';
import {
	demoVariantContext,
	inspectorValue,
	layoutToRows,
	materialiseWorkingPages,
	navToggleBlock,
	parseNumberSetting,
	personaVariantContext,
	resolveConcreteBlocks,
	rowSignature,
	rowsToLayout,
	slugErrorMessage,
	withSetting
} from './pages-view.js';

describe('materialiseWorkingPages', () => {
	it('falls back to the default page set for null/empty (never-customised tenants)', () => {
		expect(materialiseWorkingPages(null)).toEqual(defaultPages());
		expect(materialiseWorkingPages(undefined)).toEqual(defaultPages());
		expect(materialiseWorkingPages([])).toEqual(defaultPages());
	});

	it('deep-clones stored pages so pre-commit ops never alias the draft', () => {
		const stored = defaultPages();
		const working = materialiseWorkingPages(stored);
		expect(working).toEqual(stored);
		expect(working).not.toBe(stored);
		expect(working[0]).not.toBe(stored[0]);
		expect(working[0]!.layouts.default).not.toBe(stored[0]!.layouts.default);
	});
});

describe('builder row round-trip (layoutToRows → rowsToLayout)', () => {
	const layout: BrandingBlock[] = [
		{ id: 'holdings', type: 'holdings', visible: true, w: 8, h: 2, settings: { limit: 5 } },
		{
			id: 'holdings-2',
			type: 'holdings',
			visible: false,
			w: 4,
			h: 1,
			settings: { variant: 'table' }
		},
		{ id: 'message', type: 'message', visible: true, w: 12, h: 1 }
	];

	it('is byte-stable: ids, order, footprint and settings all survive an emit cycle', () => {
		expect(rowsToLayout(layoutToRows(layout))).toEqual(layout);
	});

	it('keeps two same-type blocks apart — different ids AND different settings survive', () => {
		const [a, b] = rowsToLayout(layoutToRows(layout));
		expect(a!.id).toBe('holdings');
		expect(b!.id).toBe('holdings-2');
		expect(a!.settings).toEqual({ limit: 5 });
		expect(b!.settings).toEqual({ variant: 'table' });
	});

	it('round-trips a duplicateBlock copy: fresh id after the original, settings carried', () => {
		const next = duplicateBlock(layout, 'holdings');
		expect(next).toHaveLength(4);
		const copy = next[1]!;
		expect(copy.id).not.toBe('holdings');
		expect(copy.settings).toEqual({ limit: 5 });
		const cycled = rowsToLayout(layoutToRows(next));
		expect(cycled).toEqual(next);
		expect(new Set(cycled.map((b) => b.id)).size).toBe(4);
	});

	it('mints unique ids for legacy blocks that carry none (or collide)', () => {
		const legacy: BrandingBlock[] = [
			{ type: 'accounts', w: 6, h: 1 },
			{ type: 'accounts', w: 6, h: 1 },
			{ id: 'accounts', type: 'accounts', w: 12, h: 1 }
		];
		const ids = layoutToRows(legacy).map((r) => r.id);
		expect(new Set(ids).size).toBe(3);
	});

	it('signature changes when settings change (the inspector edits must re-seed tiles)', () => {
		const rows = layoutToRows(layout);
		const edited = rows.map((r) => (r.id === 'holdings' ? { ...r, settings: { limit: 9 } } : r));
		expect(rowSignature(edited)).not.toBe(rowSignature(rows));
	});
});

describe('inspectorValue', () => {
	const variant = CLIENT_BLOCK_BY_TYPE['accounts']!.settings.find((s) => s.key === 'variant')!;
	const number: SettingDescriptor = {
		key: 'limit',
		label: 'Limit',
		control: 'number',
		min: 1,
		max: 50
	};
	const toggle: SettingDescriptor = { key: 'hide_when_empty', label: 'Hide', control: 'switch' };
	const text: SettingDescriptor = { key: 'title', label: 'Title', control: 'text' };

	it('segmented: shows the stored variant when it is on the allowlist', () => {
		expect(inspectorValue(variant, { variant: 'cards' })).toBe('cards');
	});

	it("segmented: unset or junk degrades to the first option ('auto' leads variant lists)", () => {
		expect(inspectorValue(variant, undefined)).toBe('auto');
		expect(inspectorValue(variant, { variant: 'nonsense' })).toBe('auto');
	});

	it('switch: absent means ON (matches the renderers’ `!== false` reads)', () => {
		expect(inspectorValue(toggle, undefined)).toBe(true);
		expect(inspectorValue(toggle, { hide_when_empty: false })).toBe(false);
	});

	it('switch: a defaultOn:false descriptor shows OFF when absent (the toolbar switches read `=== true`)', () => {
		const offToggle: SettingDescriptor = {
			key: 'toolbar',
			label: 'Search & filters',
			control: 'switch',
			defaultOn: false
		};
		expect(inspectorValue(offToggle, undefined)).toBe(false);
		expect(inspectorValue(offToggle, { toolbar: true })).toBe(true);
		// Junk shows the default — exactly what a `=== true` widget read renders for it.
		expect(inspectorValue(offToggle, { toolbar: 'yes' })).toBe(false);
	});

	it('number: absent shows blank, never a fake zero', () => {
		expect(inspectorValue(number, undefined)).toBe('');
		expect(inspectorValue(number, { limit: 7 })).toBe(7);
		expect(inspectorValue(number, { limit: 'seven' })).toBe('');
	});

	it('text: absent shows empty', () => {
		expect(inspectorValue(text, undefined)).toBe('');
		expect(inspectorValue(text, { title: 'Hello' })).toBe('Hello');
	});
});

describe('withSetting', () => {
	it('returns a fresh object and leaves the original untouched', () => {
		const before = { limit: 5 };
		const after = withSetting(before, 'variant', 'cards');
		expect(after).toEqual({ limit: 5, variant: 'cards' });
		expect(before).toEqual({ limit: 5 });
		expect(after).not.toBe(before);
	});

	it("clears the key on undefined and '' (unset ⇒ back to the default/auto)", () => {
		expect(withSetting({ limit: 5 }, 'limit', undefined)).toEqual({});
		expect(withSetting({ title: 'x' }, 'title', '')).toEqual({});
	});

	it('starts from an empty object when there were no settings', () => {
		expect(withSetting(undefined, 'variant', 'table')).toEqual({ variant: 'table' });
	});
});

describe('parseNumberSetting', () => {
	const d: SettingDescriptor = { key: 'limit', label: 'Limit', control: 'number', min: 1, max: 50 };

	it('blank and junk clear the key', () => {
		expect(parseNumberSetting('', d)).toBeUndefined();
		expect(parseNumberSetting('  ', d)).toBeUndefined();
		expect(parseNumberSetting('abc', d)).toBeUndefined();
	});

	it('rounds and clamps to the descriptor bounds (the API clamps identically)', () => {
		expect(parseNumberSetting('7', d)).toBe(7);
		expect(parseNumberSetting('7.6', d)).toBe(8);
		expect(parseNumberSetting('0', d)).toBe(1);
		expect(parseNumberSetting('999', d)).toBe(50);
	});
});

describe('variant contexts', () => {
	it('personaVariantContext maps the persona’s counts, slices and audience', () => {
		for (const persona of PERSONAS) {
			const ctx = personaVariantContext(persona, 'home');
			expect(ctx.audience).toBe(persona.audience);
			expect(ctx.accountCount).toBe(persona.counts.accountCount);
			expect(ctx.valuedAccountCount).toBe(persona.counts.valuedAccountCount);
			expect(ctx.holdingsCount).toBe(persona.data.holdings?.length ?? 0);
			expect(ctx.activityCount).toBe(persona.data.activity?.length ?? 0);
			expect(ctx.documentsCount).toBe(persona.data.documents?.length ?? 0);
			expect(ctx.pageSlug).toBe('home');
		}
	});

	it('personaVariantContext lets the caller pin the audience tab being edited', () => {
		const persona = personaByKey('individual2');
		expect(personaVariantContext(persona, 'home', 'organisation').audience).toBe('organisation');
	});

	it('demoVariantContext derives counts from the demo fixture it will render', () => {
		const demo = {
			totals: { value: 0, gain: 0, gainPct: 0 },
			performance: [],
			wrappers: [{}, {}, {}],
			allocations: [],
			holdings: [{}],
			activity: [{}, {}]
		} as never;
		const ctx = demoVariantContext(demo, 'accounts');
		expect(ctx.accountCount).toBe(3);
		expect(ctx.holdingsCount).toBe(1);
		expect(ctx.activityCount).toBe(2);
		expect(ctx.documentsCount).toBe(0);
		expect(ctx.audience).toBe('individual');
	});
});

describe('resolveConcreteBlocks', () => {
	const ctx: VariantContext = {
		audience: 'organisation',
		accountCount: 300,
		valuedAccountCount: 280,
		holdingsCount: 40,
		activityCount: 100,
		documentsCount: 20,
		pageSlug: 'accounts'
	};

	it("collapses 'auto' to the concrete variant PortalPage expects", () => {
		const [b] = resolveConcreteBlocks([{ id: 'accounts', type: 'accounts' }], ctx);
		expect(b!.settings?.['variant']).toBe('summary'); // 300 accounts ⇒ summary
	});

	it('respects an explicitly pinned variant', () => {
		const [b] = resolveConcreteBlocks(
			[{ id: 'accounts', type: 'accounts', settings: { variant: 'stack', limit: 3 } }],
			ctx
		);
		expect(b!.settings?.['variant']).toBe('stack');
		expect(b!.settings?.['limit']).toBe(3); // other settings ride along
	});

	it('leaves unknown block types untouched (PortalPage placeholders them)', () => {
		const unknown: BrandingBlock = { id: 'x', type: 'someFutureBlock' };
		expect(resolveConcreteBlocks([unknown], ctx)[0]).toBe(unknown);
	});

	it('does not mutate the input blocks', () => {
		const block: BrandingBlock = { id: 'accounts', type: 'accounts' };
		resolveConcreteBlocks([block], ctx);
		expect(block.settings).toBeUndefined();
	});
});

describe('navToggleBlock', () => {
	const page = (id: string, nav: boolean) => ({
		id,
		slug: id,
		label: id,
		nav,
		layouts: { default: [] }
	});

	it('refuses to hide the last shown page', () => {
		const pages = [page('a', true), page('b', false)];
		expect(navToggleBlock(pages, 'a')).toMatch(/at least one/i);
		expect(navToggleBlock(pages, 'b')).toBeNull(); // showing b is fine
	});

	it('refuses to show a page once maxNavPages are shown', () => {
		const pages = [
			...Array.from({ length: PAGE_CAPS.maxNavPages }, (_, i) => page(`p${i}`, true)),
			page('hidden', false)
		];
		expect(navToggleBlock(pages, 'hidden')).toMatch(/at most/i);
		expect(navToggleBlock(pages, 'p0')).toBeNull(); // hiding one of six is fine
	});

	it('returns null in the ordinary middle ground', () => {
		const pages = [page('a', true), page('b', true), page('c', false)];
		expect(navToggleBlock(pages, 'b')).toBeNull();
		expect(navToggleBlock(pages, 'c')).toBeNull();
	});
});

describe('slugErrorMessage', () => {
	it('has human copy for every refusal code', () => {
		expect(slugErrorMessage('reserved')).toMatch(/portal itself/);
		expect(slugErrorMessage('duplicate')).toMatch(/another page/i);
		expect(slugErrorMessage('invalid')).toMatch(/lowercase/i);
	});
});
