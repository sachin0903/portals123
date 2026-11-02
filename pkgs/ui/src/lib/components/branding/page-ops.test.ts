import { describe, expect, it } from 'vitest';
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import { PAGE_CAPS } from './portal-pages.js';
import {
	addPage,
	duplicateBlock,
	removePage,
	renamePage,
	reorderPages,
	setPageIcon,
	setPageNav,
	setPageSlug
} from './page-ops.js';

function page(slug: string, overrides: Partial<BrandingPage> = {}): BrandingPage {
	return {
		id: slug,
		slug,
		label: slug,
		icon: 'layout-grid',
		nav: true,
		layouts: { default: [] },
		...overrides
	};
}

describe('addPage', () => {
	it('mints slug/id from the label, icon layout-grid, nav on, empty default layout', () => {
		const result = addPage([page('home')], 'Fee Schedule')!;
		expect(result.id).toBe('fee-schedule');
		const added = result.pages.at(-1)!;
		expect(added).toEqual({
			id: 'fee-schedule',
			slug: 'fee-schedule',
			label: 'Fee Schedule',
			icon: 'layout-grid',
			nav: true,
			layouts: { default: [] }
		});
		expect(result.pages).toHaveLength(2);
	});

	it("dedupes past taken slugs with '-2', '-3', …", () => {
		const one = addPage([page('home'), page('fees')], 'Fees')!;
		expect(one.id).toBe('fees-2');
		const two = addPage(one.pages, 'Fees')!;
		expect(two.id).toBe('fees-3');
	});

	it('a reserved label arrives pre-suffixed by slugify', () => {
		expect(addPage([page('home')], 'Onboarding')!.id).toBe('onboarding-2');
	});

	it('keeps a deduped slug inside the 32-char regex the API enforces', () => {
		const base = 'x'.repeat(32);
		const result = addPage([page('home'), page(base)], 'x'.repeat(40))!;
		expect(result.id).toMatch(/^[a-z0-9-]{1,32}$/);
		expect(result.id).toBe(`${'x'.repeat(30)}-2`);
	});

	it('clamps the label like the API (24 chars, control characters stripped)', () => {
		const result = addPage([page('home')], 'A\x00 Very Long Custom Page Name Indeed')!;
		expect(result.pages.at(-1)!.label).toBe('A Very Long Custom Page ');
	});

	it('nav flips off when the nav cap is already full', () => {
		const full = Array.from({ length: PAGE_CAPS.maxNavPages }, (_, i) => page(`p${i}`));
		expect(addPage(full, 'Extra')!.pages.at(-1)!.nav).toBe(false);
		// Hidden pages don't count towards the cap.
		const withHidden = [...full.slice(0, -1), page('hidden', { nav: false })];
		expect(addPage(withHidden, 'Extra')!.pages.at(-1)!.nav).toBe(true);
	});

	it('returns null at the page cap', () => {
		const full = Array.from({ length: PAGE_CAPS.maxPages }, (_, i) => page(`p${i}`));
		expect(addPage(full, 'One More')).toBeNull();
	});

	it('never mutates the input array', () => {
		const pages = [page('home')];
		addPage(pages, 'Fees');
		expect(pages).toHaveLength(1);
	});
});

describe('renamePage', () => {
	it('renames the label and leaves the slug alone (bookmarks survive)', () => {
		const next = renamePage([page('fees')], 'fees', 'Our Fees');
		expect(next[0]!.label).toBe('Our Fees');
		expect(next[0]!.slug).toBe('fees');
	});

	it('truncates to 24 chars and strips control characters (the API clamp, mirrored)', () => {
		const next = renamePage([page('fees')], 'fees', 'Fee\x1b[31m Schedule and Charges Overview');
		expect(next[0]!.label).toBe('Fee[31m Schedule and Cha');
		expect(next[0]!.label.length).toBeLessThanOrEqual(24);
	});

	it('unknown id returns the same array reference', () => {
		const pages = [page('fees')];
		expect(renamePage(pages, 'nope', 'X')).toBe(pages);
	});
});

describe('setPageSlug', () => {
	const pages = [page('home'), page('fees')];

	it('writes a valid, unique slug and keeps the id stable', () => {
		const result = setPageSlug(pages, 'fees', 'our-charges');
		expect(result).not.toHaveProperty('error');
		const next = (result as { pages: BrandingPage[] }).pages;
		expect(next[1]!.slug).toBe('our-charges');
		expect(next[1]!.id).toBe('fees');
	});

	it("refuses a regex failure with 'invalid' (never silently fixes)", () => {
		for (const bad of ['Fees', 'fee schedule', 'fée', '', 'x'.repeat(33)])
			expect(setPageSlug(pages, 'fees', bad)).toEqual({ error: 'invalid' });
	});

	it("refuses a reserved slug with 'reserved'", () => {
		expect(setPageSlug(pages, 'fees', 'login')).toEqual({ error: 'reserved' });
		expect(setPageSlug(pages, 'fees', 'api')).toEqual({ error: 'reserved' });
	});

	it("refuses another page's slug with 'duplicate'", () => {
		expect(setPageSlug(pages, 'fees', 'home')).toEqual({ error: 'duplicate' });
	});

	it("a page's own current slug is not a duplicate", () => {
		expect(setPageSlug(pages, 'fees', 'fees')).not.toHaveProperty('error');
	});
});

