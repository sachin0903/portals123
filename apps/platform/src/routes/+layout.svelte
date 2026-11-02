<script lang="ts">
	import '../app.css';
	import {
		AuthShell,
		SaaSShell,
		setPageBack,
		StreamedNotificationBell,
		Toaster
	} from '@graphene/ui';
	import { can } from '@graphene/api-client/permissions';
	import { untrack } from 'svelte';
	import SearchBar from '$lib/SearchBar.svelte';
	import {
		dismissAllNotifications,
		dismissNotification,
		isOnScreen,
		markNotification
	} from '$lib/notifications';
	import { navigating, page } from '$app/state';
	import { afterNavigate, beforeNavigate } from '$app/navigation';
	import PageViewBeacon from '@graphene/ui/page-view-beacon';
	import RumBeacon from '@graphene/rum/beacon';
	import { buildNav, focusContext, focusedNavWithPinned, parseFocus } from '$lib/nav';
	import { sectionBreadcrumb, withDocumentsGate } from '$lib/breadcrumb';
	import { resolveBack, type BackEntry } from '$lib/back-stack';
	import { backTop, clearBackStack, commitNavigation } from '$lib/back-stack.svelte';

	let { data, children } = $props();

	// Needed early to gate the messaging stream below — `permissions`/`role` proper (used by the
	// nav) are derived further down, but a `const` can't be read before that point in script order.
	const canReadMessages = $derived(can(data.permissions ?? [], data.role ?? null, 'messages:read'));

	// A record page can focus the chrome on itself (as the admin portal does for a tenant) by
	// returning `sidebarFocus` from its load — see the full comment further down by `focusedNav`.
	// Read here too because the focused record's own Messages dot (`liveUnreadContactId`) is kept
	// live by the same mechanism as the global one, right below.
	const focus = $derived(parseFocus(page.data.sidebarFocus));
	// Its own primitive, not read off `focus` directly, so effects depending on it below only fire
	// when the CONTACT actually changes, not on every navigation within the same client record
	// (`page.data` — and so `focus` — is a new object each time SvelteKit re-runs any load).
	const liveUnreadContactId = $derived(focus?.liveUnreadContactId ?? null);

	// The Messages dots: the global one (does the signed-in user have anything unread, anywhere)
	// and, when a client record is focused, that record's OWN. Both are seeded once by the loader,
	// then kept fresh purely by PUSH — no fallback poll. A boolean check every 30s on every open
	// tab, whether or not anything had changed, was judged worse than the accepted trade-off: either
	// dot can go stale for as long as a silently dropped `EventSource` takes to reconnect (native
	// `EventSource` retries on its own; there is no code-level backstop for a connection that never
	// comes back).
	//
	// The seed is STREAMED (`+layout.server.ts`): the loader returns it as a promise so the shell's
	// HTML is not held back by the round trip, and it lands just after hydration. Read ONCE, as the
	// synchronous seed was — the loader re-runs on every navigation (it reads `url.pathname` for the
	// terms gate) and the dot is push-kept from here on, so later loads are not re-applied. A push
	// that answers before the seed lands is the fresher answer and wins (`chatsUnreadPushed`).
	let chatsUnread = $state(false);
	let chatsUnreadPushed = false;
	$effect(() => {
		const seed = untrack(() => data.chatsUnread);
		let live = true;
		void Promise.resolve(seed).then(
			(unread) => {
				if (live && !chatsUnreadPushed && typeof unread === 'boolean') chatsUnread = unread;
			},
			() => {
				// The loader never rejects this (it fails open to `false`); nothing to do but keep "no dot".
			}
		);
		return () => {
			live = false;
		};
	});
	// Tagged with the contact it answers for, not a bare boolean: two checks can be in flight at
	// once (a slow one for the client you just left, a fresh one for the client you landed on) and
	// they can resolve out of order. A bare boolean has no way to reject the stale answer if it
	// lands second; comparing the tag against `liveUnreadContactId` at the READ site means a late
	// answer for a client that isn't focused anymore is simply ignored, however late it arrives.
	let clientChatsUnread = $state<{ contactId: string; unread: boolean } | null>(null);

	async function refetchChatsUnread() {
		try {
			const res = await fetch('/chats/unread', { headers: { accept: 'application/json' } });
			if (!res.ok) return;
			const body = (await res.json()) as { unread?: boolean };
			if (typeof body.unread === 'boolean') {
				chatsUnread = body.unread;
				chatsUnreadPushed = true;
			}
		} catch {
			// Leaves the dot as it was — the next push or reconnect recovers it.
		}
	}

	async function refetchClientChatsUnread() {
		const contactId = liveUnreadContactId;
		if (!contactId) return;
		try {
			const res = await fetch(`/chats/unread?contact_id=${encodeURIComponent(contactId)}`, {
				headers: { accept: 'application/json' }
			});
			if (!res.ok) return;
			const body = (await res.json()) as { unread?: boolean };
			if (typeof body.unread === 'boolean') clientChatsUnread = { contactId, unread: body.unread };
		} catch {
			// Same as above.
		}
	}

	// A new (or absent) focused contact: drop back to trusting the record's own SSR-seeded value
	// until this runs once. There is no poll left to catch up on its own, so a freshly-opened
	// client record gets one explicit check rather than waiting on the next tenant-wide push.
	$effect(() => {
		clientChatsUnread = null;
		if (liveUnreadContactId) void refetchClientChatsUnread();
	});

	// The realtime stream — the ONLY thing keeping either dot fresh now (same trade-off as the
	// notification bell's own stream, `pkgs/ui`). `/messages/events` subscribes at the TENANT
	// level (`live_push.staff_key`) — every staff member on a tenant shares one key — so any
	// message anywhere is reason enough to recheck both dots; that's cheaper than working out which
	// conversation it was for, and each recheck is a single boolean GET.
	//
	// Gated on `messages:read`: unlike the boolean-only `/chats/unread` checks above, this stream
	// carries full message content (sender, text — whatever `client-chat.svelte`'s own connection
	// renders). Today that only ever reaches a browser that navigated to a page gated on
	// `messages:read` in the first place (`/chats`, a client's `/chat`); opening it unconditionally
	// here, in a layout every signed-in user mounts, would hand that same content to someone who
	// could never otherwise reach it — never rendered, since nothing here reads `ev.data`, but
	// delivered into their browser regardless, which is exposure this dot has no reason to cause.
	$effect(() => {
		if (typeof document === 'undefined' || !canReadMessages) return;
		const onUnreadChanged = () => {
			void refetchChatsUnread();
			void refetchClientChatsUnread();
		};
		window.addEventListener('chats:unread-changed', onUnreadChanged);
		const events = new EventSource('/messages/events');
		const onPush = () => {
			void refetchChatsUnread();
			void refetchClientChatsUnread();
		};
		// 'open' fires on the initial connect AND every reconnect. The FIRST one is NOT a reason to
		// refetch: the loader has just streamed the global dot's value and the focused client's own is
		// fetched by its effect above, so re-asking on connect was two duplicate round trips paid on
		// every page load — the work "settle" waited on. A RECONNECT still refetches, because that is
		// what closes the gap a dropped connection leaves, standing in for what the poll used to
		// guarantee.
		let connected = false;
		events.addEventListener('open', () => {
			if (connected) onPush();
			connected = true;
		});
		events.addEventListener('message', onPush);
		return () => {
			window.removeEventListener('chats:unread-changed', onUnreadChanged);
			events.close();
		};
	});

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

	// Where a click is already heading, or null when idle. SvelteKit renders the page you are LEAVING
	// for the whole of the destination's `load`, so `page.url` — and everything derived from it — is
	// the old page until the API answers: a sidebar click bought no visible change at all for the best
	// part of a second. The chrome that can honestly be resolved from the URL alone (the sidebar
	// highlight, the tab title) follows this instead, so it moves on the click; the shell also runs
	// its progress bar off it. See @graphene/ui nav-progress.
	//
	// The back control deliberately does NOT: it names the page you came FROM, which is still the
	// page on screen for the whole of the destination's load, so moving it early would be a lie for
	// exactly as long as the old page is still being shown.
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
		logout: 'Signing out',
		branding: 'Studio'
	};
	// White-label brand for the tab suffix (mirrors the shell header on this layout); Graphene default.
	const brandName = $derived(data.appName ?? 'Graphene');
	const pageTitle = $derived.by(() => {
		const seg = (pendingHref ?? page.url.pathname).split('/')[1] ?? '';
		const label = TITLE_LABELS[seg] ?? (seg ? seg[0].toUpperCase() + seg.slice(1) : 'Home');
		return `${label} · ${brandName}`;
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
	 * Keyed on the first path segment, the same reading `pageTitle` above already takes.
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

	// The sidebar IA — Dashboard, Investments / Pensions / Work, Firm settings (pinned, solo) — and the permission gate on every item
	// live in `$lib/nav.ts`, which is pure and unit-tested: a gate lost or gained while moving a row
	// is a 403 trap or a silently missing feature, and neither shows up in a type-check.
	const permissions = $derived(data.permissions ?? []);
	const role = $derived(data.role ?? null);
	const globalNav = $derived(buildNav({ permissions, role, chatsUnread }));

	// A record page can focus the chrome on itself (as the admin portal does for a tenant) by
	// returning `sidebarFocus` from its load: the global nav gives way to that record's own sections
	// and the sidebar grows a context header naming it (`focus`/`liveUnreadContactId` are derived
	// near the top of this file, alongside the dots they feed). No focus published ⇒ the global nav,
	// so this is inert on every page that doesn't ask for it; a focus with a title but no sections
	// keeps the global nav under the header rather than leaving the sidebar with nothing to navigate.
	//
	// The record's sections come with the PINNED groups still attached — Firm settings, which used
	// to disappear the moment you opened a client or an account. See `focusedNavWithPinned`.

	// `focus`, but with its live-tracked section's dot swapped for the pushed value once one has
	// landed FOR THE CURRENTLY FOCUSED CONTACT — the tag check is what a bare boolean couldn't do:
	// reject a slow answer that arrived after navigating to a different client (see
	// `refetchClientChatsUnread`'s comment). This is the only thing that reads `clientChatsUnread`;
	// `focus` itself stays the direct, unmodified read of `page.data.sidebarFocus`.
	const effectiveFocus = $derived.by(() => {
		if (!focus || !clientChatsUnread || clientChatsUnread.contactId !== liveUnreadContactId) {
			return focus;
		}
		const liveUnread = clientChatsUnread.unread;
		return {
			...focus,
			sections: focus.sections?.map((s) => (s.icon === 'chat' ? { ...s, unread: liveUnread } : s))
		};
	});
	const focusedNav = $derived(focusedNavWithPinned(effectiveFocus, globalNav));
	const nav = $derived(focusedNav.length ? focusedNav : globalNav);
	const context = $derived(effectiveFocus ? focusContext(effectiveFocus) : undefined);

	// ── Where "Back" goes ───────────────────────────────────────────────────────────────────────────
	//
	// Every back link used to be the route's declared PARENT, and so did the only link in the
	// topbar's trail. That answers "where does this page sit", which is worth saying — but it is not
	// the question a reader has on a record, and the two differ the moment a record is reachable from
	// more than one place: a task opened from the dashboard's action queue offered "Tasks", a page
	// they had never been on. So the trail STAYS and a back arrow joins it, fed by the trail you
	// actually walked ($lib/back-stack). Map and history, side by side.

	// The name of the page you are ON. Deliberately NOT the old `breadcrumb` derived, which
	// short-circuited on `pendingHref` to name the DESTINATION the instant a nav row was clicked:
	// right for chrome that should move on the click, and exactly wrong here, because this is what
	// gets RECORDED as a back target and so must describe the page being left. The nav fallback reads
	// the GLOBAL nav, not the focused one — a focused sidebar's items are that record's sections.
	const hereLabel = $derived.by(() => {
		const crumbs = sectionBreadcrumb(page.url.pathname, page.route.id, page.data);
		if (crumbs?.length) return crumbs[crumbs.length - 1].label;
		// The dashboard is the case in the bug report: `sectionBreadcrumb('/')` returns null and the
		// nav names it "Dashboard", so the control reads "Back to Dashboard" with no change there.
		const item = globalNav.flatMap((g) => g.items).find((i) => i.href === page.url.pathname);
		return item?.label ?? null;
	});

	// What gets recorded if we leave. The query is kept, so going back restores the filters and page
	// number you left behind; `advance` compares on the pathname alone, so changing them records
	// nothing. A page `hereLabel` cannot name (a 404, /notifications) is skipped rather than recorded
	// as "Untitled" — the hop is dropped and Back still points at the page before it.
	const hereEntry = (): BackEntry | null =>
		hereLabel ? { href: page.url.pathname + page.url.search, label: hereLabel } : null;

	// Auth screens can never be a back target — "Back to Sign in" is at best confusing and at worst a
	// 403. Same segments as TITLE_LABELS above; keep the two in step.
	const AUTH_SEGMENTS = new Set([
		'login',
		'logout',
		'mfa',
		'set-password',
		'forgot-password',
		'verify'
	]);

	// Recorded on the way out, committed only on arrival — because `beforeNavigate` also fires for
	// navigations that never happen. `content/[uid]/edit` and `forms/[uid]/edit` both call
	// `nav.cancel()` on an unsaved-changes guard, and THIS layout's callback registers before theirs
	// (a layout initialises before its page), so recording and committing in one step would leave the
	// trail holding a page the user is still standing on.
	let leaving: BackEntry | null = null;
	beforeNavigate((nav) => {
		// `type: 'leave'` (closing the tab, an external link) has no `to` — nothing to record.
		leaving = nav.to ? hereEntry() : null;
	});
	afterNavigate((nav) => {
		const to = nav.to?.url.pathname;
		if (!to) return;
		const seg = to.split('/')[1] ?? '';
		// A first load has nothing behind it, and a session boundary invalidates everything before it.
		if (nav.type === 'enter' || !data.user || AUTH_SEGMENTS.has(seg)) clearBackStack();
		else commitNavigation(leaving, to);
		leaving = null;
	});

	// THE one source. Everything that renders a back control reads this, so the topbar and the page
	// header can never disagree — two controls resolving the same question independently is this
	// same bug in a second costume.
	const backTarget = $derived(resolveBack(backTop(), page.url));

	// The trail, unchanged — it says where this page SITS, which is a different question from where
	// the reader came from, and the topbar renders both. A destination that IS a nav row is named
	// from the nav the instant it is clicked; anything deeper keeps the trail it has, because
	// `sectionBreadcrumb` reads `page.data`, which still describes the page being left, and naming an
	// unloaded record would be a guess rather than a preview.
	const breadcrumb = $derived.by(() => {
		const heading = pendingHref
			? globalNav.flatMap((g) => g.items).find((i) => i.href === pendingHref)
			: undefined;
		if (heading) return [{ label: heading.label }];
		const crumbs = sectionBreadcrumb(page.url.pathname, page.route.id, page.data);
		if (crumbs) return withDocumentsGate(crumbs, can(permissions, role, 'documents:show'));
		const current = globalNav.flatMap((g) => g.items).find((i) => i.href === page.url.pathname);
		return current ? [{ label: current.label }] : [];
	});

	// Published unconditionally — `setContext` is only legal during init, so this cannot move inside
	// the `{#if data.user}` branch below. A getter, not a value: context is set once and the getter
	// is what carries the reactivity across the boundary.
	setPageBack({
		get current() {
			return backTarget;
		}
	});

	// A focused record (a client, an account) ALSO shows a back row in its sidebar, and the two
	// coexisting is intended. That one is an UP link — structural, resolved by the record's loader,
	// and `accounts/[uid]/+layout.server.ts` points it at the OWNER precisely because the accounts
	// list is a way in rather than the place you came from. This one is temporal. Same word, two
	// questions.
</script>

<svelte:head><title>{pageTitle}</title></svelte:head>
<RumBeacon portal="platform" />
{#if data.user}<PageViewBeacon />{/if}

{#snippet search()}
	<SearchBar nav={globalNav} {permissions} {role} userKey={data.user?.email ?? null} />
{/snippet}

{#snippet actions()}
	<!-- The seed is STREAMED by the loader (`+layout.server.ts`), so this is the wrapper that holds the
	     bell's place until it lands and then mounts the live bell with it — see its own comment for
	     why the bell cannot simply be handed a promise. -->
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
		showing={isOnScreen}
	/>
{/snippet}

{#if data.user && !isAuthRoute}
	<SaaSShell
		portal="Platform"
		user={data.user}
		{nav}
		back={backTarget ?? undefined}
		{breadcrumb}
		pathname={page.url.pathname}
		{pendingHref}
		{brand}
		{search}
		{actions}
		{context}
	>
		<!-- No {#key section} + in:scale wrapper. It bought a 280ms page-enter animation and cost two
		     real bugs: the transform on the wrapper creates a new containing block, which breaks the
		     positioning of portalled popover content (every dropdown in the app), and re-keying on the
		     section forced a full subtree remount so page content did not appear until a hard reload. -->
		{@render children()}
	</SaaSShell>
{:else}
	<AuthShell
		{brand}
		tagline={brand.tagline ?? 'The adviser, compliance and paraplanning workspace.'}
	>
		{@render children()}
	</AuthShell>
{/if}

<Toaster />
