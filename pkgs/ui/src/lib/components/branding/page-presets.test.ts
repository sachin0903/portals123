import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { BrandingPage } from '@graphene/auth';
import { minBlockW, MAX_COLS, MAX_ROWS } from './client-blocks.js';
import { PAGE_PRESETS, presetByKey, type PagePreset } from './page-presets.js';
import { defaultPages, PAGE_CAPS } from './portal-pages.js';
import { PORTAL_SLUG_RE, RESERVED_SLUGS } from './portal-slugs.js';

/**
 * Every preset must survive its own validation: each page set is table-tested through the same
 * registry rules app/branding.py re-enforces on save (slug shape, reserved slugs, the structural
 * caps, known block types, contract-member variants/looks/settings), so choosing a preset can
 * never hand the studio a layout the API would clamp or drop. The contract is read via node:fs —
 * independently of the module under test, which imports it — exactly as registry.test.ts does.
 */
const contract = JSON.parse(
	readFileSync(new URL('./block-contract.json', import.meta.url), 'utf8')
) as {
	blocks: Record<string, { variants: string[]; looks?: string[]; settings: string[] }>;
};

/** Every block list a page actually carries — the default plus any audience override. */
const audienceLayouts = (page: BrandingPage) =>
	[page.layouts.default, page.layouts.individual, page.layouts.organisation].filter(
		(b): b is NonNullable<typeof b> => b != null
	);

/** A page's default layout (a preset's curated tweaks are asserted on the default arm). */
const layoutOf = (preset: PagePreset, slug: string) =>
	preset.pages.find((p) => p.slug === slug)!.layouts.default;

/** The first block of `type` on the preset's page with `slug`. */
const blockOf = (preset: PagePreset, slug: string, type: string) =>
	layoutOf(preset, slug).find((b) => b.type === type);

/** The same, on the page's organisation override — where the portfolio page keeps its accounts. */
const orgBlockOf = (preset: PagePreset, slug: string, type: string) =>
	preset.pages.find((p) => p.slug === slug)!.layouts.organisation!.find((b) => b.type === type);

const contractCarries = (type: string, key: string) =>
	contract.blocks[type]?.settings.includes(key) ?? false;

