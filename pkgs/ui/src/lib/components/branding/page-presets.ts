/**
 * Curated page-set presets — a range of sensible whole-portal defaults the studio offers as
 * starting points. Each preset is a complete `BrandingPage[]` built PROGRAMMATICALLY from
 * `defaultPages()` (portal-pages.ts) so a preset can never drift from the base page set: when the
 * defaults gain a block or a page, every preset inherits it and only the curated tweaks differ.
 * A tweak reaches every audience layout the base set carries, not just the default.
 *
 * Every tweak is guarded against ./block-contract.json (the shared Python/TS contract): a
 * `variant`/`look` is applied only when the type's contract list carries it, and any other
 * settings key only when the type's settings list does — so a preset built against a newer or
 * older contract silently degrades to the base block rather than writing a value the API would
 * clamp away (the same degrade-never-reject posture as app/branding.py). page-presets.test.ts
 * table-tests each preset through the registry rules: slugs, caps, types, variants, looks.
 *
 * Applying a preset replaces the studio's working page set wholesale (behind a ConfirmDialog —
 * see branding-editor.svelte); the FINISH/density/font axes stay theme choices on the Design view,
 * which is why the Private bank preset's description points at the Ledger finish rather than
 * setting it.
 */
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import contract from './block-contract.json';
import { defaultPages } from './portal-pages.js';

export interface PagePreset {
	key: 'classic' | 'privateBank' | 'familyOffice' | 'professional';
	label: string;
	/** One-line pitch shown under the label in the studio's preset picker. */
	description: string;
	/** A complete, contract-valid page set. Deep-clone before handing to mutable editor state. */
	pages: BrandingPage[];
}

// The contract JSON's inferred type is per-key structural (only some blocks carry `looks`), so
// the guards read through this uniform view; registry.test.ts pins it to the real file.
const CONTRACT_BLOCKS = contract.blocks as Record<
	string,
	{ variants: string[]; looks?: string[]; settings: string[] }
>;

/** A curated tweak to every block of one type on one page. `w`/`h` re-span; `settings` merge. */
interface BlockPatch {
	w?: number;
	h?: number;
	settings?: Record<string, unknown>;
}

/**
 * Merge a patch's settings into a block, key by key, under the contract's allowlists: a key the
 * type's contract settings don't carry is dropped (feature detection — a sibling contract change
 * lands whenever it lands), and a `variant`/`look` value outside the type's list is dropped too
 * (it would only degrade to 'auto' on save). Returns the block unchanged (same reference) when
 * nothing survives, so an all-dropped patch doesn't stamp an empty `settings` onto the wire.
 */
function patchBlock(block: BrandingBlock, patch: BlockPatch): BrandingBlock {
	const meta = CONTRACT_BLOCKS[block.type];
	const settings: Record<string, unknown> = { ...block.settings };
	let changed = false;
	for (const [key, value] of Object.entries(patch.settings ?? {})) {
		if (!meta?.settings.includes(key)) continue;
		if (key === 'variant' && !meta.variants.includes(String(value))) continue;
		if (key === 'look' && !(meta.looks ?? []).includes(String(value))) continue;
		settings[key] = value;
		changed = true;
	}
	if (patch.w === undefined && patch.h === undefined && !changed) return block;
	return {
		...block,
		...(patch.w !== undefined ? { w: patch.w } : {}),
		...(patch.h !== undefined ? { h: patch.h } : {}),
		...(changed || block.settings !== undefined ? { settings } : {})
	};
}

/** Map EVERY audience layout of `slug` through `fn`, not the default alone: `defaultPages()` ships
 *  organisation overrides, and reshaping only the default would leave a firm's trusts and companies
 *  behind, in a split it cannot see in the picker. A null override still follows the default. */
function onPage(
	pages: BrandingPage[],
	slug: string,
	fn: (blocks: BrandingBlock[]) => BrandingBlock[]
): BrandingPage[] {
	return pages.map((p) => {
		if (p.slug !== slug) return p;
		const layouts = { ...p.layouts, default: fn(p.layouts.default) };
		for (const audience of ['individual', 'organisation'] as const) {
			const override = p.layouts[audience];
			if (override) layouts[audience] = fn(override);
		}
		return { ...p, layouts };
	});
}

/** Apply a patch to every block of `type` on the page with `slug`. */
function tune(
	pages: BrandingPage[],
	slug: string,
	type: string,
	patch: BlockPatch
): BrandingPage[] {
	return onPage(pages, slug, (blocks) =>
		blocks.map((b) => (b.type === type ? patchBlock(b, patch) : b))
	);
}

/** Remove every block of `type` from the page with `slug`. */
function drop(pages: BrandingPage[], slug: string, type: string): BrandingPage[] {
	return onPage(pages, slug, (blocks) => blocks.filter((b) => b.type !== type));
}

