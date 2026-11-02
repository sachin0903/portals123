/**
 * Pure page CRUD — the operations the studio's page rail dispatches. Pages are addressed by `id`
 * (the stable handle a rename keeps; see app/branding.py PageDef).
 *
 * Every function enforces the SAME rules app/branding.py re-enforces on save (slug regex,
 * reserved slugs, label clamp, the structural caps), so the builder never produces a payload the
 * API would silently mangle — a firm must see the refusal in the UI, not discover a dropped page
 * after saving. Refusals return the input array unchanged (same reference), so callers can cheaply
 * detect "nothing happened"; only `setPageSlug` reports WHY, because a slug failure needs a
 * human-readable message rather than a silent no-op.
 */
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import { PAGE_CAPS, mintBlockId, slugify } from './portal-pages.js';
import { PORTAL_SLUG_RE, RESERVED_SLUGS } from './portal-slugs.js';

// Mirror of app/branding.py `_C0` — strip C0 control characters (plus DEL) from firm-authored
// text so stored copy can't carry terminal-escape or NUL junk. Not the XSS defence (escaping at
// render is); same story as the API side.
// eslint-disable-next-line no-control-regex
const C0 = /[\x00-\x1f\x7f]/g;

/** Mirror of app/branding.py PageDef `_label` (`_plain_text(v, 24)`): strip C0, cut to 24. */
const LABEL_LIMIT = 24;

/** Map one page through `patch`, returning the SAME array reference when the id is unknown. */
function updatePage(
	pages: BrandingPage[],
	id: string,
	patch: (page: BrandingPage) => BrandingPage
): BrandingPage[] {
	if (!pages.some((p) => p.id === id)) return pages;
	return pages.map((p) => (p.id === id ? patch(p) : p));
}

/**
 * Add a page named `label`: slug from slugify(label) deduped against existing slugs (suffix -2,
 * -3…, never a reserved slug — slugify already refuses those), id = the minted slug, icon
 * 'layout-grid', nav true unless the nav cap is full (then false), layouts.default = []. Returns
 * null when the page cap (PAGE_CAPS.maxPages) is reached.
 */
export function addPage(
	pages: BrandingPage[],
	label: string
): { pages: BrandingPage[]; id: string } | null {
	if (pages.length >= PAGE_CAPS.maxPages) return null;
	const base = slugify(label);
	// Dedupe against ids as well as slugs: setPageSlug changes a slug but keeps the id, so an old
	// id can equal a candidate slug — and the new page's id IS its slug, so id-uniqueness (what
	// updatePage keys on) needs the union.
	const taken = new Set(pages.flatMap((p) => [p.slug, p.id]));
	let slug = base;
	for (let n = 2; taken.has(slug) || RESERVED_SLUGS.has(slug); n += 1) {
		// Trim the stem so the suffixed slug stays inside the 32-char regex the API enforces —
		// a full-length base plus '-2' would otherwise be dropped on save as invalid.
		const suffix = `-${n}`;
		slug = base.slice(0, 32 - suffix.length) + suffix;
	}
	const clean = label.replace(C0, '').slice(0, LABEL_LIMIT);
	const page: BrandingPage = {
		id: slug,
		slug,
		label: clean,
		icon: 'layout-grid',
		// A new page joins the nav when there's room; past the cap it lands hidden rather than
		// silently pushing another page out of the tab bar.
		nav: pages.filter((p) => p.nav !== false).length < PAGE_CAPS.maxNavPages,
		layouts: { default: [] }
	};
	return { pages: [...pages, page], id: slug };
}

/**
 * Rename (label only; slug untouched — renames must not break bookmarks). Truncates to 24 chars
 * and strips control characters, mirroring the API clamp.
 */
export function renamePage(pages: BrandingPage[], id: string, label: string): BrandingPage[] {
	const clean = label.replace(C0, '').slice(0, LABEL_LIMIT);
	return updatePage(pages, id, (p) => ({ ...p, label: clean }));
}

