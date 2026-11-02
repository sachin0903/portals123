<script lang="ts">
	import '../app.css';
	import {
		AuthShell,
		ClientShell,
		CLIENT_HEADER_BUTTON_CLASS,
		CLIENT_HEADER_ICON_CLASS,
		NotificationBell,
		PAGE_ICONS,
		Toaster,
		ViewAsBanner,
		auditOverflow,
		auditTouchTargets,
		markOverflow,
		markTouchTargets,
		reportOverflow,
		reportTouchTargets,
		watchLayout,
		provideCardDesigns,
		type NavItem
	} from '@graphene/ui';
	import { dev } from '$app/environment';
	import SearchBar from '$lib/SearchBar.svelte';
	import ContactButton from '$lib/ContactButton.svelte';
	import {
		dismissAllNotifications,
		dismissNotification,
		markNotification
	} from '$lib/notifications';
	import { backFor, portalNav } from '$lib/nav';
	import { navigating, page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageViewBeacon from '@graphene/ui/page-view-beacon';
	import ViewAsBlockedDialog from '$lib/ViewAsBlockedDialog.svelte';
	import { guardWrites } from '$lib/view-as-guard.svelte';

	let { data, children } = $props();

	// The firm's card design per product type, for every surface that paints an account.
	provideCardDesigns(() => data.branding?.card_designs);

	// In a view-as session, a refused write should say why rather than surfacing as whatever
	// error handling the page it came from happens to have. Installed only for that session.
	$effect(() => {
		if (!data.viewAs) return;
		return guardWrites();
	});

	// The bar's measured height, published to the shell as `--shell-top` so its viewport-pinned
	// header glass starts under the bar rather than painting behind it.
	let bannerHeight = $state(0);

	// The account menu's client switcher (UserMenu, inside ClientShell) — only shown when the
	// login is linked to more than one crm.contacts row. Sets the active-contact cookie
	// server-side, then takes the caller to that client's Home — wherever they were when they
	// switched (e.g. mid-way through another client's Holdings) is that OTHER client's page, not
	// a place picking a new one should leave them.
	async function selectActiveContact(id: number): Promise<void> {
		const res = await fetch('/active-contact', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ contactId: id })
		});
		if (res.ok) await goto('/', { invalidateAll: true });
	}

	// Per-tenant white-label brand for the shells (logo + name); undefined ⇒ Graphene default.
	const brand = $derived({
		name: data.appName ?? undefined,
		logoUrl: data.branding?.logo_url ?? undefined,
		logoDarkUrl: data.branding?.logo_dark_url ?? undefined,
		iconUrl: data.branding?.icon_url ?? undefined,
		logoVariant: data.branding?.logo_variant ?? undefined,
		tagline: data.branding?.login?.tagline ?? undefined,
		legal: data.branding?.login?.legal ?? undefined
	});

	// Where a tap is already heading, or null when idle. `page.url` is still the page being LEFT for
	// the whole of the destination's `load`, so the tab bar's highlight follows this and moves on
	// the tap; ClientShell runs its progress bar off it too. The browser tab's title is set by the
	// root layout. See @graphene/ui nav-progress.
	const pendingHref = $derived(navigating.to?.url.pathname ?? null);

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
	 * Keyed on the first path segment.
	 */
	const AUTH_ROUTES = new Set([
		'login',
		'logout',
		'mfa',
		'set-password',
		'forgot-password',
		'verify',
		// The identity-photo link. No sign-in, and no portal chrome: it is a camera, not the app.
		'i'
	]);
	// Shell ownership follows the page that is actually rendered, never the destination still
	// loading. Otherwise leaving /set-password wraps that still-visible auth form in the signed-in
	// client's chrome until navigation completes.
	const isAuthRoute = $derived(AUTH_ROUTES.has(page.url.pathname.split('/')[1] ?? ''));

	// The layout tripwires (@graphene/ui overflow-audit + touch-audit): in development, every
	// layout change is checked for content spilling out of its box or clipped behind one, and
	// for a control whose hit area a thumb cannot rely on (under 44×44, or reaching into a
	// neighbour). An offender gets a dashed outline (app.css `[data-overflow]`, `[data-tap]`) and
	// a console line. Nothing in production — the walks read every element's computed style.
	$effect(() => {
		if (!dev) return;
		return watchLayout(document.body, {
			run(root) {
				const spills = auditOverflow(root);
				markOverflow(root, spills);
				reportOverflow(spills);
				const taps = auditTouchTargets(root);
				markTouchTargets(root, taps);
				reportTouchTargets(taps);
			},
			cleanup(root) {
				markOverflow(root, []);
				markTouchTargets(root, []);
			}
		});
	});

	// The tab bar derives from the tenant's pages set (null ⇒ today's default five — behaviour
	// for existing tenants is unchanged); icons resolve by name through the curated PAGE_ICONS,
	// falling back to the generic grid for an unknown or unset name. The "Onboarding" tab only
	// appears while the client has outstanding onboarding steps (resolved server-side in
	// +layout.server.ts), so a fully-onboarded client keeps the leaner bar.
	const nav: NavItem[] = $derived(
		portalNav(data.branding?.pages, Boolean(data.onboarding)).map((item) => ({
			label: item.label,
			href: item.href,
			icon: PAGE_ICONS[item.iconName] ?? PAGE_ICONS['layout-grid']
		}))
	);

	// The way back on a sub page (null on a tab page): the parent as the firm's page set names
	// it, or the page's own `data.back`. Follows the RENDERED page, like the shell ownership
	// above — the bar must not vanish for the destination while the current page is still up.
	const back = $derived(backFor(page.url.pathname, data.branding?.pages, page.data.back ?? null));

	// Keyed on the top-level section (first path segment), NOT the full path — so
	// nested layouts (e.g. /account) persist across their own sub-tabs instead of
	// re-mounting. Only the page body animates; the header and bottom tab bar live
	// in ClientShell, outside this node, so they never move or flicker.

	// A barely-there scale-up + fade on a sharp decelerate curve — content settling
	// into place rather than the generic slide. Kept short (expoOut front-loads the
	// motion) so navigation feels instant rather than animated. Off under reduced-motion.