/** Move the first block of `type` immediately before the first block of `beforeType`. */
function moveBefore(
	pages: BrandingPage[],
	slug: string,
	type: string,
	beforeType: string
): BrandingPage[] {
	return onPage(pages, slug, (blocks) => {
		const from = blocks.findIndex((b) => b.type === type);
		if (from === -1 || !blocks.some((b) => b.type === beforeType)) return blocks;
		const next = [...blocks];
		const [moved] = next.splice(from, 1);
		next.splice(
			next.findIndex((b) => b.type === beforeType),
			0,
			moved!
		);
		return next;
	});
}

/** Today's portal, untouched — the escape hatch back to the platform defaults. */
function classicPages(): BrandingPage[] {
	return defaultPages();
}

/**
 * The engraved register: the ledger statement header, each listing page pinned to its
 * chrome-less signature look, the adviser as a portrait, and no news. The looks are pinned
 * EXPLICITLY (not left to 'auto') so the composition holds under either finish — but the finish
 * stays a Design-view choice, hence the description's pointer.
 */
function privateBankPages(): BrandingPage[] {
	let pages = defaultPages();
	pages = drop(pages, 'home', 'news');
	pages = tune(pages, 'home', 'portfolioHeader', { settings: { variant: 'ledger' } });
	pages = tune(pages, 'home', 'adviser', { settings: { look: 'portrait' } });
	pages = tune(pages, 'portfolio', 'accounts', { settings: { look: 'ink' } });
	pages = tune(pages, 'portfolio', 'holdings', { settings: { look: 'sheet' } });
	pages = tune(pages, 'activity', 'activity', { settings: { look: 'timeline' } });
	pages = tune(pages, 'documents', 'documents', { settings: { look: 'index' } });
	return pages;
}

/**
 * Household-first: the home page leads with the family's accounts — full-width cards grouped by
 * owner — ahead of a compact header; the accounts page groups by owner too, and the documents
 * page surfaces outstanding requests when the contract carries that switch (feature-detected).
 */
function familyOfficePages(): BrandingPage[] {
	let pages = defaultPages();
	pages = moveBefore(pages, 'home', 'accounts', 'portfolioHeader');
	pages = tune(pages, 'home', 'accounts', {
		w: 12,
		settings: { variant: 'cards', group_by: 'owner' }
	});
	pages = tune(pages, 'home', 'portfolioHeader', { settings: { variant: 'compact' } });
	pages = tune(pages, 'portfolio', 'accounts', { settings: { group_by: 'owner' } });
	pages = tune(pages, 'documents', 'documents', { settings: { show_requests: true } });
	return pages;
}

/**
 * Data-forward for trusts and companies: a compact header over the summary tiles and the
 * spectrum allocation, position/movement TABLES everywhere (with toolbars where the contract
 * carries them — feature-detected), and the adviser as a slim strip.
 */
function professionalPages(): BrandingPage[] {
	let pages = defaultPages();
	pages = tune(pages, 'home', 'portfolioHeader', { settings: { variant: 'compact' } });
	pages = tune(pages, 'home', 'accounts', { settings: { variant: 'summary' } });
	pages = tune(pages, 'home', 'allocation', { settings: { look: 'spectrum' } });
	pages = tune(pages, 'home', 'adviser', { settings: { look: 'strip' } });
	pages = tune(pages, 'home', 'holdings', { settings: { variant: 'table' } });
	pages = tune(pages, 'home', 'activity', { settings: { variant: 'table' } });
	pages = tune(pages, 'portfolio', 'accounts', { settings: { variant: 'table' } });
	// The defaults are the grouped book and the home-style list — the variant has to be SET here,
	// a toolbar alone would leave the list in place.
	pages = tune(pages, 'portfolio', 'holdings', { settings: { variant: 'table', toolbar: true } });
	pages = tune(pages, 'activity', 'activity', { settings: { variant: 'table', toolbar: true } });
	return pages;
}

/** The curated presets, in the studio picker's order. */
export const PAGE_PRESETS: PagePreset[] = [
	{
		key: 'classic',
		label: 'Classic',
		description: 'The standard home dashboard and four listing pages.',
		pages: classicPages()
	},
	{
		key: 'privateBank',
		label: 'Private bank',
		description:
			'Statement-style pages with an open layout. Pair with the Ledger finish and a display font.',
		pages: privateBankPages()
	},
	{
		key: 'familyOffice',
		label: 'Family office',
		description: 'Family accounts lead the home page, grouped by owner.',
		pages: familyOfficePages()
	},
	{
		key: 'professional',
		label: 'Trust & company',
		description: 'A compact header, summary tiles and sortable tables.',
		pages: professionalPages()
	}
];

/** Look a preset up by key (undefined for junk — the picker only offers real keys). */
export function presetByKey(key: string): PagePreset | undefined {
	return PAGE_PRESETS.find((p) => p.key === key);
}
