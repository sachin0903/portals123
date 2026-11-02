/**
 * Per-widget LOOK resolution — pure, table-tested. A block's `look` setting is its VISUAL design
 * (chrome or no chrome), orthogonal to its `variant` (the DATA shape: stack vs table, donut vs
 * bars). The allowlists live in block-contract.json (`looks` per type — the same file
 * app/branding.py validates against), so a stored look the contract doesn't know cannot select a
 * component here.
 *
 * 'auto' follows the portal finish axis (`Branding.finish`): the ledger finish resolves each type
 * to its chrome-less signature design (the register portfolio-header-ledger.svelte set — hairline
 * rules, small-caps eyebrows, display-face numerals, no card), while the card finish (or no
 * finish) keeps today's carded rendering. An explicit, contract-valid look always wins over the
 * finish — a firm can put one ledger-style block on a carded portal and vice versa.
 */
import contract from '../branding/block-contract.json';
import type { BlockType } from '../branding/client-blocks.js';

/** The portal-wide finish axis as the renderer receives it (Branding.finish; absent ⇒ card). */
export type Finish = 'card' | 'ledger' | null | undefined;

// The contract JSON's inferred type is per-key structural (only some blocks carry `looks`), so
// indexing by a union key needs the uniform shape spelt out. registry.test.ts pins the real file.
const BLOCKS = contract.blocks as Record<
	string,
	{ variants: string[]; looks?: string[]; settings: string[] }
>;

/**
 * Each type's chrome-less signature — what 'auto' resolves to under the ledger finish. Not every
 * type goes chrome-less: actionItems stays 'card' (its alert card already re-skins through the
 * finish tokens and a task list needs its edge), and onboardingProgress's canonical design is the
 * 'banner' either way.
 */
const LEDGER_AUTO: Partial<Record<BlockType, string>> = {
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

/** What 'auto' resolves to under the card finish. Only onboardingProgress deviates from 'card':
 * its looks list carries no 'card' at all — the banner IS its carded design. */
const CARD_AUTO: Partial<Record<BlockType, string>> = {
	onboardingProgress: 'banner'
};

/**
 * The concrete look a block renders. An explicit setting that names a member of the type's
 * contract looks list wins; 'auto' and junk (a stale value, a number, a look from another type)
 * take the auto path — ledger finish ⇒ the type's chrome-less signature, anything else ⇒ the
 * carded design. The one type without a looks list (portfolioHeader — its variants ARE its
 * looks) and unknown types return 'card', which portal-page renders as today's chrome.
 */
export function resolveLook(
	type: BlockType,
	settings: Record<string, unknown> | undefined,
	finish: Finish
): string {
	const looks = BLOCKS[type]?.looks;
	if (!looks) return 'card';
	const raw = settings?.['look'];
	if (typeof raw === 'string' && raw !== 'auto' && looks.includes(raw)) return raw;
	if (finish === 'ledger') return LEDGER_AUTO[type] ?? 'card';
	return CARD_AUTO[type] ?? 'card';
}
