/**
 * Per-audience copy-on-write over a page's layouts — the pure state transitions behind the
 * studio's Default | Individuals | Trusts & companies tabs. An audience override starts life as a
 * COPY of the default layout (so the firm edits a familiar page, not a blank one) and can be
 * reverted back to following the default at any time.
 *
 * Everything here is pure: inputs are never mutated, returns are fresh objects (structural sharing
 * where a branch is unchanged is fine, and `customise` deliberately returns the SAME page reference
 * when there is nothing to do, so a Svelte store fed through it doesn't invalidate).
 *
 * The empty-array rule mirrors `resolveAudienceLayout` (portal-pages.ts) and the wire contract
 * (@graphene/auth BrandingPageLayouts): an EMPTY ARRAY override is an explicit "show this audience
 * nothing" and counts as customised; only null/absent falls back to the default.
 */
import type { BrandingBlock, BrandingPage } from '@graphene/auth';

export type Audience = 'individual' | 'organisation';
export type AudienceTab = 'default' | Audience;

/**
 * The layout the builder edits for a tab: the override when present (null counts as ABSENT here —
 * an explicit-null wire value reads as default), else the default.
 */
export function layoutFor(page: BrandingPage, tab: AudienceTab): BrandingBlock[] {
	if (tab === 'default') return page.layouts.default;
	return page.layouts[tab] ?? page.layouts.default;
}

/**
 * Whether the audience carries its own override (drives the "Customised" badge). An EMPTY ARRAY
 * override counts as customised (a firm hiding a page's content from trusts made a decision);
 * null/undefined do not.
 */
export function isCustomised(page: BrandingPage, audience: Audience): boolean {
	// `!= null` (not `!== undefined`): a wire payload may carry an explicit null, which means
	// "follow default" just like absence does.
	return page.layouts[audience] != null;
}

/**
 * Copy-on-write: give the audience its own DEEP copy of the default layout. Fresh block ids are
 * deliberately NOT minted — ids stay stable across the copy so the builder's dnd keys don't jump
 * when a firm flips a tab from "following default" to "customised". No-op (returns the page
 * unchanged, same reference) when already customised.
 */
export function customise(page: BrandingPage, audience: Audience): BrandingPage {
	if (isCustomised(page, audience)) return page;
	// structuredClone reaches into each block's `settings` too — a shallow copy would leave the
	// override's settings objects shared with default, and editing one tab would silently edit both.
	// CALLER BEWARE: structuredClone throws DataCloneError on a Svelte $state proxy — and an
	// uncaught throw mid-flush leaves half-updated UI with no console error. Pass plain data
	// ($state.snapshot at the component boundary), as branding-editor does.
	return {
		...page,
		layouts: { ...page.layouts, [audience]: structuredClone(page.layouts.default) }
	};
}

/** Drop the audience's override entirely (back to following default). */
export function revert(page: BrandingPage, audience: Audience): BrandingPage {
	// Remove the KEY, not write null: absent and explicit-null read the same, but the stored shape
	// should not accumulate tombstones a later diff or hand-reader has to reason about.
	if (!(audience in page.layouts)) return page;
	const layouts = { ...page.layouts };
	delete layouts[audience];
	return { ...page, layouts };
}

/** Write a layout into the tab's slot ('default' writes layouts.default). */
export function setLayout(
	page: BrandingPage,
	tab: AudienceTab,
	layout: BrandingBlock[]
): BrandingPage {
	return { ...page, layouts: { ...page.layouts, [tab]: layout } };
}
