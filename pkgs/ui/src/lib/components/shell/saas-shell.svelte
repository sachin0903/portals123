<script lang="ts">
	import type { Snippet } from 'svelte';
	import * as Sidebar from '../ui/sidebar/index.js';
	import AppSidebar from './app-sidebar.svelte';
	import NavProgress from './nav-progress.svelte';
	import SaasTopbar from './saas-topbar.svelte';
	import type { BackLink, Brand, Crumb, NavContext, NavGroup, NavItem, Tenant } from './types.js';

	let {
		portal,
		portalLabel = false,
		user,
		nav,
		pathname = '',
		pendingHref = null,
		context,
		back,
		breadcrumb = [],
		tenants,
		activeTenantId,
		notice,
		search,
		actions,
		brand,
		menuItems = [],
		interactive = true,
		children
	}: {
		portal: string;
		/** Print the portal name under the lockup too. See app-sidebar — off by default, on for the
		 *  admin portal, where it is the only thing that distinguishes it from a firm's platform. */
		portalLabel?: boolean;
		user: { email: string };
		nav: NavGroup[];
		pathname?: string;
		/**
		 * Where a click is already heading (`navigating.to?.url.pathname`), or null when idle. Drives
		 * every piece of navigation feedback in the shell: the progress bar, the optimistic sidebar
		 * highlight and the dimming of the page being left. See nav-progress for why any of it exists.
		 */
		pendingHref?: string | null;
		/**
		 * Focused-record header above the nav. Pass it when the whole sidebar is scoped to one record —
		 * a client, a tenant — so the shell reads as that record's dashboard rather than the firm's.
		 */
		context?: NavContext;
		/** A single origin-aware back control in the topbar, shown instead of the trail. See
		 *  saas-topbar — an app that passes none keeps its breadcrumb. */
		back?: BackLink;
		breadcrumb?: Crumb[];
		/** Admin cross-tenant switcher data. */
		tenants?: Tenant[];
		activeTenantId?: string;
		/** Optional status rendered in the topbar, between the page name and the utilities. */
		notice?: Snippet;
		/** Optional search affordance rendered in the topbar. */
		search?: Snippet;
		/** Optional header actions rendered in the topbar's right cluster (e.g. the notification
		 *  bell), between search and the tenant switcher. Mirrors client-shell's `actions` slot. */
		actions?: Snippet;
		/** Per-tenant white-label brand (logo + name). Unset ⇒ Graphene default. */
		brand?: Brand;
		/** Extra links shown in the user popout (alongside Account + Sign out). */
		menuItems?: NavItem[];
		/**
		 * `false` ⇒ this shell is a PICTURE of the app (the branding editor renders the real shell to
		 * preview a firm's theme). Every control that writes outside its own subtree — the theme
		 * toggle, the rail toggle's cookie, the ⌘B shortcut, the root `--content-inset-left`, the
		 * sign-out form — is replaced by a static stand-in or switched off. See saas-topbar.
		 */
		interactive?: boolean;
		children?: Snippet;
	} = $props();

	// A preview is a PICTURE of the app: it never navigates, so it must never show navigation
	// feedback either (the same rule as the theme toggle and the rail cookie).
	const pending = $derived(interactive && pendingHref !== null);
	// A same-path navigation is a filter or a page cut, not a page change — the listing keeps its own
	// rows on screen and dimming the whole canvas on every debounced keystroke reads as a fault. Only
	// a genuine change of page dims what is being left behind.
	const leaving = $derived(pending && pendingHref !== pathname);
</script>

<!--
  `--topbar-h` is declared HERE, once, and consumed by both bars (saas-topbar's `<header>` and the
  sidebar header, which must match it exactly) and by any page doing full-height maths inside the
  inset — e.g. the document/template/flow editors, which want `h-[calc(100svh-var(--topbar-h,4rem))]`
  instead of hardcoding the number. Three of them hardcoded `3.5rem` and overflowed by 8px the moment
  the bar grew to 4rem; a token is what stops that recurring. Always consume it WITH the `4rem`
  fallback: the mobile sidebar renders in a body-portaled sheet, outside this element's inheritance.
-->
<Sidebar.Provider style="--topbar-h: 4rem;" preview={!interactive}>
	<NavProgress active={pending} />
	<AppSidebar
		{portal}
		{portalLabel}
		{user}
		{nav}
		{pathname}
		{pendingHref}
		{brand}
		{context}
		{menuItems}
		{interactive}
	/>
	<Sidebar.Inset>
		<SaasTopbar
			{back}
			{breadcrumb}
			{tenants}
			{activeTenantId}
			{notice}
			{search}
			{actions}
			{interactive}
		/>
		<!-- The dim is applied as STYLE DIRECTIVES rather than a class: a scoped style whose selector
		     is hidden inside `cn()` gets pruned, and the 80ms transition delay means a navigation that
		     resolves quickly never visibly dims at all — the bar alone carries those. Both directives
		     drop together when the navigation settles, so the page comes back instantly rather than
		     fading in. -->
		<div
			class="glass-canvas min-w-0 flex-1 p-4 md:p-6"
			aria-busy={leaving ? 'true' : undefined}
			style:opacity={leaving ? '0.6' : undefined}
			style:transition={leaving ? 'opacity 150ms ease 80ms' : undefined}
		>
			{@render children?.()}
		</div>
	</Sidebar.Inset>
</Sidebar.Provider>
