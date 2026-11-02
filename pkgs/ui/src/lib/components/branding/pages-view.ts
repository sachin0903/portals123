/**
 * Pages-view logic — the pure, testable half of the branding studio's Pages view. The Svelte
 * components (branding-editor / pages-rail / block-inspector / layout-builder) stay thin over
 * these helpers so the behaviour that matters — the materialise-once guard, the builder's
 * row↔block round-trip, the inspector's descriptor→value mapping and the nav-cap refusals —
 * is pinned by pages-view.test.ts without mounting a component.
 */
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import {
	blockKeys,
	blockW,
	blockH,
	CLIENT_BLOCK_BY_TYPE,
	type BlockType,
	type SettingDescriptor
} from './client-blocks.js';
import { defaultPages, PAGE_CAPS } from './portal-pages.js';
import { resolveVariant, type VariantContext } from './variants.js';
import type { Persona } from '../../fixtures/personas.js';

// ── The working page set ───────────────────────────────────────────────────

/**
 * The studio's working page set, materialised ONCE when the Pages view opens. The draft's
 * `pages` stays null until the firm actually edits — saving an untouched studio must keep
 * `pages` null so a tenant that never customised keeps tracking the platform defaults
 * (stamping the defaults in would freeze the portal at today's shape). Stored pages are
 * deep-cloned so pre-commit list operations never alias the draft's own arrays.
 */
export function materialiseWorkingPages(pages: BrandingPage[] | null | undefined): BrandingPage[] {
	return pages && pages.length > 0 ? (structuredClone(pages) as BrandingPage[]) : defaultPages();
}

// ── Builder rows (extracted from layout-builder so the emit contract is unit-tested) ─────────

/** One builder tile. Mirrors BrandingBlock 1:1 plus a guaranteed id (dnd needs a stable key). */
export interface LayoutRow {
	id: string;
	type: string;
	visible: boolean;
	w: number;
	h: number;
	/** Per-instance configuration. The builder never edits it (the inspector does) but it MUST
	 * ride through every emit: the first version mapped rows to `{type, visible, w, h}` only,
	 * so any drag or toggle silently erased every block's settings. */
	settings: Record<string, unknown> | undefined;
}

/**
 * Blocks → builder rows. Ids come from the block when present; legacy layouts (pre per-instance
 * ids) get minted ones. Minting must respect ids already taken, or a legacy layout with a
 * duplicate would collapse two rows into one dnd item.
 */
export function layoutToRows(l: BrandingBlock[]): LayoutRow[] {
	// blockKeys, not a local mint: the renderer derives each cell's identity the same way, and a
	// measured height is attributed by that identity — the two must be the same function.
	const ids = blockKeys(l);
	return l.map((b, i) => {
		return {
			id: ids[i] ?? b.type,
			type: b.type,
			visible: b.visible !== false,
			w: blockW(b),
			h: blockH(b),
			settings: b.settings
		};
	});
}

/** Builder rows → the emitted layout. `settings` is omitted (not `undefined`) when unset so the
 * wire shape matches what the API stores — a round-trip must be byte-stable. */
export function rowsToLayout(rows: LayoutRow[]): BrandingBlock[] {
	return rows.map((r) => ({
		id: r.id,
		type: r.type,
		visible: r.visible,
		w: r.w,
		h: r.h,
		...(r.settings !== undefined ? { settings: r.settings } : {})
	}));
}

/** A cheap change signature so the builder can tell a genuinely new parent value from its own
 * emit echoed back (which must not clobber an in-flight interaction). Settings are part of it:
 * the inspector edits them on the parent's copy, and without them a settings edit would never
 * re-seed the tiles. */
export function rowSignature(rows: LayoutRow[]): string {
	return rows
		.map((b) => `${b.id}:${b.type}:${b.visible}:${b.w}:${b.h}:${JSON.stringify(b.settings ?? {})}`)
		.join('|');
}

// ── Inspector: descriptor → control value, and back ──────────────────────────────────────────

/**
 * What a control shows for a descriptor. Fallbacks are deliberate, not incidental:
 * - segmented/select: an unset or off-allowlist value shows the FIRST option ('auto' leads every
 *   variant list), mirroring resolveVariant's junk-degrades-to-auto rule.
 * - switch: absent ⇒ the descriptor's `defaultOn` (true when omitted) — the control must agree
 *   with the widget's read or it would lie about the render. The historic switches read
 *   `!== false` (absent ⇒ on); the listing toolbars read `=== true` and declare
 *   `defaultOn: false`, so a fresh block shows its toolbar switch OFF, matching the page.
 * - number: absent ⇒ '' (an empty input reads as "use the default", never a fake 0).
 */
export function inspectorValue(
	d: SettingDescriptor,
	settings: Record<string, unknown> | undefined
): string | number | boolean {
	const stored = settings?.[d.key];
	switch (d.control) {
		case 'segmented':
		case 'select': {
			const first = d.options?.[0]?.value ?? '';
			return typeof stored === 'string' && d.options?.some((o) => o.value === stored)
				? stored
				: first;
		}
		case 'switch':
			// A stored real boolean stands; anything else (absent, junk) shows the default —
			// which is also what the widgets render for it (`!== false` reads treat junk as on,
			// `=== true` reads treat junk as off; defaultOn mirrors each read).
			return typeof stored === 'boolean' ? stored : (d.defaultOn ?? true);
		case 'number':
			return typeof stored === 'number' && Number.isFinite(stored) ? stored : '';
		default:
			return typeof stored === 'string' ? stored : '';
	}
}

