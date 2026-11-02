<script lang="ts">
	import '../app.css';
	import {
		AuthShell,
		SaaSShell,
		StreamedNotificationBell,
		Toaster,
		type NavGroup
	} from '@graphene/ui';
	import SearchBar from '$lib/SearchBar.svelte';
	import {
		dismissAllNotifications,
		dismissNotification,
		markNotification
	} from '$lib/notifications';
	import { buildGlobalNav, buildTenantNav } from '$lib/nav';
	import { firmContext, focusedNavWithPinned, parseFocus } from '$lib/record-focus';
	import { can } from '$lib/permissions';
	import { navigating, page } from '$app/state';
	import PageViewBeacon from '@graphene/ui/page-view-beacon';
	import RumBeacon from '@graphene/rum/beacon';
	import TableIcon from '@lucide/svelte/icons/table-2';
	import HomeIcon from '@lucide/svelte/icons/house';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import NetworkIcon from '@lucide/svelte/icons/network';
	import BoxesIcon from '@lucide/svelte/icons/boxes';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import TerminalIcon from '@lucide/svelte/icons/terminal';
	import RadioIcon from '@lucide/svelte/icons/radio';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import PencilIcon from '@lucide/svelte/icons/pencil';

	let { data, children } = $props();

	// Fallback heading for the platform drill-down: the sidebar renders before the dataset has
	// loaded, so the section heading must not depend on it.
	// A slug reads well enough as a heading until the platform's own metadata arrives with the
	// page data, so there is no name table here any more — it was one of seven copies of the same
	// registry, and the least useful: it existed only to fill the gap before the first load.
	const titleCase = (slug: string): string =>
		slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

	// The operator's operations permissions, which gate every nav row below (a superuser sees all).
	const permissions = $derived(data.permissions ?? []);
	const role = $derived(data.role ?? null);

	// Graphene's OWN white-label brand (the dark/light logo set edited on the Graphene tenant's
	// branding page), resolved server-side in hooks.server.ts. Only logos/icon/name — the admin
	// surface keeps the product look — and undefined ⇒ the shells' built-in Graphene mark.
	const brand = $derived(data.brand ?? undefined);

	// Where a click is already heading, or null when idle. `page.url` stays on the page being LEFT
	// for the whole of the destination's `load`, so every chrome affordance that can be resolved from
	// the URL alone follows this instead and moves on the click; the shell runs its progress bar and
	// its optimistic sidebar highlight off the same value. See @graphene/ui nav-progress.
	const pendingHref = $derived(navigating.to?.url.pathname ?? null);

	// Per-page browser-tab title, derived from the route. SvelteKit leaves document.title
	// untouched when a page renders no <title>, so a layout-level default stops the previous
	// page's title sticking across navigation. Detail pages override with their own <title>.
	const TITLE_LABELS: Record<string, string> = {
		'': 'Home',
		login: 'Sign in',
		mfa: 'Two-factor authentication',
		'set-password': 'Set your password',
		'forgot-password': 'Reset your password',
		verify: 'Verify your email',
		logout: 'Signing out'
	};
	const pageTitle = $derived.by(() => {
		const seg = (pendingHref ?? page.url.pathname).split('/')[1] ?? '';
		const label = TITLE_LABELS[seg] ?? (seg ? seg[0].toUpperCase() + seg.slice(1) : 'Home');
		// De-dupe when the section label already IS the suffix (e.g. /operations/* → just
		// "Operations", not "Operations · Operations").
		return label === 'Operations' ? label : `${label} · Operations`;
	});

	/**
	 * Routes that ARE the auth flow, which must never wear the app chrome.
	 *
	 * The shell used to be chosen on the session alone, so opening an emailed set-password link
	 * while already signed in rendered "Set your password" inside the full sidebar, search bar and
	 * notification bell — with the CURRENT user's name at the bottom, on a page that may be setting
	 * a password for somebody else entirely. A person following an invite is not navigating the
	 * app; they are being asked for a credential, and the chrome invites them to wander off
	 * mid-flow.
	 *
	 * Keyed on the first path segment, the same reading `pageTitle` already takes.
	 */
	const AUTH_ROUTES = new Set([
		'login',
		'logout',
		'mfa',
		'set-password',
		'forgot-password',
		'verify'
	]);
	// Shell ownership follows the page that is actually rendered, never the destination still
	// loading. Otherwise leaving /set-password wraps that still-visible auth form in the signed-in
	// user's chrome until navigation completes.
	const isAuthRoute = $derived(AUTH_ROUTES.has(page.url.pathname.split('/')[1] ?? ''));

	// The cross-tenant nav — shown until you drill into a tenant or a platform. Its groups and its
	// permission gates live in `$lib/nav.ts` as a pure, unit-tested function: the gate table IS the
	// security surface, and a lost or added gate is invisible to the type-checker.
	const globalNav = $derived<NavGroup[]>(buildGlobalNav({ permissions, role }));

	// Inside a tenant, the sidebar scopes to that firm. The pattern is deliberately NOT \d+:
	// tenant URLs carry the firm's uid (`ten_1743d0…`), and a digits-only match would leave the
	// whole firm sidebar unrendered.
	const tenantUid = $derived(page.url.pathname.match(/^\/tenants\/([^/]+)/)?.[1] ?? null);

	// Inside a platform, the sidebar drills into that platform's sections (same mechanism as
	// tenants; the /platforms index itself keeps the global nav). The heading comes from the
	// loaded dataset.
	//
	// The section is "Ingestion", one word for all three, because the PAGE is transport-shaped
	// rather than the label: SEI delivers files, Seccl pushes webhooks, Morgan Lloyd is polled.
	// A per-provider noun ("Streams" / "Feeds") was worse than it looks — SEI's live metadata said
	// "Streams", so a file-drop custodian got a nav item promising streams over a page saying it
	// had none.
	// `api` is a section of Platforms, not a platform: /platforms/api is the cross-provider call
	// log. Without the exclusion the drill-down nav renders for a platform that does not exist
	// (SvelteKit routes it correctly — this regex is the only thing that gets it wrong).
	const platformSlug = $derived(
		page.url.pathname.match(/^\/platforms\/(?!api(?:\/|$))([a-z-]+)/)?.[1] ?? null
	);
	// The firm console sits under a platform. `[^/]+` and not `\d+` or `[a-z-]+`: a tenant uid is
	// hex (`ten_df92e4bc…`), the same reason the sidebar's tenant match is loose — a stricter
	// pattern silently unrenders the whole section.
	const platformFirmUid = $derived(
		page.url.pathname.match(/^\/platforms\/[a-z-]+\/firms\/([^/]+)/)?.[1] ?? null
	);
	const platformMeta = $derived(
		(page.data.dataset as { meta?: { name?: string; streamsLabel?: string } } | null | undefined)
			?.meta
	);
	const platformName = $derived(platformMeta?.name ?? titleCase(platformSlug ?? ''));

	// Operate-as-firm renders a record (a client, an account) the way the platform does: its loader
	// publishes `sidebarFocus`, and the tenant nav gives way to that record's sections with the
	// pinned groups (Firm settings, Configure) kept below — the platform's own mechanism, imported
	// rather than copied, so the two sidebars cannot behave differently.
	// The operate banner used to sit above every page. It belongs to the chrome: whose firm this is,
	// and whether a click can change it. Only under `/operate` — the book tree has its own.
	const operateMode = $derived.by(() => {
		if (!/^\/tenants\/[^/]+\/operate(?:\/|$)/.test(page.url.pathname)) return null;
		const tenant = page.data.tenant as { name?: string } | undefined;
		const writing = page.data.writing;
		if (!tenant?.name || typeof writing !== 'boolean') return null;
		return { name: tenant.name, writing };
	});

	const focus = $derived(tenantUid ? parseFocus(page.data.sidebarFocus) : null);
	const tenantNav = $derived<NavGroup[]>(
		tenantUid ? buildTenantNav(tenantUid, { permissions, role }) : []
	);
	const focusedNav = $derived(focusedNavWithPinned(focus, tenantNav));

	// The firm the operator is inside, published by the `[uid]` layout for every route beneath it
	// (configure and operate alike). The sidebar header names it — the firm's own mark, the tenant
	// list as the way back — the way the platform's header names a focused client; a focused
	// record keeps its own header with the firm in the eyebrow. See `firmContext`.
	const firm = $derived.by(() => {
		if (!tenantUid) return null;
		const tenant = page.data.tenant as { name?: string } | undefined;
		if (!tenant?.name) return null;
		return {
			name: tenant.name,
			iconUrl: (page.data.tenantIconUrl as string | null | undefined) ?? null
		};
	});
	const context = $derived(firm ? firmContext(firm, focus) : undefined);

	const nav = $derived<NavGroup[]>(
		tenantUid
			? focusedNav.length
				? focusedNav
				: tenantNav
			: platformSlug
				? [
						{ items: [{ label: 'All platforms', href: '/platforms', icon: ChevronLeftIcon }] },
						{
							label: platformName ?? 'Platform',
							items: [
								{
									label: 'Overview',
									href: `/platforms/${platformSlug}`,
									icon: HomeIcon,
									exact: true
								},
								{ label: 'Models', href: `/platforms/${platformSlug}/models`, icon: BoxesIcon },
								{ label: 'Runs', href: `/platforms/${platformSlug}/runs`, icon: HistoryIcon },
								// The SQL console has its own slug (platforms:query, split out of
								// platforms:read — see docs/api.md "six splits"): observers keep the other
								// sections, only the console row needs the console permission.
								...(can(permissions, role, 'platforms:query')
									? [
											{
												label: 'Query',
												href: `/platforms/${platformSlug}/query`,
												icon: TerminalIcon
											}
										]
									: []),
								{
									label: 'Lineage',
									href: `/platforms/${platformSlug}/lineage`,
									icon: NetworkIcon
								},
								{
									label: 'Ingestion',
									href: `/platforms/${platformSlug}/ingestion`,
									icon: RadioIcon
								}
							]
						},
						// The firm console, when one is open. A second group rather than a nested
						// item: it is a different subject (this firm's records) from the sections
						// above (this platform's pipeline), and the entity submenu inside it is
						// data-driven, so it renders in the page rather than here.
						...(platformFirmUid
							? [
									{
										label: 'Firm',
										items: [
											{
												label: 'Platform data',
												href: `/platforms/${platformSlug}/firms/${platformFirmUid}`,
												icon: TableIcon,
												exact: true
											}
										]
									}
								]
							: [])
					]
				: globalNav
	);

	const breadcrumb = $derived.by(() => {
		// The destination names the crumb while a click is in flight, so the bar changes on the click
		// rather than when the load answers — but only for a path that IS a nav row, since anything
		// deeper takes its label from data that has not arrived yet.
		const path = pendingHref ?? page.url.pathname;
		// Inside a firm the rows are the firm's (the platform names the page from its own nav the
		// same way), so a tenant page gets its "Dashboard" / "Clients" crumb rather than none.
		const current = [...(tenantUid ? tenantNav : []), ...globalNav]
			.flatMap((g) => g.items)
			.find((i) => i.href === path);
		return current ? [{ label: current.label }] : [];
	});
