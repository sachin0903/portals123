/**
 * Portal pages — the page-level half of the client-portal customisation contract: the slug/icon/cap
 * allowlists (sourced from ./block-contract.json, the shared Python/TS contract), the default page
 * set, and the pure helpers the studio + client renderer share. The default page set MUST mirror
 * app/branding.py `default_pages()` — the API materialises it for tenants with no `pages`, this side
 * renders it when the wire carries none, and the two must agree or a firm's first "customise" click
 * would silently reshape their portal. Change them together (registry.test.ts pins the contract).
 */
import type { Component } from 'svelte';
import type { BrandingBlock, BrandingPage } from '@graphene/auth';
import contract from './block-contract.json';
import { RESERVED_SLUGS } from './portal-slugs.js';
import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
import BanknoteIcon from '@lucide/svelte/icons/banknote';
import BriefcaseIcon from '@lucide/svelte/icons/briefcase';
import ChartLineIcon from '@lucide/svelte/icons/chart-line';
import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
import FileTextIcon from '@lucide/svelte/icons/file-text';
import FolderIcon from '@lucide/svelte/icons/folder';
import HouseIcon from '@lucide/svelte/icons/house';
import InfoIcon from '@lucide/svelte/icons/info';
import LandmarkIcon from '@lucide/svelte/icons/landmark';
import LayersIcon from '@lucide/svelte/icons/layers';
import LayoutGridIcon from '@lucide/svelte/icons/layout-grid';
import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
import NewspaperIcon from '@lucide/svelte/icons/newspaper';
import PieChartIcon from '@lucide/svelte/icons/pie-chart';
import ReceiptIcon from '@lucide/svelte/icons/receipt';
import ShieldIcon from '@lucide/svelte/icons/shield';
import StarIcon from '@lucide/svelte/icons/star';
import UsersIcon from '@lucide/svelte/icons/users';
import WalletIcon from '@lucide/svelte/icons/wallet';

/** The slug rules live in ./portal-slugs.ts — a svelte-free module the apps can import for their
 * param matchers (`@graphene/ui/portal-slugs`); re-exported here so existing consumers keep one
 * import site. */
export { PORTAL_SLUG_RE, RESERVED_SLUGS, SHADOWABLE_SLUGS } from './portal-slugs.js';

/** The curated page-icon names a firm can pick from (lucide names; see PAGE_ICONS). */
export const PAGE_ICON_NAMES: readonly string[] = contract.pageIcons;

/** Structural caps: maxPages / maxBlocksPerPage / maxTotalBlocks / maxNavPages. */
export const PAGE_CAPS = contract.caps;

/** The icon components behind PAGE_ICON_NAMES. Look up by a page's `icon`; an unknown or unset
 * name is the caller's cue to fall back to 'layout-grid' (derivePortalNav already emits it). */
export const PAGE_ICONS: Record<string, Component> = {
	house: HouseIcon,
	wallet: WalletIcon,
	layers: LayersIcon,
	'arrow-left-right': ArrowLeftRightIcon,
	'file-text': FileTextIcon,
	'clipboard-list': ClipboardListIcon,
	'pie-chart': PieChartIcon,
	receipt: ReceiptIcon,
	landmark: LandmarkIcon,
	briefcase: BriefcaseIcon,
	shield: ShieldIcon,
	users: UsersIcon,
	newspaper: NewspaperIcon,
	'message-circle': MessageCircleIcon,
	banknote: BanknoteIcon,
	'chart-line': ChartLineIcon,
	folder: FolderIcon,
	star: StarIcon,
	'layout-grid': LayoutGridIcon,
	info: InfoIcon
};

/** A nav entry derived from a page — icon by NAME so the derivation stays pure (the client app
 * maps `iconName` through PAGE_ICONS itself). */
export interface PortalNavItem {
	label: string;
	href: string;
	iconName: string;
}

