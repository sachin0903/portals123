import { derivePortalNav } from '@graphene/ui';

/**
 * The client portal's tab bar, as data. It lives here rather than inline in `+layout.svelte` because
 * it answers two questions that are worth asserting on directly — whether an onboarding client sees
 * anything else, and how many tabs the bar holds — and a `$derived.by` in a component can only be
 * checked by scanning the component's source, which pins the shape of the code rather than what it
 * decides. (It was: an earlier source scan for a specific ternary failed the moment the same
 * decision was written as an early return.)
 *
 * The bar is the firm's page set and nothing else. Insights used to be appended here in code
 * because `/insights` was a built-in route outside the set; it is now a page in the set (a `news`
 * block in its `library` variant), so a firm places, renames or drops it in the studio like any
 * other tab, and nothing the studio cannot see is drawn on the bar.
 *
 * Icons are NAMES, not components, so this module stays free of Svelte imports; the layout resolves
 * them through the shared `PAGE_ICONS`.
 */
export type PortalNavEntry = { label: string; href: string; iconName: string };

/**
 * The most tabs the bar holds. Five is where both platform guidelines stop (HIG, Material), and
 * where the labels stop fitting at 390px — six ran them at 11px and still clipped "Documents".
 * The studio's own cap (`PAGE_CAPS.maxNavPages`) is looser and stays so, because tightening it
 * would invalidate page sets firms have already saved; this is the BAR's cap, applied at render.
 */
export const TAB_BAR_CAP = 5;

/** Where a sub page's "‹ Back" leads: the parent's path and its name as the firm labels it. */
export type BackTarget = { href: string; label: string };

/**
 * The client portal has two tiers. The TAB pages — home and the firm's other pages, every one a
 * slug in the page set — carry the bar and no back control. Everything else is a SUB page, one
 * level down from a tab page, and the shell draws one consistent way back to its parent
 * (ClientShell `back`). This table is the parent of each sub-page route family, by first path
 * segment, as a SLUG in the page set — so a firm that renamed Docs to "Papers" gets "‹ Papers",
 * and a firm that dropped its Docs page sends a document's reader back to home rather than to a
 * 404. A record under a tab page (one document under Docs) returns to that page; the utilities
 * reached from the top bar (profile, messages, notifications…) return to home.
 */
const SUB_PAGE_PARENT: Record<string, string> = {
	messages: 'home',
	transfer: 'home',
	requests: 'home',
	notifications: 'home',
	profile: 'home',
	'look-through': 'home'
};
/** Route families that are a tab page at one segment and a record beneath it at two. */
const RECORD_PARENT: Record<string, string> = {
	accounts: 'home',
	documents: 'documents',
	insights: 'insights'
};

function parentTarget(slug: string, pages: Parameters<typeof derivePortalNav>[0]): BackTarget {
	const nav = derivePortalNav(pages);
	const href = slug === 'home' ? '/' : `/${slug}`;
	const hit = nav.find((i) => i.href === href) ?? nav.find((i) => i.href === '/');
	return hit ? { href: hit.href, label: hit.label } : { href: '/', label: 'Home' };
}

/**
 * The back target for `pathname`, or null on a tab page. A page's own `data.back` wins — the
 * fund look-through returns to the account it was opened from, which only its load knows.
 */
export function backFor(
	pathname: string,
	pages: Parameters<typeof derivePortalNav>[0],
	explicit: BackTarget | null = null
): BackTarget | null {
	if (explicit) return explicit;
	const [first, second] = pathname.split('/').filter(Boolean);
	if (!first) return null;
	const parent =
		SUB_PAGE_PARENT[first] ?? (second !== undefined ? RECORD_PARENT[first] : undefined);
	return parent ? parentTarget(parent, pages) : null;
}

export function portalNav(
	pages: Parameters<typeof derivePortalNav>[0],
	onboarding: boolean,
	journeyTitle?: string | null
): PortalNavEntry[] {
	// While a journey is live the bar is that journey ALONE: the whole portal is the client's
	// outstanding steps until they are done, and `+layout.server.ts` redirects everything else there
	// anyway, so a fuller bar would only offer tabs that bounce. Account opening stays "Onboarding";
	// any other journey is named for itself.
	if (onboarding) {
		return [
			{
				label: journeyTitle?.trim() || 'Onboarding',
				href: '/onboarding',
				iconName: 'clipboard-list'
			}
		];
	}
	return derivePortalNav(pages)
		.slice(0, TAB_BAR_CAP)
		.map((item) => ({ label: item.label, href: item.href, iconName: item.iconName }));
}
