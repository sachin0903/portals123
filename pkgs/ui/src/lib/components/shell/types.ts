import type { Component } from 'svelte';

/** A single sidebar navigation entry. `icon` is a lucide component (optional). */
export interface NavItem {
	label: string;
	href: string;
	icon?: Component;
	/**
	 * Optional visual subsection inside a larger group. The heading renders immediately before the
	 * first visible item carrying a new value, so permission-gated items cannot leave an empty
	 * heading behind. This is presentation only: active matching, persistence and disclosure still
	 * belong to the parent `NavGroup`.
	 */
	section?: string;
	/** Active only on an exact path match (not prefix) — e.g. a section index like
	 * `/tenants/1` that shouldn't stay highlighted on `/tenants/1/users`. */
	exact?: boolean;
	/** Shows a small unread dot on the item — deliberately just a dot, never a count, since a
	 * nav row has no room for one and (unlike the bell) there's no single feed to read from to
	 * clear it. The caller decides what "unread" means for its own item. */
	unread?: boolean;
}

/** A labelled group of nav items in the sidebar. */
export interface NavGroup {
	label?: string;
	items: NavItem[];
	/** Renders as a collapsible disclosure. Omit for a plain always-open group. */
	collapsible?: boolean;
	/** Initial open state when collapsible. Default true. */
	defaultOpen?: boolean;
	/** Pin to the bottom of the sidebar, below the flexible space. */
	pinned?: boolean;
	/**
	 * While this group is open, the other collapsible groups show closed — and they come back as the
	 * user left them when it closes.
	 *
	 * For a long configuration group pinned to the bottom (Firm settings, Configure): opening it used
	 * to push the daily-work groups off the top of a short viewport, so you scrolled a sidebar to
	 * reach the nav. That suppression is DERIVED, not written — the daily groups keep their own
	 * remembered state, which is why closing this one restores them rather than expanding everything.
	 * Two `solo` groups are exclusive of each other: opening one closes the other, and that close IS
	 * written, so the one you left stays shut.
	 */
	solo?: boolean;
}

/** A single "go back" control: where it points and what it is called.
 *
 * The label is a bare NAME — "Tasks", "Dashboard", "Mr Max Oakden" — never a sentence. Each surface
 * words it itself: the topbar renders "Back to {label}", `PageHeader` renders it alone after a
 * chevron. A label that already said "Back to …" would read "Back to Back to Tasks" in the bar.
 */
export interface BackLink {
	href: string;
	label: string;
}

/** Optional context header shown ABOVE the nav when the sidebar is focused on one record. */
export interface NavContext {
	eyebrow?: string; // e.g. 'CLIENT'
	title: string; // e.g. 'Mr Max Oakden'
	subtitle?: string; // e.g. 'max.oakden@example.com'
	/** The record's structural parent — an UP link, resolved by the record's loader. Distinct from
	 *  the topbar's back control, which is temporal (where you actually came from). See
	 *  `accounts/[uid]/+layout.server.ts`, which points Up at the OWNER rather than the list. */
	back?: BackLink;
	/**
	 * A picture of the thing focused — a firm's brand icon, for the admin portal drilled into a
	 * tenant. Drawn as the tile beside the title and as the rail tile in place of the monogram.
	 * People get no picture here (the record page draws their avatar); firms do, because a firm's
	 * mark is how its own staff recognise it and a monogram of "Acme Wealth" is not.
	 */
	image?: string;
	/** A glyph for the tile when there is no `image` — e.g. a building for a firm with no icon set.
	 *  Without either, the tile falls back to the title's monogram. */
	icon?: Component;
}

/** A tenant (adviser firm) for the admin cross-tenant switcher. */
export interface Tenant {
	id: string;
	name: string;
}

/** A breadcrumb crumb; omit `href` for the current page. */
export interface Crumb {
	label: string;
	href?: string;
}

/** Per-tenant white-label brand for the app shells. All optional — unset ⇒ Graphene default. */
export interface Brand {
	/** Wordmark / firm name. */
	name?: string;
	/** Logo image URL (replaces the built-in mark when set). */
	logoUrl?: string;
	/** Optional dark-mode logo variant. */
	logoDarkUrl?: string;
	/** Optional icon/symbol image — used in the `icon` logo variant (falls back to the logo). */
	iconUrl?: string;
	/** Top-left logo treatment: `icon` = mark only; `wordmark` = full lockup (no appended name). */
	logoVariant?: 'icon' | 'wordmark';
	/** Login-page tagline + regulatory/legal line (auth shell). */
	tagline?: string;
	legal?: string;
}