/**
 * A URL-safe page slug from a human label: lowercase; spaces/underscores → hyphens; anything
 * outside [a-z0-9-] stripped; hyphens collapsed and trimmed; cut to 32 chars. Never empty
 * ('page' fallback) and never a reserved route ('-2' suffix — reserved slugs are short, so the
 * suffix cannot push it back over the cap in any meaningful way).
 */
export function slugify(label: string): string {
	let s = label
		.toLowerCase()
		.replace(/[\s_]+/g, '-')
		.replace(/[^a-z0-9-]/g, '')
		.replace(/-{2,}/g, '-')
		.replace(/^-+|-+$/g, '')
		.slice(0, 32)
		.replace(/-+$/, ''); // the 32-char cut can leave a trailing hyphen
	if (!s) s = 'page';
	if (RESERVED_SLUGS.has(s)) s = `${s}-2`;
	return s;
}

// Minting lives beside blockKeys in client-blocks.ts, so the renderer can key by the same identity
// without pulling in the page vocabulary. Re-exported because this is where callers know it from.
export { mintBlockId } from './client-blocks.js';

function page(
	slug: string,
	label: string,
	icon: string,
	blocks: BrandingBlock[],
	organisation?: BrandingBlock[]
): BrandingPage {
	return {
		id: slug,
		slug,
		label,
		icon,
		nav: true,
		layouts: organisation ? { default: blocks, organisation } : { default: blocks }
	};
}

/**
 * The default client-portal page set — a short composed home page, an accounts page that holds
 * the analysis, and the two full-listing surfaces. MUST stay byte-for-byte equivalent to
 * app/branding.py `default_pages()` (both are built from the contract's `defaultPages` section);
 * page ids = slugs, block ids = type names except where a page holds two of a type, labels = the
 * portal's nav labels (note 'Docs', not 'Documents').
 *
 * HOME IS SHORT. It used to be every other page in miniature — eight widgets, each a teaser for a
 * tab one tap away, 2,500px of scroll on a phone before the activity list, and the activity (the
 * one thing that is new since last visit) last. Now: the figure, what needs you, your accounts,
 * what has changed, what has arrived, who to talk to, and the firm's note. The adviser card sits
 * beside the recent documents — an advised portal that never shows the adviser is a brokerage —
 * and it is the one block on home that is a person rather than a number. Accounts and the adviser
 * are one unit tall: authored `h` is a floor, and a single account card under a two-unit floor
 * left a hole before the next block. Analysis (allocation,
 * holdings) lives on the Accounts page; news has Insights. Those blocks ship HIDDEN on home rather
 * than absent, so a firm that wants one back toggles it in the studio instead of re-adding and
 * re-sizing it. `message` ships with no copy, which the renderer treats as blank and gives no grid
 * cell — a slot, not an empty card.
 *
 * NO HOLDINGS PAGE. Holdings are a cross-account slice of what each account already shows, and
 * the tab bar is capped at five on a phone (Home · Accounts · Activity · Docs · Insights); the
 * whole-book holdings table sits under the accounts instead. `holdings` stays a shadowable slug,
 * so a firm that wants the page back can add it.
 *
 * The `organisation` overrides re-cut home and accounts as a register for trusts and companies;
 * the listing pages need none, a full table suiting both.
 */
