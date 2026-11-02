import { describe, expect, it } from 'vitest';
import { PERSONAS } from '../../fixtures/personas.js';
import { personaVariantContext } from './pages-view.js';
import { resolveVariant } from './variants.js';

/**
 * The persona × block resolver table. The four studio personas are deterministic and sit either
 * side of every VARIANT_THRESHOLDS boundary on purpose, so the concrete variant each one produces
 * is pinned here as exact strings — the preview (shell-preview → resolveConcreteBlocks) renders
 * exactly what this table says, and a threshold or persona change must show up as a diff below.
 */

// persona → { blockType → expected variant } on the home page.
const HOME_EXPECTATIONS: Record<string, Record<string, string>> = {
	// Under every threshold: 2 accounts, 4 holdings, 9 activity rows, 5 documents.
	individual2: {
		portfolioHeader: 'hero',
		accounts: 'stack',
		holdings: 'top',
		activity: 'list',
		documents: 'recent'
	},
	// At the stack maximum (6 accounts) but over the holdings (10 ≥ 8), activity (15 ≥ 12) and
	// documents (9 ≥ 8) lines.
	family6: {
		portfolioHeader: 'hero',
		accounts: 'stack',
		holdings: 'table',
		activity: 'table',
		documents: 'grouped'
	},
	// The accounts 'cards' band (12 sits in 7–24) with holdings (12 ≥ 8) and activity (14 ≥ 12)
	// over their table lines but documents (6 < 8) still under grouped.
	individual12: {
		portfolioHeader: 'hero',
		accounts: 'cards',
		holdings: 'table',
		activity: 'table',
		documents: 'recent'
	},
	// Over everything: 300 accounts (> 24 → summary), 40 holdings, 50 activity rows, 12 documents
	// — and an organisation, which forces the holdings table regardless of count.
	trust300: {
		portfolioHeader: 'hero',
		accounts: 'summary',
		holdings: 'table',
		activity: 'table',
		documents: 'grouped'
	}
};

describe('persona × block resolver table (home)', () => {
	for (const persona of PERSONAS) {
		const expectations = HOME_EXPECTATIONS[persona.key]!;
		const ctx = personaVariantContext(persona, 'home');
		for (const [type, expected] of Object.entries(expectations))
			it(`${persona.key} ${type} → ${expected}`, () => {
				expect(resolveVariant(type as Parameters<typeof resolveVariant>[0], undefined, ctx)).toBe(
					expected
				);
			});
	}
});

describe('portfolioHeader away from home', () => {
	for (const persona of PERSONAS)
		it(`${persona.key} on a non-home slug → compact`, () => {
			const ctx = personaVariantContext(persona, 'accounts');
			expect(resolveVariant('portfolioHeader', undefined, ctx)).toBe('compact');
		});
});
