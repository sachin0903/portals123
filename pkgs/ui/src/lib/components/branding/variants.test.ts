import { describe, expect, it } from 'vitest';
import type { BlockType } from './client-blocks.js';
import { CLIENT_BLOCKS } from './client-blocks.js';
import { resolveVariant, VARIANT_THRESHOLDS, type VariantContext } from './variants.js';

function ctx(overrides: Partial<VariantContext> = {}): VariantContext {
	return {
		audience: 'individual',
		accountCount: 2,
		valuedAccountCount: 2,
		holdingsCount: 4,
		activityCount: 5,
		documentsCount: 3,
		pageSlug: 'home',
		...overrides
	};
}

describe('resolveVariant auto rules', () => {
	// [type, context overrides, expected] — boundary values on both sides of every threshold.
	const cases: [BlockType, Partial<VariantContext>, string][] = [
		// portfolioHeader: hero at home, compact elsewhere.
		['portfolioHeader', { pageSlug: 'home' }, 'hero'],
		['portfolioHeader', { pageSlug: 'accounts' }, 'compact'],
		// accounts: stack ≤6, cards ≤24, summary above. 'table' never auto.
		['accounts', { accountCount: 1 }, 'stack'],
		['accounts', { accountCount: 6 }, 'stack'],
		['accounts', { accountCount: 7 }, 'cards'],
		['accounts', { accountCount: 24 }, 'cards'],
		['accounts', { accountCount: 25 }, 'summary'],
		// holdings: table from 8, or always for an organisation.
		['holdings', { holdingsCount: 7 }, 'top'],
		['holdings', { holdingsCount: 8 }, 'table'],
		['holdings', { holdingsCount: 1, audience: 'organisation' }, 'table'],
		// activity: table from 12.
		['activity', { activityCount: 11 }, 'list'],
		['activity', { activityCount: 12 }, 'table'],
		// documents: grouped from 8.
		['documents', { documentsCount: 7 }, 'recent'],
		['documents', { documentsCount: 8 }, 'grouped'],
		// allocation: donut always (bars only explicit).
		['allocation', {}, 'donut'],
		['allocation', { audience: 'organisation', accountCount: 40 }, 'donut']
	];
	for (const [type, overrides, expected] of cases)
		it(`${type} ${JSON.stringify(overrides)} -> ${expected}`, () => {
			expect(resolveVariant(type, undefined, ctx(overrides))).toBe(expected);
		});
});

describe('explicit variants', () => {
	it('an explicit allowlisted variant wins over the auto rule', () => {
		expect(resolveVariant('accounts', { variant: 'table' }, ctx({ accountCount: 2 }))).toBe(
			'table'
		);
		expect(resolveVariant('allocation', { variant: 'bars' }, ctx())).toBe('bars');
		expect(resolveVariant('holdings', { variant: 'top' }, ctx({ holdingsCount: 50 }))).toBe('top');
	});

	it('the statement looks are explicit-only: chosen they win, but auto NEVER picks them', () => {
		expect(resolveVariant('portfolioHeader', { variant: 'canvas' }, ctx())).toBe('canvas');
		expect(
			resolveVariant('portfolioHeader', { variant: 'ledger' }, ctx({ pageSlug: 'accounts' }))
		).toBe('ledger');
		// The auto rule is UNCHANGED by their arrival: hero at home, compact elsewhere.
		expect(resolveVariant('portfolioHeader', undefined, ctx({ pageSlug: 'home' }))).toBe('hero');
		expect(resolveVariant('portfolioHeader', undefined, ctx({ pageSlug: 'reports' }))).toBe(
			'compact'
		);
	});

	it('junk on portfolioHeader still degrades through the auto path, not to a blank header', () => {
		expect(resolveVariant('portfolioHeader', { variant: 'sparkles' }, ctx())).toBe('hero');
		expect(resolveVariant('portfolioHeader', { variant: 7 }, ctx({ pageSlug: 'docs' }))).toBe(
			'compact'
		);
	});

	it("junk falls through to the auto path — a stale setting degrades, it doesn't blank the block", () => {
		expect(resolveVariant('accounts', { variant: 'sparkles' }, ctx({ accountCount: 2 }))).toBe(
			'stack'
		);
		expect(resolveVariant('accounts', { variant: 42 }, ctx({ accountCount: 25 }))).toBe('summary');
		// A variant that exists on ANOTHER type is still junk here.
		expect(resolveVariant('holdings', { variant: 'cards' }, ctx({ holdingsCount: 2 }))).toBe('top');
	});

	it("explicit 'auto' takes the auto path", () => {
		expect(resolveVariant('documents', { variant: 'auto' }, ctx({ documentsCount: 9 }))).toBe(
			'grouped'
		);
	});
});

describe("the return contract: concrete names for multi-variant types, 'auto' for single", () => {
	// Tested over the whole registry so a type added later cannot dodge the contract.
	for (const meta of CLIENT_BLOCKS) {
		const multi = meta.variants.length > 1;
		it(`${meta.type} (${multi ? 'multi' : 'single'}-variant)`, () => {
			const resolved = resolveVariant(meta.type, undefined, ctx());
			if (multi) {
				expect(resolved).not.toBe('auto');
				expect(meta.variants).toContain(resolved);
			} else {
				expect(resolved).toBe('auto');
			}
		});
	}

	it("single-variant types return 'auto' even when a setting names their only variant", () => {
		expect(resolveVariant('adviser', { variant: 'auto' }, ctx())).toBe('auto');
		expect(resolveVariant('message', { variant: 'junk' }, ctx())).toBe('auto');
	});
});

describe('VARIANT_THRESHOLDS', () => {
	it('stays the documented tuning surface', () => {
		expect(VARIANT_THRESHOLDS).toEqual({
			accountsStackMax: 6,
			accountsCardsMax: 24,
			holdingsTableMin: 8,
			activityTableMin: 12,
			documentsGroupedMin: 8
		});
	});
});
