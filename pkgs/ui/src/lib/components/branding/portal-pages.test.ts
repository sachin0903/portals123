import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import { MAX_COLS, MAX_ROWS, minBlockW } from './client-blocks.js';
import {
	defaultPages,
	derivePortalNav,
	mintBlockId,
	PAGE_CAPS,
	resolveAudienceLayout,
	slugify
} from './portal-pages.js';

describe('slugify', () => {
	const cases: [label: string, expected: string][] = [
		['Fee Schedule', 'fee-schedule'],
		['My_Custom_Page', 'my-custom-page'],
		// Unicode/symbol junk strips; the hyphens it leaves behind collapse and trim.
		['  Émojis 🎉 & Symbols!! ', 'mojis-symbols'],
		['a --- b', 'a-b'],
		// Reserved routes get suffixed rather than colliding with a real portal path.
		['Onboarding', 'onboarding-2'],
		['files', 'files-2'],
		// >32 chars cuts at the cap…
		['The Quarterly Portfolio Performance Review', 'the-quarterly-portfolio-performa'],
		// …and a cut that lands on a hyphen does not leave it dangling.
		['abcdefghij abcdefghij abcdefghi j', 'abcdefghij-abcdefghij-abcdefghi'],
		// Never empty.
		['', 'page'],
		['🎉🎉🎉', 'page'],
		['___', 'page']
	];
	for (const [label, expected] of cases)
		it(`'${label}' -> '${expected}'`, () => {
			expect(slugify(label)).toBe(expected);
		});

	it('caps at 32 characters', () => {
		expect(slugify('x'.repeat(100)).length).toBeLessThanOrEqual(32);
	});
});

describe('mintBlockId', () => {
	it('uses the bare type when free', () => {
		expect(mintBlockId('accounts', new Set())).toBe('accounts');
	});

	it("suffixes '-2', '-3', … past taken ids", () => {
		expect(mintBlockId('accounts', new Set(['accounts']))).toBe('accounts-2');
		expect(mintBlockId('accounts', new Set(['accounts', 'accounts-2']))).toBe('accounts-3');
	});

	it('skips holes left by deletions', () => {
		expect(mintBlockId('message', new Set(['message', 'message-3']))).toBe('message-2');
	});

	it('does not mutate the taken set (the caller records placements)', () => {
		const taken = new Set(['accounts']);
		mintBlockId('accounts', taken);
		expect(taken).toEqual(new Set(['accounts']));
	});
});

/** The contract, read via node:fs — independently of the module under test, which imports it. */
const contract = JSON.parse(
	readFileSync(new URL('./block-contract.json', import.meta.url), 'utf8')
) as {
	blocks: Record<string, { variants: string[]; looks?: string[]; settings: string[] }>;
	settingLimits: Record<string, number | [number, number]>;
	defaultPages: {
		slug: string;
		label: string;
		icon: string;
		blocks: ContractBlock[];
		individual?: ContractBlock[];
		organisation?: ContractBlock[];
	}[];
};
interface ContractBlock {
	id?: string;
	type: string;
	w: number;
	h: number;
	visible: boolean;
	settings?: Record<string, unknown>;
}

const AUDIENCES = [
	['blocks', 'default'],
	['individual', 'individual'],
	['organisation', 'organisation']
] as const;