</script>

<!-- Not under view-as: the page views are the client's own engagement record. -->
{#if data.user && !data.viewAs}<PageViewBeacon />{/if}

{#snippet search()}
	<SearchBar activeContactId={data.activeContactId ?? null} />
{/snippet}

{#snippet actions()}
	<!-- The bell marks notices read and the contact button opens a composer, and both of those
	     write. A member of staff looking at this portal read-only would only find out by being
	     refused, so the chrome that cannot work is not offered. The API is what enforces it. -->
	{#if data.viewAs}
		<!-- Nothing: every control here acts on the client's account. -->
	{:else}
		{#if !data.onboarding}
			<NotificationBell
				items={data.notifications ?? []}
				unreadCount={data.notificationsUnread ?? 0}
				feedHref="/notifications"
				countEndpoint={data.activeContactId
					? `/notifications/unread-count?contact_id=${data.activeContactId}`
					: '/notifications/unread-count'}
				feedEndpoint={data.activeContactId
					? `/notifications/recent?contact_id=${data.activeContactId}`
					: '/notifications/recent'}
				realtimeUrl={data.activeContactId
					? `/notifications/events?contact_id=${data.activeContactId}`
					: '/notifications/events'}
				onMarkRead={(uid) => markNotification(uid)}
				onMarkAllRead={() => markNotification(undefined, data.activeContactId)}
				onDismiss={(uid) => dismissNotification(uid)}
				onDismissAll={() => dismissAllNotifications(data.activeContactId)}
				triggerClass={CLIENT_HEADER_BUTTON_CLASS}
				iconClass={CLIENT_HEADER_ICON_CLASS}
			/>
		{/if}
		<!-- No theme toggle here: the switcher lives on /profile (Appearance). A colour-scheme
		     control is a setting, not an action a client takes on every visit, and the phone's top
		     bar was carrying five icons. -->
		<ContactButton />
	{/if}
{/snippet}

{#snippet shell(heightClass: string)}
	<ClientShell
		user={data.user!}
		{nav}
		pathname={page.url.pathname}
		{pendingHref}
		{back}
		immersive={page.data.immersive === true}
		{search}
		{actions}
		{brand}
		{heightClass}
		contacts={data.contacts ?? []}
		activeContactId={data.activeContactId ?? null}
		onSelectContact={selectActiveContact}
	>
		<!-- No {#key section} + in:scale wrapper. It bought a 280ms page-enter animation and cost two
		     real bugs: the transform on the wrapper creates a new containing block, which breaks the
		     positioning of portalled popover content (every dropdown), and re-keying on the section
		     forced a full subtree remount so page content did not appear until a hard reload. -->
		{@render children()}
	</ClientShell>
{/snippet}

{#if data.user && !isAuthRoute && data.viewAs}
	<!-- A member of staff is looking at this client's portal. The bar takes the top of the window
	     and the shell — which normally owns the whole viewport (`h-dvh`) — fills what is left, so
	     its own scroll area, topbar and tab bar all start under the notice. The dialog beside it
	     explains the API's refusal if they try to act anyway — see view-as-guard. -->
	<div class="flex h-dvh flex-col" style="--shell-top: {bannerHeight}px">
		<div bind:clientHeight={bannerHeight} class="shrink-0">
			<ViewAsBanner
				contactName={data.viewAs.contactName}
				secondsRemaining={data.viewAs.secondsRemaining}
			/>
		</div>
		{@render shell('min-h-0 flex-1')}
	</div>
	<ViewAsBlockedDialog />
{:else if data.user && !isAuthRoute}
	{@render shell('h-dvh')}
{:else}
	<AuthShell {brand}>
		{@render children()}
	</AuthShell>
{/if}

<!-- top-center: the mobile bottom tab bar owns the bottom edge. -->
<Toaster position="top-center" />