</script>

<svelte:head><title>{pageTitle}</title></svelte:head>
<RumBeacon portal="admin" />
{#if data.user}<PageViewBeacon />{/if}

{#snippet notice()}
	{#if operateMode}
		<p
			class="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium {operateMode.writing
				? 'border-warning/60 bg-warning/10'
				: 'border-brand/40 bg-brand/5'}"
			title={operateMode.writing
				? 'You can make authorised changes for this firm. Every change is recorded under your name.'
				: "You can view authorised areas of this firm's workspace, but cannot make changes. Access is recorded under your name."}
		>
			{#if operateMode.writing}
				<PencilIcon class="size-3.5 shrink-0" />
				<span class="truncate">Acting on behalf of {operateMode.name}</span>
			{:else}
				<EyeIcon class="size-3.5 shrink-0" />
				<span class="truncate">Viewing {operateMode.name}'s portal (read-only)</span>
			{/if}
		</p>
	{/if}
{/snippet}

{#snippet search()}
	<SearchBar
		{permissions}
		{role}
		nav={tenantUid ? tenantNav : nav}
		userKey={data.user?.email ?? null}
	/>
{/snippet}

{#snippet actions()}
	<!-- The seed is STREAMED by the loader; the wrapper holds the bell's place until it lands. -->
	<StreamedNotificationBell
		items={data.notifications}
		unreadCount={data.notificationsUnread}
		feedHref="/notifications"
		countEndpoint="/notifications/unread-count"
		feedEndpoint="/notifications/recent"
		realtimeUrl="/notifications/events"
		onMarkRead={(uid) => markNotification(uid)}
		onMarkAllRead={() => markNotification()}
		onDismiss={(uid) => dismissNotification(uid)}
		onDismissAll={() => dismissAllNotifications()}
	/>
{/snippet}

{#if data.user && !isAuthRoute}
	<SaaSShell
		portal="Internal Operations"
		portalLabel
		user={data.user}
		{nav}
		{breadcrumb}
		pathname={page.url.pathname}
		{pendingHref}
		{notice}
		{search}
		{actions}
		{brand}
		{context}
	>
		<!-- No {#key section} + in:scale wrapper. It bought a 280ms page-enter animation and cost two
		     real bugs: the transform on the wrapper creates a new containing block, which breaks the
		     positioning of portalled popover content (every dropdown), and re-keying on the section
		     forced a full subtree remount so page content did not appear until a hard reload. -->
		{@render children()}
	</SaaSShell>
{:else}
	<!-- The explicit tagline overrides any brand tagline (none rides in anyway) — the login page
	     keeps its own line while the logo set follows the Graphene branding. -->
	<AuthShell {brand} tagline="Graphene Internal Operations.">
		{@render children()}
	</AuthShell>
{/if}

<Toaster />