describe('defaultPages', () => {
	const pages = defaultPages();

	it('pins to block-contract.json defaultPages — the cross-language referee', () => {
		// Python's default_pages() is an independent literal (the API container ships without
		// pkgs/ui, so it cannot read this file at runtime), and the first drafts of the two sides
		// shipped DIFFERENT spans for the same blocks. Both sides now pin to the same contract
		// section; drift fails a test instead of shipping two different "defaults". The blocks
		// compare their FULL `settings` object (not just the variant): a richer default — the
		// listing toolbars, the documents requests banner — that only one side shipped must fail
		// here, and the contract keeps entries minimal (no settings key ⇒ this side carries none).
		// An audience layout only one side ships fails the same way — the key is on both or neither.
		const shape = (b: BrandingBlock) => ({
			// The contract spells an id out only where a page holds two blocks of one type.
			...(b.id !== undefined && b.id !== b.type ? { id: b.id } : {}),
			type: b.type,
			w: b.w,
			h: b.h,
			visible: b.visible !== false,
			...(b.settings && Object.keys(b.settings).length > 0 ? { settings: b.settings } : {})
		});
		const got = pages.map((p) => ({
			slug: p.slug,
			label: p.label,
			icon: p.icon,
			blocks: p.layouts.default.map(shape),
			...(p.layouts.individual ? { individual: p.layouts.individual.map(shape) } : {}),
			...(p.layouts.organisation ? { organisation: p.layouts.organisation.map(shape) } : {})
		}));
		expect(got, 'change together with block-contract.json + apps/api/app/branding.py').toEqual(
			contract.defaultPages
		);
	});

	it("is today's five portal surfaces, home first, in nav order", () => {
		expect(pages.map((p) => p.slug)).toEqual([
			'home',
			'portfolio',
			'activity',
			'documents',
			'insights'
		]);
	});

	it("ids are slugs, labels are today's nav labels, every page is in nav", () => {
		for (const p of pages) expect(p.id).toBe(p.slug);
		expect(pages.map((p) => p.label)).toEqual([
			'Home',
			'Portfolio',
			'Activity',
			'Docs',
			'Insights'
		]);
		expect(pages.every((p) => p.nav === true)).toBe(true);
	});

	it("icons are today's nav icons", () => {
		expect(pages.map((p) => p.icon)).toEqual([
			'house',
			'pie-chart',
			'arrow-left-right',
			'file-text',
			'newspaper'
		]);
	});

	it('home opens with the onboarding/action strips over the hero header', () => {
		const home = pages[0]!.layouts.default;
		expect(home.slice(0, 3).map((b) => b.type)).toEqual([
			'onboardingProgress',
			'actionItems',
			'portfolioHeader'
		]);
		// onboardingProgress is taller — the milestone stepper needs the room; the other two stay at one unit.
		expect([home[0]!.w, home[0]!.h]).toEqual([12, 2]);
		for (const b of home.slice(1, 3)) expect([b.w, b.h]).toEqual([12, 1]);
		expect(home[2]!.settings).toEqual({ variant: 'hero', show_chart: true });
	});

	it('puts the adviser under the accounts and above the listings, for both audiences', () => {
		for (const [, field] of AUDIENCES) {
			const home = pages[0]!.layouts[field];
			if (!home) continue;
			const at = (type: string) => home.findIndex((b) => b.type === type);
			expect(at('adviser'), field).toBe(at('accounts') + 1);
			expect(at('adviser'), field).toBeLessThan(at('activity'));
			expect(at('activity'), field).toBeLessThan(at('documents'));
		}
	});

	// HOME IS SHORT (portal-pages.ts): analysis lives on Portfolio and news on Insights, so those
	// three ship hidden on home — placed, for a firm to toggle back — and everything else shows.
	it('hides allocation, holdings and news on home; everything else visible', () => {
		const quiet = new Set(['allocation', 'holdings', 'news']);
		for (const [, field] of AUDIENCES) {
			const home = pages[0]!.layouts[field];
			if (!home) continue;
			for (const b of home) expect(b.visible, `${field} ${b.type}`).toBe(!quiet.has(b.type));
		}
	});

	// Empty copy claims no grid cell (portal-page.svelte::isBlank), and platform copy in a firm's voice would be a fabrication — so the slot is placed and left blank.
	it('places a message slot but writes no copy into it', () => {
		const message = pages[0]!.layouts.default.find((b) => b.type === 'message');
		expect(message).toBeDefined();
		expect(message!.settings).toEqual({ look: 'note' });
	});

	it('block ids are the type names, unique per layout', () => {
		for (const p of pages) {
			for (const [, field] of AUDIENCES) {
				const blocks = p.layouts[field];
				if (!blocks) continue;
				const ids = blocks.map((b) => b.id);
				expect(ids.length, `${p.slug} ${field}`).toBe(new Set(ids).size);
				// The type name, except where a layout holds two of a type — the added block takes the second id.
				for (const b of blocks)
					expect(b.id, `${p.slug} ${field}`).toMatch(new RegExp(`^${b.type}(-\\d+)?$`));
			}
		}
	});

	it('listing pages pin their variants, looks and toolbars', () => {
		const first = Object.fromEntries(pages.map((p) => [p.slug, p.layouts.default[0]!]));
		// Portfolio leads with the figure; the whole-book holdings sit under the donut, grouped.
		expect(first['portfolio']!.settings).toEqual({ variant: 'compact', show_chart: false });
		const portfolio = pages.find((p) => p.slug === 'portfolio')!.layouts.default;
		expect(portfolio.map((b) => b.type)).toEqual(['portfolioHeader', 'allocation', 'holdings']);
		expect(portfolio[2]!.settings).toEqual({ variant: 'grouped' });
		// The home card's list, uncapped by its toolbar — not a table (see portal-pages.ts).
		expect(first['activity']!.settings).toEqual({
			variant: 'list',
			look: 'card',
			toolbar: true
		});
		expect(first['documents']!.settings).toEqual({
			variant: 'grouped',
			look: 'timeline',
			toolbar: true,
			show_requests: true
		});
		expect(first['insights']!.settings).toEqual({ variant: 'library', look: 'magazine' });
		for (const slug of ['activity', 'documents', 'insights'])
			expect([first[slug]!.w, first[slug]!.h], slug).toEqual([12, 4]);
	});

	it('home-page compact blocks stay toolbar-less (the quiet-home rule)', () => {
		for (const [, field] of AUDIENCES)
			for (const b of pages[0]!.layouts[field] ?? [])
				expect(b.settings?.['toolbar'], b.type).not.toBe(true);
	});

	it('overrides trusts and companies on home and portfolio only', () => {
		const withOverride = pages.filter((p) => p.layouts.organisation).map((p) => p.slug);
		// The listing pages carry none: a full table suits both, and a repeat of the default is a fork to maintain.
		expect(withOverride).toEqual(['home', 'portfolio']);
		// `individual` is deliberately never set: the default IS it, and setting both leaves `default` meaningless.
		for (const p of pages) expect(p.layouts.individual, p.slug).toBeUndefined();
	});

	it('re-cuts the organisation home as a register', () => {
		const org = pages[0]!.layouts.organisation!;
		const by = Object.fromEntries(org.map((b) => [b.type, b]));
		expect(by['accounts']!.settings).toEqual({ variant: 'table', group_by: 'owner' });
		expect(by['accounts']!.w).toBe(12);
		expect(by['activity']!.settings).toEqual({ variant: 'table', limit: 8 });
		expect(by['adviser']!.settings).toEqual({ look: 'strip' });
		expect(by['allocation']!.settings).toEqual({ variant: 'bars', look: 'spectrum' });
	});

	it('leads the organisation portfolio page with a roll-up above the accounts register', () => {
		// A trust or company reads its accounts as a register, so its portfolio keeps them.
		const org = pages.find((p) => p.slug === 'portfolio')!.layouts.organisation!;
		expect(org.map((b) => b.type)).toEqual(['accounts', 'accounts', 'allocation', 'holdings']);
		expect(org[0]!.settings).toEqual({ variant: 'summary' });
		expect(org[1]!.settings?.['variant']).toBe('table');
		expect(org[3]!.settings).toEqual({ variant: 'table', toolbar: true });
	});

	it('shares no block objects between a page’s audience layouts', () => {
		// The studio mutates blocks in place, so a shared object lets an edit on one audience tab rewrite the other.
		const seen = new Set<object>();
		for (const p of pages)
			for (const [, field] of AUDIENCES)
				for (const b of p.layouts[field] ?? []) {
					expect(seen.has(b), `${p.slug} ${field} ${b.type}`).toBe(false);
					seen.add(b);
					if (b.settings) {
						expect(seen.has(b.settings)).toBe(false);
						seen.add(b.settings);
					}
				}
	});

	it('names only contract members, within the contract’s bounds', () => {
		// The pin above proves the two literals agree; this proves what they agree on is legal. A contract edit dropping a look would leave both pointing at it, and the API clamps that to 'auto'.
		let total = 0;
		for (const p of pages) {
			for (const [, field] of AUDIENCES) {
				const blocks = p.layouts[field];
				if (!blocks) continue;
				total += blocks.length;
				expect(blocks.length, `${p.slug} ${field}`).toBeLessThanOrEqual(PAGE_CAPS.maxBlocksPerPage);
				for (const b of blocks) {
					const meta = contract.blocks[b.type];
					expect(meta, `unknown type ${b.type}`).toBeDefined();
					expect(b.w!).toBeGreaterThanOrEqual(minBlockW(b.type));
					expect(b.w!).toBeLessThanOrEqual(MAX_COLS);
					expect(b.h!).toBeGreaterThanOrEqual(1);
					expect(b.h!).toBeLessThanOrEqual(MAX_ROWS);
					for (const [key, value] of Object.entries(b.settings ?? {})) {
						expect(meta!.settings, `${p.slug} ${field} ${b.type}.${key}`).toContain(key);
						if (key === 'variant') expect(meta!.variants).toContain(value);
						if (key === 'look') expect(meta!.looks ?? []).toContain(value);
						const bound = contract.settingLimits[`${b.type}.${key}`];
						if (Array.isArray(bound)) {
							expect(value as number).toBeGreaterThanOrEqual(bound[0]);
							expect(value as number).toBeLessThanOrEqual(bound[1]);
						}
					}
				}
			}
		}
		expect(total).toBeLessThanOrEqual(PAGE_CAPS.maxTotalBlocks);
		expect(pages.length).toBeLessThanOrEqual(PAGE_CAPS.maxPages);
		expect(pages.filter((p) => p.nav !== false).length).toBeLessThanOrEqual(PAGE_CAPS.maxNavPages);
	});
});

