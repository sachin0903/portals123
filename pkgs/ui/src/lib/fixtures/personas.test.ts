import { describe, expect, it } from 'vitest';
import type { VariantContext } from '../components/branding/variants.js';
import { VARIANT_THRESHOLDS, resolveVariant } from '../components/branding/variants.js';
import { adviser as demoAdviser, wrappers as demoWrappers } from './demo.js';
import { PERSONAS, personaByKey, type Persona } from './personas.js';

function ctxOf(p: Persona): VariantContext {
	return {
		audience: p.audience,
		accountCount: p.counts.accountCount,
		valuedAccountCount: p.counts.valuedAccountCount,
		holdingsCount: p.data.holdings!.length,
		activityCount: p.data.activity!.length,
		documentsCount: p.data.documents!.length,
		pageSlug: 'home'
	};
}

describe('PERSONAS', () => {
	it('is the four studio personas in account-count order', () => {
		expect(PERSONAS.map((p) => p.key)).toEqual([
			'individual2',
			'family6',
			'individual12',
			'trust300'
		]);
		expect(PERSONAS.map((p) => p.label)).toEqual([
			'Individual · 2 accounts',
			'Family · 6 accounts',
			'Individual · 12 accounts',
			'Trust · 300 accounts'
		]);
		expect(PERSONAS.map((p) => p.audience)).toEqual([
			'individual',
			'individual',
			'individual',
			'organisation'
		]);
	});

	it('personaByKey resolves each key to the same object, call after call', () => {
		for (const p of PERSONAS) {
			expect(personaByKey(p.key)).toBe(p);
			// Deterministic: two lookups deep-equal (and the data is plain — JSON-safe, no getters
			// or functions — so a snapshot of it is stable too).
			expect(personaByKey(p.key)).toEqual(personaByKey(p.key));
			expect(JSON.parse(JSON.stringify(p.data))).toEqual(p.data);
		}
	});

	// The generation is seeded formulas, not Math.random/Date.now — pin spot values so any drift
	// towards nondeterminism (or an engine-dependent hash) fails loudly rather than flaking.
	it('generated trust data is deterministic (pinned spot values)', () => {
		const trust = personaByKey('trust300');
		const first = trust.data.wrappers![0]!;
		expect(first.name).toBe('Portfolio 001 · GIA');
		expect(first.owner).toBe('The Aldercroft Settlement');
		// Exact literals: the seeded hash is integer-only maths, so these hold on every engine.
		expect(first.value).toBe(280385.95);
		expect(first.gainPct).toBe(0.101);
		expect(first.gain).toBe(25721.15);
		expect(first.trend).toHaveLength(8);
		// Two evaluations of the same seeded formula: the array literal built at module load must
		// re-derive from the same inputs (value/gain), not from any per-run entropy.
		expect(first.trend.at(-1)).toBe(Math.round((first.value / 1000) * 10) / 10);
	});

	it('every persona carries non-null data for every portal slice', () => {
		for (const p of PERSONAS) {
			expect(p.data.totals, p.key).toBeDefined();
			expect(p.data.performance, p.key).toHaveLength(8);
			expect(p.data.wrappers!.length, p.key).toBeGreaterThan(0);
			expect(p.data.allocations!.length, p.key).toBeGreaterThan(0);
			expect(p.data.holdings!.length, p.key).toBeGreaterThan(0);
			expect(p.data.activity!.length, p.key).toBeGreaterThan(0);
			expect(p.data.documents!.length, p.key).toBeGreaterThan(0);
			expect(p.data.adviser, p.key).toBeDefined();
			expect(p.data.tasks!.length, p.key).toBeGreaterThan(0);
			expect(p.data.onboarding, p.key).toBeDefined();
		}
	});

	it('counts match the data where they should', () => {
		for (const p of PERSONAS) expect(p.data.wrappers!.length, p.key).toBe(p.counts.accountCount);
		for (const p of PERSONAS) {
			const valued = p.data.wrappers!.filter((w) => w.value > 0).length;
			expect(valued, p.key).toBe(p.counts.valuedAccountCount);
		}
		// Totals are the sum of the wrappers they headline.
		for (const p of PERSONAS) {
			const sum = p.data.wrappers!.reduce((s, w) => s + w.value, 0);
			expect(p.data.totals!.value, p.key).toBeCloseTo(sum, 2);
		}
	});

	it('personas own their data — mutating one never corrupts the shared demo fixture', () => {
		const individual = personaByKey('individual2');
		expect(individual.data.wrappers![0]).not.toBe(demoWrappers[0]);
		expect(individual.data.adviser).not.toBe(demoAdviser);
	});
});

describe('variant boundaries', () => {
	it('individual2 stays under every threshold (accounts → stack)', () => {
		const ctx = ctxOf(personaByKey('individual2'));
		expect(ctx.accountCount).toBeLessThanOrEqual(VARIANT_THRESHOLDS.accountsStackMax);
		expect(ctx.holdingsCount).toBeLessThan(VARIANT_THRESHOLDS.holdingsTableMin);
		expect(ctx.activityCount).toBeLessThan(VARIANT_THRESHOLDS.activityTableMin);
		expect(ctx.documentsCount).toBeLessThan(VARIANT_THRESHOLDS.documentsGroupedMin);
		expect(resolveVariant('accounts', undefined, ctx)).toBe('stack');
		expect(resolveVariant('holdings', undefined, ctx)).toBe('top');
		expect(resolveVariant('activity', undefined, ctx)).toBe('list');
		expect(resolveVariant('documents', undefined, ctx)).toBe('recent');
	});

	it('family6 flips holdings/activity → table and documents → grouped, accounts still stacked', () => {
		const ctx = ctxOf(personaByKey('family6'));
		expect(resolveVariant('accounts', undefined, ctx)).toBe('stack');
		expect(resolveVariant('holdings', undefined, ctx)).toBe('table');
		expect(resolveVariant('activity', undefined, ctx)).toBe('table');
		expect(resolveVariant('documents', undefined, ctx)).toBe('grouped');
	});

	it("individual12 lands in the accounts 'cards' band no other persona covers", () => {
		const ctx = ctxOf(personaByKey('individual12'));
		expect(ctx.accountCount).toBeGreaterThan(VARIANT_THRESHOLDS.accountsStackMax);
		expect(ctx.accountCount).toBeLessThanOrEqual(VARIANT_THRESHOLDS.accountsCardsMax);
		expect(resolveVariant('accounts', undefined, ctx)).toBe('cards');
		// 12 holdings ≥ 8 and 14 activity rows ≥ 12 → tables; 6 documents < 8 → recent.
		expect(resolveVariant('holdings', undefined, ctx)).toBe('table');
		expect(resolveVariant('activity', undefined, ctx)).toBe('table');
		expect(resolveVariant('documents', undefined, ctx)).toBe('recent');
	});

	it('trust300 crosses every boundary (accounts → summary via counts)', () => {
		const ctx = ctxOf(personaByKey('trust300'));
		expect(ctx.accountCount).toBeGreaterThan(VARIANT_THRESHOLDS.accountsCardsMax);
		expect(resolveVariant('accounts', undefined, ctx)).toBe('summary');
		expect(resolveVariant('holdings', undefined, ctx)).toBe('table');
		expect(resolveVariant('activity', undefined, ctx)).toBe('table');
		expect(resolveVariant('documents', undefined, ctx)).toBe('grouped');
	});
});