/**
 * The next settings object after one edit — always a fresh object (the block is replaced
 * wholesale downstream, so mutation would bypass the editor's change tracking). `undefined`
 * and '' CLEAR the key: unsetting returns the block to its default/auto behaviour instead of
 * pinning an empty value on the wire forever.
 */
export function withSetting(
	settings: Record<string, unknown> | undefined,
	key: string,
	value: unknown
): Record<string, unknown> {
	const next = { ...settings };
	if (value === undefined || value === '') delete next[key];
	else next[key] = value;
	return next;
}

/** A number control's raw input → the stored value: blank/junk clears the key; anything else is
 * rounded and clamped to the descriptor's bounds (they are row limits — integers by nature, and
 * the API clamps the same way, so the inspector never shows a value the save would change). */
export function parseNumberSetting(raw: string, d: SettingDescriptor): number | undefined {
	const n = Number(raw);
	if (raw.trim() === '' || !Number.isFinite(n)) return undefined;
	const rounded = Math.round(n);
	const lo = d.min ?? Number.MIN_SAFE_INTEGER;
	const hi = d.max ?? Number.MAX_SAFE_INTEGER;
	return Math.max(lo, Math.min(hi, rounded));
}

// ── Variant resolution contexts (the preview + the inspector's auto readout) ─────────────────

/** The VariantContext a persona implies on a given page: counts from the persona's headline
 * numbers, slice sizes from its actual data, audience from the persona unless the caller pins
 * one (the editor previews the audience TAB being edited, whatever the persona's own is). */
export function personaVariantContext(
	persona: Persona,
	pageSlug: string,
	audience: VariantContext['audience'] = persona.audience
): VariantContext {
	return {
		audience,
		accountCount: persona.counts.accountCount,
		valuedAccountCount: persona.counts.valuedAccountCount,
		holdingsCount: persona.data.holdings?.length ?? 0,
		activityCount: persona.data.activity?.length ?? 0,
		documentsCount: persona.data.documents?.length ?? 0,
		pageSlug
	};
}

/** The fallback context when no persona is supplied (a pages-aware preview outside the studio):
 * counts derived from the shared demo fixture the preview renders anyway, so what the resolver
 * decides always matches what the frame shows. Typed structurally on just the slices it counts
 * (clientDemo is richer than ClientHomeData — its allocations are a per-wrapper Record). */
export function demoVariantContext(
	demo: {
		wrappers: readonly unknown[];
		holdings: readonly unknown[];
		activity: readonly unknown[];
		documents?: readonly unknown[];
	},
	pageSlug: string,
	audience: VariantContext['audience'] = 'individual'
): VariantContext {
	return {
		audience,
		accountCount: demo.wrappers.length,
		valuedAccountCount: demo.wrappers.length,
		holdingsCount: demo.holdings.length,
		activityCount: demo.activity.length,
		documentsCount: demo.documents?.length ?? 0,
		pageSlug
	};
}

/**
 * Audience-resolved blocks → the concrete-variant blocks PortalPage expects. The renderer treats
 * `settings.variant` as already resolved (portal-page.svelte), so 'auto' must be collapsed here.
 * Unknown types pass through untouched — PortalPage shows its own placeholder for them.
 */
export function resolveConcreteBlocks(
	blocks: BrandingBlock[],
	ctx: VariantContext
): BrandingBlock[] {
	return blocks.map((b) => {
		if (!CLIENT_BLOCK_BY_TYPE[b.type]) return b;
		const variant = resolveVariant(b.type as BlockType, b.settings, ctx);
		return { ...b, settings: { ...b.settings, variant } };
	});
}

// ── Rail copy: nav-cap refusals + slug validation messages ───────────────────────────────────

/**
 * Why a page's nav toggle is disabled right now — or null when it may flip. Mirrors the
 * refusals inside page-ops' setPageNav (≤ maxNavPages shown, ≥ 1 shown) so the switch is
 * disabled BEFORE the op would silently refuse, with the reason on screen.
 */
export function navToggleBlock(pages: BrandingPage[], id: string): string | null {
	const page = pages.find((p) => p.id === id);
	if (!page) return null;
	const shown = pages.filter((p) => p.nav !== false).length;
	if (page.nav !== false && shown <= 1) return 'At least one page must stay in the menu.';
	if (page.nav === false && shown >= PAGE_CAPS.maxNavPages)
		return `The menu shows at most ${PAGE_CAPS.maxNavPages} pages — hide another page first.`;
	return null;
}

/** Human copy for setPageSlug's refusal codes. */
export function slugErrorMessage(error: 'invalid' | 'reserved' | 'duplicate'): string {
	switch (error) {
		case 'reserved':
			return 'That address is used by the portal itself.';
		case 'duplicate':
			return 'Another page already uses that address.';
		default:
			return 'Addresses use lowercase letters, numbers and hyphens (up to 32 characters).';
	}
}