describe('resolveAudienceLayout', () => {
	// Insights deliberately: it carries no audience override, so the fallback arms run against a
	// real page. (It was Holdings until the default set lost that page — Oct 2026.)
	const base = defaultPages().find((p) => p.slug === 'insights')!;
	const def = base.layouts.default;

	it('falls back to default when the audience has no override', () => {
		expect(resolveAudienceLayout(base, 'individual')).toBe(def);
		expect(resolveAudienceLayout(base, 'organisation')).toBe(def);
	});

	it('falls back to default on an explicit null override', () => {
		const p: BrandingPage = { ...base, layouts: { default: def, organisation: null } };
		expect(resolveAudienceLayout(p, 'organisation')).toBe(def);
	});

	it('returns a set override', () => {
		const override = [{ id: 'message', type: 'message', visible: true, w: 12, h: 1 }];
		const p: BrandingPage = { ...base, layouts: { default: def, individual: override } };
		expect(resolveAudienceLayout(p, 'individual')).toBe(override);
		expect(resolveAudienceLayout(p, 'organisation')).toBe(def);
	});

	// An empty array is an explicit "show this audience nothing" — a firm hiding a page's content
	// from trusts made a decision, and collapsing [] to the default would silently undo it.
	it('honours an EMPTY ARRAY override', () => {
		const p: BrandingPage = { ...base, layouts: { default: def, organisation: [] } };
		expect(resolveAudienceLayout(p, 'organisation')).toEqual([]);
	});
});