export function defaultPages(): BrandingPage[] {
	const home: BrandingBlock[] = [
		{
			id: 'onboardingProgress',
			type: 'onboardingProgress',
			visible: true,
			w: 12,
			h: 2,
			settings: { look: 'milestones' }
		},
		{
			id: 'actionItems',
			type: 'actionItems',
			visible: true,
			w: 12,
			h: 1,
			settings: { look: 'banner', hide_when_empty: true }
		},
		{
			id: 'portfolioHeader',
			type: 'portfolioHeader',
			visible: true,
			w: 12,
			h: 1,
			settings: { variant: 'hero', show_chart: true }
		},
		{
			id: 'accounts',
			type: 'accounts',
			visible: true,
			w: 6,
			h: 1,
			settings: { variant: 'stack', look: 'card', group_by: 'wrapper', show_sparklines: true }
		},
		// The adviser sits right under the accounts, ABOVE the activity and documents listings:
		// who to talk to is the second thing on the page, not the last card after two lists.
		{ id: 'adviser', type: 'adviser', visible: true, w: 6, h: 1, settings: { look: 'card' } },
		{
			id: 'activity',
			type: 'activity',
			visible: true,
			w: 6,
			h: 2,
			settings: { variant: 'list', look: 'card', limit: 4 }
		},
		{
			id: 'documents',
			type: 'documents',
			visible: true,
			w: 6,
			h: 2,
			settings: { variant: 'recent', limit: 4 }
		},
		{ id: 'message', type: 'message', visible: true, w: 12, h: 2, settings: { look: 'note' } },
		{
			id: 'allocation',
			type: 'allocation',
			visible: false,
			w: 5,
			h: 2,
			settings: { variant: 'donut' }
		},
		{
			id: 'holdings',
			type: 'holdings',
			visible: false,
			w: 6,
			h: 2,
			settings: { variant: 'top', limit: 5 }
		},
		{ id: 'news', type: 'news', visible: false, w: 4, h: 2 }
	];
	const homeOrganisation: BrandingBlock[] = [
		{
			id: 'onboardingProgress',
			type: 'onboardingProgress',
			visible: true,
			w: 12,
			h: 2,
			settings: { look: 'milestones' }
		},
		{
			id: 'actionItems',
			type: 'actionItems',
			visible: true,
			w: 12,
			h: 1,
			settings: { look: 'banner', hide_when_empty: true }
		},
		{
			id: 'portfolioHeader',
			type: 'portfolioHeader',
			visible: true,
			w: 12,
			h: 1,
			settings: { variant: 'hero', show_chart: true }
		},
		{
			id: 'accounts',
			type: 'accounts',
			visible: true,
			w: 12,
			h: 2,
			settings: { variant: 'table', group_by: 'owner' }
		},
		{ id: 'adviser', type: 'adviser', visible: true, w: 12, h: 1, settings: { look: 'strip' } },
		{
			id: 'activity',
			type: 'activity',
			visible: true,
			w: 12,
			h: 2,
			settings: { variant: 'table', limit: 8 }
		},
		{
			id: 'documents',
			type: 'documents',
			visible: true,
			w: 12,
			h: 2,
			settings: { variant: 'recent', limit: 4 }
		},
		{ id: 'message', type: 'message', visible: true, w: 12, h: 2, settings: { look: 'note' } },
		{
			id: 'allocation',
			type: 'allocation',
			visible: false,
			w: 6,
			h: 2,
			settings: { variant: 'bars', look: 'spectrum' }
		},
		{
			id: 'holdings',
			type: 'holdings',
			visible: false,
			w: 6,
			h: 2,
			settings: { variant: 'top', limit: 8 }
		},
		{ id: 'news', type: 'news', visible: false, w: 4, h: 2 }
	];
	// A factory, not a shared array: the studio mutates blocks, so one tab's edit would show on the other.
	// PORTFOLIO is the whole-book lens neither home nor an account record gives: the figure and its
	// return, how it is spread, and what it is made of — the holdings under the asset classes the
	// donut draws, in its colours. No account cards: home's deck already lists the accounts, and
	// a page of the same cards one tab over was a page with nothing of its own.
	const portfolioBlocks = (): BrandingBlock[] => [
		{
			id: 'portfolioHeader',
			type: 'portfolioHeader',
			visible: true,
			w: 12,
			h: 1,
			settings: { variant: 'compact', show_chart: false }
		},
		{
			id: 'allocation',
			type: 'allocation',
			visible: true,
			w: 12,
			h: 2,
			settings: { variant: 'donut' }
		},
		{
			id: 'holdings',
			type: 'holdings',
			visible: true,
			w: 12,
			h: 4,
			settings: { variant: 'grouped' }
		}
	];
	// The roll-up is the ADDED block, so it takes the second id — the ids a copy-on-write override would carry.
	// A trust or company reads its accounts as a register, so its portfolio page keeps them.
	const portfolioOrganisation: BrandingBlock[] = [
		{
			id: 'accounts-2',
			type: 'accounts',
			visible: true,
			w: 12,
			h: 2,
			settings: { variant: 'summary' }
		},
		{
			id: 'accounts',
			type: 'accounts',
			visible: true,
			w: 12,
			h: 4,
			settings: { variant: 'table', group_by: 'wrapper', show_sparklines: true }
		},
		{
			id: 'allocation',
			type: 'allocation',
			visible: true,
			w: 12,
			h: 2,
			settings: { variant: 'bars', look: 'spectrum' }
		},
		{
			id: 'holdings',
			type: 'holdings',
			visible: true,
			w: 12,
			h: 4,
			settings: { variant: 'table', toolbar: true }
		}
	];
	// The listing pages ship their toolbars ON (search/filters — and the documents requests
	// banner), replicating the legacy listing pages; home's compact blocks stay toolbar-less.
	// Settings objects mirror the contract's `defaultPages` entries byte-for-byte (the pin test
	// compares the FULL object, not just the variant).
	return [
		page('home', 'Home', 'house', home, homeOrganisation),
		page('portfolio', 'Portfolio', 'pie-chart', portfolioBlocks(), portfolioOrganisation),
		// The home card's list, not a table: one style for activity wherever it is read, and a
		// table of Date · Description · Amount is a statement, not a feed, on a phone. The toolbar
		// (search + account filter) is what makes it the full page; the list is uncapped by it.
		page('activity', 'Activity', 'arrow-left-right', [
			{
				id: 'activity',
				type: 'activity',
				visible: true,
				w: 12,
				h: 4,
				settings: { variant: 'list', look: 'card', toolbar: true }
			}
		]),
		page('documents', 'Docs', 'file-text', [
			{
				id: 'documents',
				type: 'documents',
				visible: true,
				w: 12,
				h: 4,
				settings: { variant: 'grouped', look: 'timeline', toolbar: true, show_requests: true }
			}
		]),
		// Insights is a PAGE in the set, not a route the client app bolts onto the bar: a firm
		// places, renames or removes it in the studio like any other tab. The `library` news
		// variant is the whole content library (lead + list); `/insights/{uid}` stays built in.
		page('insights', 'Insights', 'newspaper', [
			{
				id: 'news',
				type: 'news',
				visible: true,
				w: 12,
				h: 4,
				// `magazine` pinned: the default Insights page leads with the firm's own pictures
				// and palette — the one page where its identity has the room (looks.ts).
				settings: { variant: 'library', look: 'magazine' }
			}
		])
	];
}

/**
 * The block layout a page shows one audience. A set override wins — INCLUDING an empty array,
 * which is an explicit "show this audience nothing" and must be honoured (a firm hiding a page's
 * content from trusts is a decision, not an accident). Only null/absent falls back to the default.
 */
export function resolveAudienceLayout(
	page: BrandingPage,
	audience: 'individual' | 'organisation'
): BrandingBlock[] {
	return page.layouts[audience] ?? page.layouts.default;
}

/**
 * The client portal's tab bar from a tenant's pages: nav pages in stored order, capped at
 * maxNavPages; home maps to '/', everything else to '/{slug}'. `nav` absent counts as shown (a
 * page a firm creates without touching the toggle should appear). Null/empty pages ⇒ the default
 * set, so a tenant that has never customised gets today's five tabs.
 */
export function derivePortalNav(pages: BrandingPage[] | null | undefined): PortalNavItem[] {
	const source = pages && pages.length > 0 ? pages : defaultPages();
	return source
		.filter((p) => p.nav !== false)
		.slice(0, PAGE_CAPS.maxNavPages)
		.map((p) => ({
			label: p.label,
			href: p.slug === 'home' ? '/' : `/${p.slug}`,
			iconName: p.icon ?? 'layout-grid'
		}));
}