describe('PAGE_PRESETS', () => {
	it('offers the four curated presets, keys unique, in picker order', () => {
		expect(PAGE_PRESETS.map((p) => p.key)).toEqual([
			'classic',
			'privateBank',
			'familyOffice',
			'professional'
		]);
	});

	it('labels and describes every preset', () => {
		for (const p of PAGE_PRESETS) {
			expect(p.label.length).toBeGreaterThan(0);
			expect(p.description.length).toBeGreaterThan(0);
		}
	});

	it('presetByKey finds each preset and refuses junk', () => {
		for (const p of PAGE_PRESETS) expect(presetByKey(p.key)).toBe(p);
		expect(presetByKey('nope')).toBeUndefined();
	});

	it('shares no page or block objects between presets or audiences (a studio edit must not bleed across)', () => {
		const seen = new Set<object>();
		for (const preset of PAGE_PRESETS) {
			for (const page of preset.pages) {
				expect(seen.has(page)).toBe(false);
				seen.add(page);
				for (const blocks of audienceLayouts(page)) {
					for (const block of blocks) {
						expect(seen.has(block)).toBe(false);
						seen.add(block);
						if (block.settings) {
							expect(seen.has(block.settings)).toBe(false);
							seen.add(block.settings);
						}
					}
				}
			}
		}
	});

	describe.each(PAGE_PRESETS.map((p) => [p.key, p] as const))('%s survives validation', (_, p) => {
		it('respects the page caps and keeps a navigable portal', () => {
			expect(p.pages.length).toBeGreaterThanOrEqual(1);
			expect(p.pages.length).toBeLessThanOrEqual(PAGE_CAPS.maxPages);
			const nav = p.pages.filter((page) => page.nav !== false).length;
			expect(nav).toBeGreaterThanOrEqual(1);
			expect(nav).toBeLessThanOrEqual(PAGE_CAPS.maxNavPages);
		});

		it('uses valid, unreserved, unique slugs (ids present)', () => {
			const slugs = p.pages.map((page) => page.slug);
			expect(new Set(slugs).size).toBe(slugs.length);
			for (const page of p.pages) {
				expect(page.slug).toMatch(PORTAL_SLUG_RE);
				expect(RESERVED_SLUGS.has(page.slug)).toBe(false);
				expect(page.id.length).toBeGreaterThan(0);
			}
		});

		it('stays inside the block caps across every audience layout', () => {
			let total = 0;
			for (const page of p.pages) {
				for (const blocks of audienceLayouts(page)) {
					expect(blocks.length).toBeLessThanOrEqual(PAGE_CAPS.maxBlocksPerPage);
					total += blocks.length;
				}
			}
			expect(total).toBeLessThanOrEqual(PAGE_CAPS.maxTotalBlocks);
		});

		it('carries exactly the audience overrides the base page set does', () => {
			// A preset adds no fork of its own, but must not DROP the platform's: reshaping only the
			// default would leave a firm's trusts and companies on the old composition.
			const base = Object.fromEntries(defaultPages().map((d) => [d.slug, d]));
			for (const page of p.pages) {
				const from = base[page.slug];
				if (!from) continue;
				expect(page.layouts.individual === undefined, `${page.slug} individual`).toBe(
					from.layouts.individual === undefined
				);
				expect(page.layouts.organisation === undefined, `${page.slug} organisation`).toBe(
					from.layouts.organisation === undefined
				);
			}
		});

		it('places only contract-known blocks with layout-unique ids and usable spans', () => {
			for (const page of p.pages)
				for (const blocks of audienceLayouts(page)) {
					const ids = blocks.map((b) => b.id);
					expect(new Set(ids).size).toBe(ids.length);
					for (const block of blocks) {
						expect(contract.blocks, `unknown type ${block.type}`).toHaveProperty(block.type);
						if (block.w !== undefined) {
							expect(block.w).toBeGreaterThanOrEqual(minBlockW(block.type));
							expect(block.w).toBeLessThanOrEqual(MAX_COLS);
						}
						if (block.h !== undefined) {
							expect(block.h).toBeGreaterThanOrEqual(1);
							expect(block.h).toBeLessThanOrEqual(MAX_ROWS);
						}
					}
				}
		});

		it('carries only contract-member settings, variants and looks', () => {
			for (const page of p.pages)
				for (const blocks of audienceLayouts(page))
					for (const block of blocks) {
						const meta = contract.blocks[block.type]!;
						for (const [key, value] of Object.entries(block.settings ?? {})) {
							expect(meta.settings, `${block.type}.${key}`).toContain(key);
							if (key === 'variant') expect(meta.variants).toContain(value);
							if (key === 'look') expect(meta.looks ?? []).toContain(value);
						}
					}
		});
	});

	describe('classic', () => {
		it('is exactly the platform default page set', () => {
			expect(presetByKey('classic')!.pages).toEqual(defaultPages());
		});
	});

	describe('privateBank', () => {
		const p = presetByKey('privateBank')!;

		it('removes news and sets the ledger statement header on home', () => {
			expect(blockOf(p, 'home', 'news')).toBeUndefined();
			expect(blockOf(p, 'home', 'portfolioHeader')?.settings?.['variant']).toBe('ledger');
			expect(blockOf(p, 'home', 'adviser')?.settings?.['look']).toBe('portrait');
		});

		it('pins each listing page to its chrome-less signature look', () => {
			// Accounts live on the organisation portfolio page only (the default has none to tune).
			expect(orgBlockOf(p, 'portfolio', 'accounts')?.settings?.['look']).toBe('ink');
			expect(blockOf(p, 'portfolio', 'holdings')?.settings?.['look']).toBe('sheet');
			expect(blockOf(p, 'activity', 'activity')?.settings?.['look']).toBe('timeline');
			expect(blockOf(p, 'documents', 'documents')?.settings?.['look']).toBe('index');
		});

		it('does not set the finish — that stays a Design-view theme choice', () => {
			// A preset is pages-only data; nothing here can carry a Branding.finish. The shape
			// itself proves it, but the description must keep pointing the firm at the pairing.
			expect(p.description).toMatch(/Ledger finish/);
		});
	});

	describe('familyOffice', () => {
		const p = presetByKey('familyOffice')!;

		it('leads home with full-width owner-grouped account cards ahead of a compact header', () => {
			const home = layoutOf(p, 'home');
			const accountsAt = home.findIndex((b) => b.type === 'accounts');
			const headerAt = home.findIndex((b) => b.type === 'portfolioHeader');
			expect(accountsAt).toBeGreaterThanOrEqual(0);
			expect(accountsAt).toBeLessThan(headerAt);
			const accounts = home[accountsAt]!;
			expect(accounts.w).toBe(12);
			expect(accounts.settings?.['variant']).toBe('cards');
			expect(accounts.settings?.['group_by']).toBe('owner');
			expect(home[headerAt]!.settings?.['variant']).toBe('compact');
		});

		it('groups the organisation portfolio’s accounts register by owner', () => {
			expect(orgBlockOf(p, 'portfolio', 'accounts')?.settings?.['group_by']).toBe('owner');
		});

		it('surfaces document requests exactly when the contract carries the switch', () => {
			const documents = blockOf(p, 'documents', 'documents');
			if (contractCarries('documents', 'show_requests')) {
				expect(documents?.settings?.['show_requests']).toBe(true);
			} else {
				expect(documents?.settings ?? {}).not.toHaveProperty('show_requests');
			}
		});
	});

	describe('professional', () => {
		const p = presetByKey('professional')!;

		it('makes home data-forward: compact header, summary tiles, spectrum, strip, tables', () => {
			expect(blockOf(p, 'home', 'portfolioHeader')?.settings?.['variant']).toBe('compact');
			expect(blockOf(p, 'home', 'accounts')?.settings?.['variant']).toBe('summary');
			expect(blockOf(p, 'home', 'allocation')?.settings?.['look']).toBe('spectrum');
			expect(blockOf(p, 'home', 'adviser')?.settings?.['look']).toBe('strip');
			expect(blockOf(p, 'home', 'holdings')?.settings?.['variant']).toBe('table');
			expect(blockOf(p, 'home', 'activity')?.settings?.['variant']).toBe('table');
		});

		it('keeps the organisation portfolio’s accounts register as the full table', () => {
			expect(orgBlockOf(p, 'portfolio', 'accounts')?.settings?.['variant']).toBe('table');
		});

		it.each([
			['portfolio', 'holdings'],
			['activity', 'activity']
		] as const)(
			'turns the %s page’s %s into a table, with a toolbar exactly when the contract carries it',
			(slug, type) => {
				// The defaults are the grouped book and the home-style list; this preset is the one
				// that wants tables, so it has to SET the variant, not just the toolbar.
				const block = blockOf(p, slug, type);
				expect(block?.settings?.['variant']).toBe('table');
				if (contractCarries(type, 'toolbar')) {
					expect(block?.settings?.['toolbar']).toBe(true);
				} else {
					expect(block?.settings ?? {}).not.toHaveProperty('toolbar');
				}
			}
		);
	});
});