describe('setPageIcon', () => {
	it('sets the icon by id', () => {
		expect(setPageIcon([page('fees')], 'fees', 'receipt')[0]!.icon).toBe('receipt');
	});
});

describe('setPageNav', () => {
	it('toggles nav both ways', () => {
		const pages = [page('home'), page('fees')];
		expect(setPageNav(pages, 'fees', false)[1]!.nav).toBe(false);
		const hidden = [page('home'), page('fees', { nav: false })];
		expect(setPageNav(hidden, 'fees', true)[1]!.nav).toBe(true);
	});

	it('refuses to turn ON past the nav cap (returns the same reference)', () => {
		const pages = [
			...Array.from({ length: PAGE_CAPS.maxNavPages }, (_, i) => page(`p${i}`)),
			page('hidden', { nav: false })
		];
		expect(setPageNav(pages, 'hidden', true)).toBe(pages);
	});

	it('refuses to turn OFF the last shown page (returns the same reference)', () => {
		const pages = [page('home'), page('hidden', { nav: false })];
		expect(setPageNav(pages, 'home', false)).toBe(pages);
	});

	it('an absent nav flag counts as shown (matches derivePortalNav)', () => {
		const p = page('home');
		delete (p as { nav?: boolean }).nav;
		const pages = [p, page('hidden', { nav: false })];
		// home is the only SHOWN page even though its flag is absent — still refused.
		expect(setPageNav(pages, 'home', false)).toBe(pages);
	});
});

describe('removePage', () => {
	it('removes by id', () => {
		const next = removePage([page('home'), page('fees')], 'fees');
		expect(next.map((p) => p.id)).toEqual(['home']);
	});

	it('refuses to remove the last remaining page (same reference)', () => {
		const pages = [page('home')];
		expect(removePage(pages, 'home')).toBe(pages);
	});

	it('unknown id returns the same reference', () => {
		const pages = [page('home'), page('fees')];
		expect(removePage(pages, 'nope')).toBe(pages);
	});
});

describe('reorderPages', () => {
	const pages = [page('a'), page('b'), page('c')];

	it('reorders to the given id order', () => {
		expect(reorderPages(pages, ['c', 'a', 'b']).map((p) => p.id)).toEqual(['c', 'a', 'b']);
	});

	it('refuses anything short of an exact permutation (same reference)', () => {
		expect(reorderPages(pages, ['a', 'b'])).toBe(pages); // missing one
		expect(reorderPages(pages, ['a', 'b', 'x'])).toBe(pages); // unknown id
		expect(reorderPages(pages, ['a', 'a', 'b'])).toBe(pages); // duplicate
		expect(reorderPages(pages, ['a', 'b', 'c', 'c'])).toBe(pages); // too many
	});
});

describe('duplicateBlock', () => {
	const layout: BrandingBlock[] = [
		{
			id: 'accounts',
			type: 'accounts',
			visible: true,
			w: 12,
			h: 4,
			settings: { variant: 'stack' }
		},
		{ id: 'message', type: 'message', visible: true, w: 6, h: 2 }
	];

	it('inserts the copy immediately after the original with a fresh minted id', () => {
		const next = duplicateBlock(layout, 'accounts');
		expect(next.map((b) => b.id)).toEqual(['accounts', 'accounts-2', 'message']);
		expect(next[1]!.type).toBe('accounts');
		expect([next[1]!.w, next[1]!.h]).toEqual([12, 4]);
	});

	it("mints past already-taken '-N' ids", () => {
		const crowded = [...layout, { id: 'accounts-2', type: 'accounts' }];
		const next = duplicateBlock(crowded, 'accounts');
		expect(next[1]!.id).toBe('accounts-3');
	});

	it("mutating the duplicate's settings leaves the original's untouched (deep copy)", () => {
		const next = duplicateBlock(layout, 'accounts');
		next[1]!.settings!['variant'] = 'table';
		expect(layout[0]!.settings).toEqual({ variant: 'stack' });
	});

	it('a block with no settings duplicates without minting an empty settings object', () => {
		const next = duplicateBlock(layout, 'message');
		expect(next[2]!.id).toBe('message-2');
		expect(next[2]!.settings).toBeUndefined();
	});

	it('unknown id returns the same reference', () => {
		expect(duplicateBlock(layout, 'nope')).toBe(layout);
	});

	it('refuses at the per-page block cap (same reference)', () => {
		const full: BrandingBlock[] = Array.from({ length: PAGE_CAPS.maxBlocksPerPage }, (_, i) => ({
			id: `b${i}`,
			type: 'message'
		}));
		expect(duplicateBlock(full, 'b0')).toBe(full);
	});

	it('never mutates the input layout', () => {
		const before = structuredClone(layout);
		duplicateBlock(layout, 'accounts');
		expect(layout).toEqual(before);
	});
});