/**
 * Change a page's slug. Errors (returns {error}) rather than silently fixing: 'invalid' when it
 * fails the slug regex, 'reserved' when in RESERVED_SLUGS, 'duplicate' when another page has it.
 * The UI surfaces the message; it never writes a bad slug.
 */
export function setPageSlug(
	pages: BrandingPage[],
	id: string,
	slug: string
): { pages: BrandingPage[] } | { error: 'invalid' | 'reserved' | 'duplicate' } {
	if (!PORTAL_SLUG_RE.test(slug)) return { error: 'invalid' };
	if (RESERVED_SLUGS.has(slug)) return { error: 'reserved' };
	if (pages.some((p) => p.id !== id && p.slug === slug)) return { error: 'duplicate' };
	// The id stays put on purpose — it is the stable handle (dnd keys, this module's addressing);
	// only the URL segment moves.
	return { pages: updatePage(pages, id, (p) => ({ ...p, slug })) };
}

export function setPageIcon(pages: BrandingPage[], id: string, icon: string): BrandingPage[] {
	return updatePage(pages, id, (p) => ({ ...p, icon }));
}

/**
 * Toggle nav. Enforces ≤ PAGE_CAPS.maxNavPages shown (returns pages unchanged when turning ON
 * would exceed it) and ≥1 shown (returns unchanged when turning OFF the last nav page).
 */
export function setPageNav(pages: BrandingPage[], id: string, nav: boolean): BrandingPage[] {
	const target = pages.find((p) => p.id === id);
	if (!target) return pages;
	if ((target.nav !== false) === nav) return pages; // already in the requested state
	// Count the OTHER shown pages, so the check reads the same whichever way we're toggling.
	const shownOthers = pages.filter((p) => p.id !== id && p.nav !== false).length;
	if (nav && shownOthers >= PAGE_CAPS.maxNavPages) return pages;
	if (!nav && shownOthers === 0) return pages; // a portal with zero tabs is unnavigable
	return pages.map((p) => (p.id === id ? { ...p, nav } : p));
}

/** Remove a page. Refuses (returns unchanged) to remove the last remaining page. */
export function removePage(pages: BrandingPage[], id: string): BrandingPage[] {
	if (pages.length <= 1) return pages;
	const next = pages.filter((p) => p.id !== id);
	return next.length === pages.length ? pages : next;
}

/** Reorder to the given id order; ids must be a permutation of the current ids (else unchanged). */
export function reorderPages(pages: BrandingPage[], ids: string[]): BrandingPage[] {
	// A dnd library mid-drag can hand back a stale or partial id list; reordering to it would drop
	// pages, so anything short of an exact permutation is refused.
	if (ids.length !== pages.length) return pages;
	const byId = new Map(pages.map((p) => [p.id, p]));
	if (new Set(ids).size !== ids.length || ids.some((id) => !byId.has(id))) return pages;
	return ids.map((id) => byId.get(id)!);
}

/**
 * Duplicate the block with this id within a layout: deep-copied settings, a fresh id minted with
 * mintBlockId against the layout's taken ids, inserted immediately after the original. Returns
 * unchanged when the id is unknown or the per-page block cap is reached.
 */
export function duplicateBlock(layout: BrandingBlock[], blockId: string): BrandingBlock[] {
	if (layout.length >= PAGE_CAPS.maxBlocksPerPage) return layout;
	const at = layout.findIndex((b) => b.id === blockId);
	if (at === -1) return layout;
	const original = layout[at]!;
	const taken = new Set(layout.map((b) => b.id).filter((id): id is string => id !== undefined));
	const copy: BrandingBlock = {
		...original,
		id: mintBlockId(original.type, taken),
		// Deep-copy settings so editing the duplicate's variant/limits never bleeds back into the
		// original (spread alone would share the settings object). CALLER BEWARE: structuredClone
		// throws DataCloneError on a Svelte $state proxy — pass plain data ($state.snapshot at the
		// component boundary), as branding-editor does.
		...(original.settings !== undefined ? { settings: structuredClone(original.settings) } : {})
	};
	return [...layout.slice(0, at + 1), copy, ...layout.slice(at + 1)];
}
