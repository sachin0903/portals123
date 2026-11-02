/**
 * The account record's sections — the strip that used to be a `Tabs.Root` inside `AccountDetail`.
 *
 * Same shape as the client record's (`client-sections.ts`), and deliberately so: an account is
 * opened from a client and read the same way, so its chrome should not be a second idea. Each
 * section is a route, the SIDEBAR is the strip (`accounts/[uid]/+layout.server.ts` publishes
 * `sidebarFocus` and the root layout renders it), and icons travel as keys into `FOCUS_ICONS`
 * because `page.data` is serialised — a component reference cannot cross that boundary.
 *
 * The two maps below have to agree, which is why they share a file: `accountSections` is where a
 * reader can go, `accountTabTarget` is where a stale in-page tab link lands. A section in one and
 * not the other is a dead link, and `account-sections.test.ts` fails on it.
 */

import type { FocusSection, SidebarFocus } from './sidebar-focus';

/** What the sidebar needs to know about the account it is focused on. */
export interface AccountFocusInput {
	/** Mounted under a path other than the root — see `ClientFocusInput.prefix`. */
	prefix?: string;
	/** The account's `uid` — every section href is built from it. Deliberately not the serial
	 *  `platform.accounts.id`, which is the database's key and never appears in a URL. */
	uid: string;
	/** The account's display name — its `name`, falling back to its uid. */
	name: string;
	/** Shown under the title. The wrapper and the owner are what tell two of a client's accounts
	 *  apart, and the uid alone does not. */
	subtitle?: string | null;
	/** Where "up" goes. An account is reached from its owner far more often than from the list, so
	 *  the back link points at the client when there is one. */
	back?: { href: string; label: string };
}

/**
 * The record's sections, in sidebar order.
 *
 * Overview is the landing view and carries the account's details, bank, fees and regular movements
 * together — those four were one tab, and they are what an adviser opens an account to check.
 */
export function accountSections(input: AccountFocusInput): FocusSection[] {
	const base = `${input.prefix ?? ''}/accounts/${input.uid}`;
	return [
		{ label: 'Overview', href: base, icon: 'overview', exact: true },
		{ label: 'Holdings', href: `${base}/holdings`, icon: 'holdings' },
		{ label: 'Transactions', href: `${base}/transactions`, icon: 'transactions' }
	];
}

/** The whole focused-sidebar payload for an account: context header + sections. */
export function accountSidebarFocus(input: AccountFocusInput): SidebarFocus {
	return {
		eyebrow: 'Account',
		title: input.name,
		...(input.subtitle ? { subtitle: input.subtitle } : {}),
		back: input.back ?? { href: `${input.prefix ?? ''}/accounts`, label: 'Accounts' },
		sections: accountSections(input)
	};
}

/**
 * The old in-page tab values, and the route each became.
 *
 * The tabs were component state rather than URL state, so unlike the client record's `?tab=` links
 * there is nothing in the wild pointing here. The map exists anyway because the section list and the
 * route tree have to agree, and this is what the test compares them with — a section added to
 * `accountSections` with no route is a nav row that 404s.
 */
export const ACCOUNT_TAB_ROUTES: Record<string, string> = {
	overview: '',
	holdings: '/holdings',
	transactions: '/transactions'
};

/** Where an old tab name should end up. Anything unrecognised lands on the record itself — the
 *  reader asked for this account, and the worst outcome is the top of it. */
export function accountTabTarget(uid: string, tab: string | null | undefined): string {
	const key = (tab ?? '').trim().toLowerCase();
	const route = key in ACCOUNT_TAB_ROUTES ? ACCOUNT_TAB_ROUTES[key] : '';
	return `/accounts/${uid}${route}`;
}
