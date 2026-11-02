import { describe, expect, it } from 'vitest';
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import { customise, isCustomised, layoutFor, revert, setLayout } from './page-audience.js';

function makePage(layouts?: Partial<BrandingPage['layouts']>): BrandingPage {
	return {
		id: 'home',
		slug: 'home',
		label: 'Home',
		icon: 'house',
		nav: true,
		layouts: {
			default: [
				{ id: 'accounts', type: 'accounts', visible: true, w: 12, h: 4, settings: { limit: 5 } },
				{ id: 'message', type: 'message', visible: true, w: 6, h: 2 }
			],
			...layouts
		}
	};
}

const override: BrandingBlock[] = [
	{ id: 'documents', type: 'documents', visible: true, w: 12, h: 4 }
];

describe('layoutFor', () => {
	it("'default' returns layouts.default", () => {
		const p = makePage({ individual: override });
		expect(layoutFor(p, 'default')).toBe(p.layouts.default);
	});

	it('returns the override when the audience carries one', () => {
		const p = makePage({ individual: override });
		expect(layoutFor(p, 'individual')).toBe(override);
		expect(layoutFor(p, 'organisation')).toBe(p.layouts.default);
	});

	it('falls back to default when the audience has no override', () => {
		const p = makePage();
		expect(layoutFor(p, 'individual')).toBe(p.layouts.default);
		expect(layoutFor(p, 'organisation')).toBe(p.layouts.default);
	});

	it('an explicit-null wire value reads as default', () => {
		const p = makePage({ organisation: null });
		expect(layoutFor(p, 'organisation')).toBe(p.layouts.default);
	});

	// A firm hiding a page's content from trusts made a decision — [] must be honoured, not
	// collapsed to the default (mirrors resolveAudienceLayout in portal-pages.ts).
	it('honours an EMPTY ARRAY override', () => {
		const p = makePage({ organisation: [] });
		expect(layoutFor(p, 'organisation')).toEqual([]);
	});
});

describe('isCustomised', () => {
	it('false when absent or explicitly null', () => {
		expect(isCustomised(makePage(), 'individual')).toBe(false);
		expect(isCustomised(makePage({ individual: null }), 'individual')).toBe(false);
	});

	it('true when the audience carries an override — including an empty one', () => {
		expect(isCustomised(makePage({ organisation: override }), 'organisation')).toBe(true);
		expect(isCustomised(makePage({ organisation: [] }), 'organisation')).toBe(true);
	});
});

describe('customise', () => {
	it('gives the audience a deep copy of the default layout', () => {
		const p = makePage();
		const next = customise(p, 'organisation');
		expect(next.layouts.organisation).toEqual(p.layouts.default);
		expect(next.layouts.organisation).not.toBe(p.layouts.default);
	});

	it('mutating the copy leaves the default untouched (settings deep-copied)', () => {
		const p = makePage();
		const next = customise(p, 'individual');
		const copied = next.layouts.individual![0]!;
		copied.settings!['limit'] = 99;
		copied.w = 1;
		expect(p.layouts.default[0]!.settings).toEqual({ limit: 5 });
		expect(p.layouts.default[0]!.w).toBe(12);
	});

	it('keeps block ids stable (dnd keys must not jump on copy-on-write)', () => {
		const next = customise(makePage(), 'individual');
		expect(next.layouts.individual!.map((b) => b.id)).toEqual(['accounts', 'message']);
	});

	it('is a no-op (same reference) when already customised — even by an empty array', () => {
		const withOverride = makePage({ individual: override });
		expect(customise(withOverride, 'individual')).toBe(withOverride);
		const withEmpty = makePage({ individual: [] });
		expect(customise(withEmpty, 'individual')).toBe(withEmpty);
	});

	it('does treat an explicit null as uncustomised (copies over it)', () => {
		const p = makePage({ organisation: null });
		expect(customise(p, 'organisation').layouts.organisation).toEqual(p.layouts.default);
	});

	it('never mutates the input page', () => {
		const p = makePage();
		customise(p, 'organisation');
		expect(p.layouts.organisation).toBeUndefined();
	});
});

describe('revert', () => {
	it('drops the override entirely (key removed, not nulled)', () => {
		const next = revert(makePage({ individual: override }), 'individual');
		expect('individual' in next.layouts).toBe(false);
		expect(layoutFor(next, 'individual')).toBe(next.layouts.default);
	});

	it('drops an explicit-null slot too (no tombstones left in the stored shape)', () => {
		const next = revert(makePage({ organisation: null }), 'organisation');
		expect('organisation' in next.layouts).toBe(false);
	});

	it('leaves the other audience and the default alone', () => {
		const p = makePage({ individual: override, organisation: [] });
		const next = revert(p, 'individual');
		expect(next.layouts.organisation).toEqual([]);
		expect(next.layouts.default).toBe(p.layouts.default);
	});

	it('never mutates the input page', () => {
		const p = makePage({ individual: override });
		revert(p, 'individual');
		expect(p.layouts.individual).toBe(override);
	});
});

describe('setLayout', () => {
	it("'default' writes layouts.default", () => {
		const p = makePage();
		const next = setLayout(p, 'default', override);
		expect(next.layouts.default).toBe(override);
		expect(p.layouts.default).not.toBe(override); // input untouched
	});

	it('an audience tab writes its own slot only', () => {
		const p = makePage();
		const next = setLayout(p, 'organisation', override);
		expect(next.layouts.organisation).toBe(override);
		expect(next.layouts.default).toBe(p.layouts.default);
		expect(next.layouts.individual).toBeUndefined();
	});

	it('an empty array writes as an empty array (stays customised)', () => {
		const next = setLayout(makePage(), 'organisation', []);
		expect(isCustomised(next, 'organisation')).toBe(true);
	});
});