describe('derivePortalNav', () => {
	function customPage(slug: string, nav = true): BrandingPage {
		return {
			id: slug,
			slug,
			label: slug,
			nav,
			layouts: { default: [] }
		};
	}

	it("null/undefined/empty pages fall back to the default set (today's five tabs)", () => {
		for (const input of [null, undefined, [] as BrandingPage[]]) {
			const nav = derivePortalNav(input);
			expect(nav.map((n) => n.href)).toEqual([
				'/',
				'/portfolio',
				'/activity',
				'/documents',
				'/insights'
			]);
			expect(nav.map((n) => n.label)).toEqual([
				'Home',
				'Portfolio',
				'Activity',
				'Docs',
				'Insights'
			]);
			expect(nav[0]!.iconName).toBe('house');
		}
	});

	it("home maps to '/', other slugs to '/{slug}'", () => {
		const nav = derivePortalNav([customPage('home'), customPage('fees')]);
		expect(nav.map((n) => n.href)).toEqual(['/', '/fees']);
	});

	it('keeps stored order and drops nav:false pages', () => {
		const nav = derivePortalNav([customPage('b'), customPage('hidden', false), customPage('a')]);
		expect(nav.map((n) => n.label)).toEqual(['b', 'a']);
	});

	it('treats an absent nav flag as shown', () => {
		const p = customPage('fees');
		delete (p as { nav?: boolean }).nav;
		expect(derivePortalNav([p])).toHaveLength(1);
	});

	it('caps at 6 entries', () => {
		const many = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map((s) => customPage(s));
		expect(derivePortalNav(many)).toHaveLength(6);
	});

	it("falls back to 'layout-grid' when a page names no icon", () => {
		expect(derivePortalNav([customPage('fees')])[0]!.iconName).toBe('layout-grid');
	});
});
