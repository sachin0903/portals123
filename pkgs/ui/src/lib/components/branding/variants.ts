/**
 * Variant resolution — how a block decides which of its looks to render. Firms set 'auto' (the
 * default) and the portal picks a presentation from the client's own data, so the same layout
 * serves a two-account individual and a forty-account trust; an explicit variant pins the look.
 * Pure and client-side only: the Python side validates variant NAMES (block-contract.json) but
 * never resolves them.
 */
import type { BlockType } from './client-blocks.js';
import { CLIENT_BLOCK_BY_TYPE } from './client-blocks.js';

/** The per-request facts auto-resolution reads. Counts are the client's own (post-RLS) numbers. */
export interface VariantContext {
	audience: 'individual' | 'organisation';
	accountCount: number;
	/** Accounts with a live valuation — carried for renderers (sparklines, headline maths); the
	 * auto rules themselves key off the raw counts. */
	valuedAccountCount: number;
	holdingsCount: number;
	activityCount: number;
	documentsCount: number;
	pageSlug: string;
}

/** The auto rules' breakpoints — tunable in one place, pinned by variants.test.ts. */
export const VARIANT_THRESHOLDS = {
	accountsStackMax: 6,
	accountsCardsMax: 24,
	holdingsTableMin: 8,
	activityTableMin: 12,
	documentsGroupedMin: 8
} as const;

/**
 * The concrete variant a block should render.
 *
 * Contract (tested): for a type with MORE THAN ONE variant this always returns a concrete,
 * non-'auto' member of the type's allowlist; for a single-variant type it returns 'auto' and the
 * component renders its only (default) look. An explicit `settings.variant` wins only when it is a
 * real member of the type's allowlist — junk or 'auto' takes the auto path, so a stale or
 * hand-edited setting degrades to sensible rather than to a blank block.
 *
 * Auto rules:
 * - portfolioHeader: 'hero' on the home page, 'compact' anywhere else. The immersive 'canvas'
 *   and typographic 'ledger' looks are EXPLICIT choices only — auto never picks a statement
 *   look for a firm that didn't ask for one (junk still degrades through this auto path).
 * - accounts: 'stack' up to accountsStackMax, 'cards' up to accountsCardsMax, 'summary' above
 *   ('table' only when explicitly chosen — it is a power-user look, never a default).
 * - holdings: 'table' from holdingsTableMin holdings, or always for an organisation (trusts and
 *   companies read positions, not highlights); 'top' otherwise.
 * - activity: 'table' from activityTableMin entries, 'list' below.
 * - documents: 'grouped' from documentsGroupedMin documents, 'recent' below.
 * - allocation: 'donut' ('bars' only when explicitly chosen).
 */
export function resolveVariant(
	type: BlockType,
	settings: Record<string, unknown> | undefined,
	ctx: VariantContext
): string {
	const allowed = CLIENT_BLOCK_BY_TYPE[type]?.variants ?? ['auto'];
	if (allowed.length <= 1) return 'auto';
	const chosen = settings?.['variant'];
	if (typeof chosen === 'string' && chosen !== 'auto' && allowed.includes(chosen)) return chosen;
	switch (type) {
		case 'portfolioHeader':
			return ctx.pageSlug === 'home' ? 'hero' : 'compact';
		case 'accounts':
			if (ctx.accountCount <= VARIANT_THRESHOLDS.accountsStackMax) return 'stack';
			if (ctx.accountCount <= VARIANT_THRESHOLDS.accountsCardsMax) return 'cards';
			return 'summary';
		case 'holdings':
			return ctx.holdingsCount >= VARIANT_THRESHOLDS.holdingsTableMin ||
				ctx.audience === 'organisation'
				? 'table'
				: 'top';
		case 'activity':
			return ctx.activityCount >= VARIANT_THRESHOLDS.activityTableMin ? 'table' : 'list';
		case 'documents':
			return ctx.documentsCount >= VARIANT_THRESHOLDS.documentsGroupedMin ? 'grouped' : 'recent';
		case 'allocation':
			return 'donut';
		default:
			// A multi-variant type without an auto rule yet: its first concrete look.
			return allowed[1] ?? 'auto';
	}
}
