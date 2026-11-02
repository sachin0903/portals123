// ── Focused sidebar ──────────────────────────────────────────────────────────────────────────────
// A record page (a client, an account) focuses the sidebar on itself: the global nav gives way to
// that record's own sections. The section list belongs to the record route, not to the chrome — so a
// route declares it by returning `sidebarFocus` from its `load` and the root layout renders it.
// Producers: `client-sections.ts` and `account-sections.ts` in this directory.
//
// It has to travel as data (`page.data` is serialised), so an icon is a KEY into the registry below
// rather than a component.
//
// Both portals import this from `@graphene/ui/nav`. It must not import an app's `$lib`: that alias
// resolves to whichever app bundled the module.

import type { Component } from 'svelte';
import type { NavContext, NavGroup } from '../components/shell/types';
import HouseIcon from '@lucide/svelte/icons/house';
import PiggyBankIcon from '@lucide/svelte/icons/piggy-bank';
import BanknoteIcon from '@lucide/svelte/icons/banknote';
import FileTextIcon from '@lucide/svelte/icons/file-text';
import BookOpenIcon from '@lucide/svelte/icons/book-open';
import UsersIcon from '@lucide/svelte/icons/users';
import ShieldIcon from '@lucide/svelte/icons/shield-check';
import ActivityIcon from '@lucide/svelte/icons/activity';
import MailIcon from '@lucide/svelte/icons/mail';
import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
import ContactRoundIcon from '@lucide/svelte/icons/contact-round';
import RouteIcon from '@lucide/svelte/icons/route';

/** One row of a focused sidebar. */
export interface FocusSection {
	label: string;
	href: string;
	/** Key into `FOCUS_ICONS`; an unknown or absent key simply renders no icon. */
	icon?: string;
	/** Highlight only on an exact path match (a section index like `/clients/12`). */
	exact?: boolean;
	/** Same dot as a global `NavItem`'s — see that type for why it's a dot, never a count. */
	unread?: boolean;
}

/** What a page publishes as `sidebarFocus` to focus the chrome on one record. */
export interface SidebarFocus {
	/** Small label above the title, e.g. `CLIENT`. */
	eyebrow?: string;
	title: string;
	subtitle?: string;
	back?: { href: string; label: string };
	sections?: FocusSection[];
	/**
	 * When set, one of `sections` carries a live unread dot (icon `'chat'`) that the ROOT layout
	 * keeps fresh itself, by polling `GET /chats/unread?contact_id=<this value>` — see its poller
	 * in `routes/+layout.svelte`. The section's own `unread` here is still the correct value for
	 * the very first paint (seeded server-side); this key is what lets the root layout override it
	 * afterwards without re-running this page's `load`.
	 */
	liveUnreadContactId?: string;
}

/** Icons a focused section may ask for by name — exactly the sections that exist today. Deliberately
 * a closed set: `page.data` is untrusted input as far as the chrome is concerned, and an unknown key
 * degrades to "no icon" rather than throwing. Adding a section means adding its key here. */
export const FOCUS_ICONS: Record<string, Component> = {
	overview: HouseIcon,
	holdings: PiggyBankIcon,
	transactions: BanknoteIcon,
	documents: FileTextIcon,
	illustrations: BookOpenIcon,
	people: UsersIcon,
	compliance: ShieldIcon,
	emails: MailIcon,
	activity: ActivityIcon,
	// Two keys, not one: Communications is the outbound-email ledger and Activity is the action
	// trail. They are different surfaces, so they carry different glyphs — one symbol for two
	// meanings inside a single sidebar is how a reader learns to distrust the icons.
	chat: MessageCircleIcon,
	details: ContactRoundIcon,
	journeys: RouteIcon
};

const isRecord = (v: unknown): v is Record<string, unknown> =>
	typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | undefined =>
	typeof v === 'string' && v.trim() ? v.trim() : undefined;

/**
 * Read `page.data.sidebarFocus`, or `null` when the current page publishes none (the common case —
 * the sidebar then shows the global nav). Validating rather than casting: this is untyped page data
 * from any route in the app, and a half-formed focus must fall back to the global nav rather than
 * render a nav with blank rows.
 */
export function parseFocus(value: unknown): SidebarFocus | null {
	if (!isRecord(value)) return null;
	const title = str(value.title);
	if (!title) return null;

	const back = isRecord(value.back) ? value.back : null;
	const backHref = str(back?.href);
	const backLabel = str(back?.label);

	const sections = Array.isArray(value.sections)
		? value.sections.filter(isRecord).flatMap((s): FocusSection[] => {
				const label = str(s.label);
				const href = str(s.href);
				if (!label || !href) return [];
				return [
					{
						label,
						href,
						...(str(s.icon) ? { icon: str(s.icon) } : {}),
						...(s.exact === true ? { exact: true } : {}),
						...(s.unread === true ? { unread: true } : {})
					}
				];
			})
		: [];

	return {
		...(str(value.eyebrow) ? { eyebrow: str(value.eyebrow) } : {}),
		title,
		...(str(value.subtitle) ? { subtitle: str(value.subtitle) } : {}),
		...(backHref && backLabel ? { back: { href: backHref, label: backLabel } } : {}),
		...(sections.length ? { sections } : {}),
		...(str(value.liveUnreadContactId)
			? { liveUnreadContactId: str(value.liveUnreadContactId) }
			: {})
	};
}

/** The context header for a focused sidebar. */
export function focusContext(focus: SidebarFocus): NavContext {
	return {
		...(focus.eyebrow ? { eyebrow: focus.eyebrow } : {}),
		title: focus.title,
		...(focus.subtitle ? { subtitle: focus.subtitle } : {}),
		...(focus.back ? { back: focus.back } : {})
	};
}

/** The nav for a focused sidebar: the record's own sections, icons resolved from the registry. */
/**
 * The sidebar for a focused record: that record's own sections, plus the PINNED groups from the
 * global nav.
 *
 * A focused sidebar used to replace the global one outright, which took Firm settings with it —
 * so the moment you opened a client or an account, the way to reach Team, Roles, Templates or the
 * Studio was to navigate out of the record first. Firm settings is where you go to change how the
 * thing you are looking at behaves; being unable to reach it from inside a record is exactly
 * backwards.
 *
 * Only PINNED groups come across, and that is the whole rule. They are the ones already declared
 * to live at the bottom of the sidebar below the flexible space (`NavGroup.pinned`), so they do not
 * compete with the record's sections for the top of the list — the daily-work groups stay behind,
 * because a record's sections ARE the navigation while you are in it.
 *
 * Returns an empty list when there is nothing focused, so the caller falls back to the global nav
 * exactly as before.
 */
export function focusedNavWithPinned(
	focus: SidebarFocus | null,
	globalNav: NavGroup[]
): NavGroup[] {
	const sections = focus ? focusNav(focus) : [];
	// A focus with a title but no sections keeps the global nav under the header — appending the
	// pinned groups to nothing would strand them as the entire sidebar.
	if (!sections.length) return [];
	return [...sections, ...globalNav.filter((g) => g.pinned)];
}

export function focusNav(focus: SidebarFocus): NavGroup[] {
	const sections = focus.sections ?? [];
	if (!sections.length) return [];
	return [
		{
			items: sections.map((s) => ({
				label: s.label,
				href: s.href,
				...(s.icon && FOCUS_ICONS[s.icon] ? { icon: FOCUS_ICONS[s.icon] } : {}),
				...(s.exact ? { exact: true } : {}),
				...(s.unread ? { unread: true } : {})
			}))
		}
	];
}
