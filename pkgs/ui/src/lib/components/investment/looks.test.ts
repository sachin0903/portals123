import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { BlockType } from '../branding/client-blocks.js';
import { resolveLook, type Finish } from './looks.js';

/**
 * resolveLook is the one decision between a stored `look` setting + the portal finish and the
 * component a block renders, so it is tabled exhaustively: every type, every contract look,
 * every finish, plus the junk a wire setting can carry.
 */

// Read independently of the module under test (which imports the JSON), so a broken import or a
// stale literal fails here — the registry.test.ts idiom.
const contract = JSON.parse(
	readFileSync(new URL('../branding/block-contract.json', import.meta.url), 'utf8')
) as { blocks: Record<string, { looks?: string[] }> };

const FINISHES: Finish[] = ['card', 'ledger', null, undefined];

/** The chrome-less signature 'auto' resolves to under the ledger finish, per type. */
const LEDGER_SIGNATURE: Record<string, string> = {
	accounts: 'ink',
	allocation: 'orbit',
	holdings: 'sheet',
	activity: 'timeline',
	documents: 'index',
	adviser: 'strip',
	actionItems: 'card',
	message: 'note',
	onboardingProgress: 'banner',
	news: 'journal'
};

/** What 'auto' resolves to under the card finish (only onboardingProgress has no 'card' look). */
const CARD_SIGNATURE: Record<string, string> = { onboardingProgress: 'banner' };

const looksBearing = Object.entries(contract.blocks).filter(([, b]) => b.looks) as [
	BlockType,
	{ looks: string[] }
][];

describe('resolveLook', () => {
	it('the signature tables cover exactly the looks-bearing types (change together with the contract)', () => {
		expect(Object.keys(LEDGER_SIGNATURE).sort()).toEqual(looksBearing.map(([t]) => t).sort());
	});

	it('every ledger signature is a member of its type’s contract looks list', () => {
		for (const [type, { looks }] of looksBearing)
			expect(looks, type).toContain(LEDGER_SIGNATURE[type]);
	});

	for (const [type, { looks }] of looksBearing) {
		describe(type, () => {
			const auto = (finish: Finish) =>
				finish === 'ledger' ? LEDGER_SIGNATURE[type] : (CARD_SIGNATURE[type] ?? 'card');

			for (const finish of FINISHES) {
				const f = String(finish);

				it(`explicit valid looks win over finish ${f}`, () => {
					for (const look of looks.filter((l) => l !== 'auto'))
						expect(resolveLook(type, { look }, finish), look).toBe(look);
				});

				it(`'auto' under finish ${f} takes the auto path`, () => {
					expect(resolveLook(type, { look: 'auto' }, finish)).toBe(auto(finish));
				});

				it(`absent/junk settings under finish ${f} take the auto path`, () => {
					expect(resolveLook(type, undefined, finish)).toBe(auto(finish));
					expect(resolveLook(type, {}, finish)).toBe(auto(finish));
					// A look from another type's list, a stale name, a number, null — all junk.
					expect(resolveLook(type, { look: 'quantum' }, finish)).toBe(auto(finish));
					expect(resolveLook(type, { look: 42 }, finish)).toBe(auto(finish));
					expect(resolveLook(type, { look: null }, finish)).toBe(auto(finish));
				});
			}

			it('a look valid for ANOTHER type is junk here', () => {
				// 'orbit' belongs to allocation alone; every other type must refuse it.
				if (type !== 'allocation')
					expect(resolveLook(type, { look: 'orbit' }, 'ledger')).toBe(auto('ledger'));
			});
		});
	}

	it('accounts: the full truth table, spelt out', () => {
		expect(resolveLook('accounts', undefined, undefined)).toBe('card');
		expect(resolveLook('accounts', undefined, null)).toBe('card');
		expect(resolveLook('accounts', undefined, 'card')).toBe('card');
		expect(resolveLook('accounts', undefined, 'ledger')).toBe('ink');
		expect(resolveLook('accounts', { look: 'float' }, 'ledger')).toBe('float');
		expect(resolveLook('accounts', { look: 'card' }, 'ledger')).toBe('card');
		expect(resolveLook('accounts', { look: 'ink' }, null)).toBe('ink');
	});

	it('onboardingProgress: banner either way — its looks list has no card', () => {
		expect(resolveLook('onboardingProgress', undefined, null)).toBe('banner');
		expect(resolveLook('onboardingProgress', undefined, 'ledger')).toBe('banner');
		expect(resolveLook('onboardingProgress', { look: 'slim' }, null)).toBe('slim');
	});

	it('the type without a looks axis always resolves card — explicit settings included', () => {
		for (const finish of FINISHES) {
			expect(resolveLook('portfolioHeader', { look: 'ledger' }, finish)).toBe('card');
		}
	});

	it('an unknown type resolves card (portal-page renders its placeholder chrome)', () => {
		expect(resolveLook('quantumWidget' as BlockType, { look: 'ink' }, 'ledger')).toBe('card');
	});
});
