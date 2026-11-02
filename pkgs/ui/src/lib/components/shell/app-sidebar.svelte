<script lang="ts">
	import { untrack } from 'svelte';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import * as Collapsible from '../ui/collapsible/index.js';
	import * as Sidebar from '../ui/sidebar/index.js';
	import Logo from '../brand/logo.svelte';
	import SidebarContext from './sidebar-context.svelte';
	import SidebarUser from './sidebar-user.svelte';
	import {
		applyGroupOpen,
		groupHoldsHref,
		groupKey,
		initials,
		isGroupOpen,
		railRuleKeys,
		navStateKey,
		readNavState,
		soloGroupOpen
	} from './nav-groups.js';
	import type { Brand, NavContext, NavGroup, NavItem } from './types.js';

	let {
		portal,
		portalLabel = false,
		user,
		nav,
		pathname = '',
		pendingHref = null,
		brand,
		context,
		menuItems = [],
		interactive = true
	}: {
		portal: string;
		/**
		 * Print `portal` under the lockup as well as announcing it.
		 *
		 * OFF by default. On the platform portal it said "Platform" beneath a Graphene wordmark in a
		 * firm's own portal — a word the reader already knows, bought at the price of a two-line
		 * header that keeps the logo at 24px. It stays available for the surface where the string
		 * carries information the lockup cannot: the admin portal wears the operations tenant's own
		 * logo, so "Internal Operations" is the only thing distinguishing it from a firm's platform.
		 * The `aria-label` names the portal either way, so nothing is lost to a screen reader.
		 */
		portalLabel?: boolean;
		user: { email: string; name?: string };
		nav: NavGroup[];
		/** Current path (apps pass `$app/state` page.url.pathname) for active highlighting. */
		pathname?: string;
		/**
		 * Where a click is already heading — apps pass `$app/state` `navigating.to?.url.pathname`;
		 * null when idle.
		 *
		 * `pathname` does not change until the destination's `load` has resolved, so on its own the
		 * highlight moves a second AFTER the click on any surface that reads the API. The destination
		 * row lights up on the click instead, and wears a spinner until its page arrives.
		 */
		pendingHref?: string | null;
		/** Per-tenant white-label brand (logo + name). Unset ⇒ Graphene default. */
		brand?: Brand;
		/** Focused-record header above the nav (a client, a tenant). Unset ⇒ the global sidebar. */
		context?: NavContext;
		/** Extra links shown in the user popout, alongside Account + Sign out. */
		menuItems?: NavItem[];
		/** `false` ⇒ rendered inside the branding preview: no live chrome (see saas-shell). */
		interactive?: boolean;
	} = $props();

	const sidebar = Sidebar.useSidebar();

	// The 56px icon rail — desktop only. On mobile the sidebar is a full-width portaled sheet, and
	// `sidebar.state` still reports the DESKTOP rail state there, so a mobile sheet would otherwise be
	// treated as a rail and lose every label.
	const railMode = $derived(sidebar.state === 'collapsed' && !sidebar.isMobile);

	const groups = $derived(nav.map((group, index) => ({ group, key: groupKey(group, index) })));

	// ── Remembering which groups are open ──────────────────────────────────────────────────────────
	// Across navigations: free — this component lives in each app's root layout, so `$state` survives
	// every client-side navigation.
	//
	// Across RELOADS: persisted in localStorage, keyed per portal. Staff collapse Firm once and expect
	// it to stay collapsed; re-expanding it on every hard refresh makes the disclosure feel broken.
	// localStorage rather than the `sidebar_state` cookie because this is a map, not a flag, and no
	// server-side code needs it — and it is read at INIT (see `readNavState`) so hydration, not a
	// post-mount effect, is where the stored state lands. No flash.
	//
	// The branding preview reads and writes nothing: it is a picture of a shell, and it must not
	// inherit — or rewrite — the chrome preferences of the app hosting it (same rule as the rail
	// cookie and the theme toggle).
	//
	// `untrack` because this genuinely IS a one-time seed: `portal` and `interactive` are fixed for the
	// life of a mounted shell, and re-reading storage on a prop change would clobber live clicks.
	let openState = $state<Record<string, boolean>>(
		untrack(() => (interactive ? readNavState(portal) : {}))
	);

	// A `solo` group (the platform's Firm settings) holds the others shut while it is open — derived
	// from the same map rather than written into it, so the groups it suppresses come back as the user
	// left them. `applyGroupOpen` owns the two rules that follow from the two states disagreeing.
	const soloOpen = $derived(soloGroupOpen(nav, openState, railMode));

	function setGroupOpen(key: string, open: boolean) {
		const next = applyGroupOpen(nav, openState, key, open);
		if (next === openState) return;
		openState = next;
		if (!interactive) return;
		try {
			localStorage.setItem(navStateKey(portal), JSON.stringify(openState));
		} catch {
			// Storage blocked or over quota. The session state above still holds; only durability is lost.
		}
	}

	const brandName = $derived(brand?.name ?? 'Graphene');
	// `wordmark` (default) = the full lockup, no separate name text; `icon` = mark + name text.
	const logoVariant = $derived(brand?.logoVariant ?? 'wordmark');

	// ── The collapsed icon rail ────────────────────────────────────────────────────────────────────
	// The rail is `--sidebar-width-icon` (3.5rem) wide. Rendering the SAME lockup there and letting it
	// shrink squeezed a tenant wordmark into ~24px of usable width: `object-contain` scales a 4:1
	// wordmark down to about 6px tall, which is a smudge, not a brand. So the rail gets its own mark,
	// chosen by what the firm actually supplied:
	//
	//   1. `iconUrl` — a dedicated square symbol. What the rail is for.
	//   2. a wordmark but NO symbol — a monogram tile in the firm's own `--sidebar-primary`, matching
	//      the account row's avatar. Legible at 32px and unmistakably theirs. This is the case the
	//      `iconSrc ?? src` fallback inside Logo cannot serve: it would hand back the wordmark again.
	//   3. no assets at all — the built-in Graphene mark (near-square, reads fine at this size).
	const railIcon = $derived(
		brand?.iconUrl ? 'symbol' : brand?.logoUrl || brand?.logoDarkUrl ? 'monogram' : 'mark'
	);
	// "Morgan Lloyd" ⇒ ML, "Graphene" ⇒ G. Two letters is the most that stays legible in the tile.
	const monogram = $derived(initials(brandName, 'G'));

	// The rail hairline's LOOK, declared once. Two snippets render it (a <div> between groups, an
	// <li> inside the menu's <ul>) and a copy each is exactly how `field-shell.ts` records controls
	// drifting apart — change the width in one and the group rules and the subsection rules stop
	// matching, with nothing to catch it.
	//
	// `--primary` at 30%, not the chrome's `--glass-border`: a neutral hairline is what the header
	// and footer use to BOUND the chrome, and the rail's rules DIVIDE its nav — same weight, a
	// different job. Tinting them in the same branded token as the active tile makes the rail read
	// as one system, and a firm's own primary drives both.
	const RAIL_RULE_CLASS =
		'mx-auto my-2 hidden h-px w-7 shrink-0 bg-primary/30 group-data-[collapsible=icon]:block';

	// Active highlighting is "most specific wins": only the nav item whose href is the LONGEST match
	// for the current path lights up. So a nested page that has its own item (e.g. /documents/types)
	// never also lights its parent (/documents), while a nested page WITHOUT its own item (e.g.
	// /documents/123) still lights the parent. Normal items match by prefix; `exact` items (and the
	// root) match the whole path only.
	//
	// It matches against where the app is GOING (`pendingHref`) when a navigation is in flight, and
	// where it is only otherwise — the click is the moment the answer is known, and waiting for the
	// load to confirm it is what made the sidebar feel unresponsive.
	const targetPath = $derived(pendingHref ?? pathname);
	function matchesPath(item: NavItem): boolean {
		return item.href === '/' || item.exact
			? targetPath === item.href
			: targetPath === item.href || targetPath.startsWith(item.href + '/');
	}
	const activeHref = $derived.by(() => {
		let best = '';
		for (const group of nav) {
			for (const item of group.items) {
				if (matchesPath(item) && item.href.length > best.length) best = item.href;
			}
		}
		return best;
	});
	function isActive(href: string): boolean {
		return activeHref !== '' && href === activeHref;
	}
	// The spinner goes on the row we are heading to — which is exactly the row `activeHref` picked
	// while `pendingHref` is set. A navigation started anywhere else (a table row, a redirect) lands
	// on its section's row, which is the honest answer for it too.
	function isPending(href: string): boolean {
		return pendingHref !== null && pendingHref !== pathname && href === activeHref;
	}

	// Open-ness is resolved ONCE, here, because three things read it and they must agree: the
	// disclosure's own `open`, whether the group renders anything, and whether the rail draws a rule
	// above it. Computing it per consumer is what let a hairline be drawn above a closed group.
	const resolved = $derived(
		groups.map(({ group, key }) => ({
			group,
			key,
			open:
				isGroupOpen(group, openState, key, soloOpen) ||
				(railMode && groupHoldsHref(group, activeHref))
		}))
	);
	const mainGroups = $derived(resolved.filter(({ group }) => !group.pinned));
	const pinnedGroups = $derived(resolved.filter(({ group }) => group.pinned));
	// Render order, not declaration order — pinned groups are drawn last whatever the nav says.
	const ruleKeys = $derived(railRuleKeys([...mainGroups, ...pinnedGroups]));
</script>

<!--
  `--radius-chrome`: the corner the app CHROME uses, declared once here and inherited by every nav
  row, the account row and its avatar. It is a CAP, not a constant — a firm's branded `--radius`
  still drives it, but only up to 0.25rem. Branding emits `:root:root{--radius:…}`, which outranks
  the sharp preset's `:root`, so a firm asking for a consumer 0.85rem radius was turning 32px nav
  rows into pills no matter what the preset said. Content surfaces keep the full branded radius;
  dense repeated chrome does not. See the handoff requesting this as a real theme token.
-->
<Sidebar.Root
	collapsible="icon"
	class="[--radius-chrome:min(var(--radius),0.25rem)] [border-inline-end-color:var(--glass-border)]"
>
	<!-- Header height matches the topbar's exactly (both read `--topbar-h`), so the two bottom
	     hairlines form one unbroken line across the whole app instead of stepping at the sidebar edge.
	     Collapsed, the 12px side padding is dropped: 3rem − 24px left 24px for the mark, which is what
	     crushed the wordmark. The rail centres its mark in the full 56px instead. -->
	<Sidebar.Header
		class="h-[var(--topbar-h,4rem)] justify-center border-b [border-bottom-color:var(--glass-border)] px-3 group-data-[collapsible=icon]:px-0"
	>
		<!-- No `href` in a preview: an anchor without one is neither focusable nor navigable, so the
		     branding preview cannot bounce the admin app to `/`. -->
		<a
			href={interactive ? '/' : undefined}
			class="flex flex-col items-start gap-1 group-data-[collapsible=icon]:items-center"
			aria-label="{brandName} {portal} — home"
		>
			<!-- Expanded: the full lockup, and the portal label only where it earns its line.
			     Without the label the lockup is the header's only content, so it takes the height the
			     second line was using (h-6 → h-9) and the built-in wordmark grows with it. -->
			<span
				class="flex flex-col items-start group-data-[collapsible=icon]:hidden {portalLabel
					? 'gap-1'
					: ''}"
			>
				<Logo
					variant={logoVariant === 'icon' ? 'mark' : 'full'}
					markClass={portalLabel ? 'h-6 shrink-0' : 'h-9 shrink-0'}
					nameClass={portalLabel ? undefined : 'text-xl'}
					src={brand?.logoUrl}
					darkSrc={brand?.logoDarkUrl}
					iconSrc={brand?.iconUrl}
					name={brandName}
				/>
				<!-- The portal label STACKS under the wordmark rather than sitting beside it: a long
				     label ("Internal Operations") truncated to "INTERNAL…" inline, and the two-line
				     lockup keeps every portal's header structurally identical.

				     It renders for EVERY logo variant. It used to be suppressed for `icon` on the
				     assumption that Logo prints the brand name beside the mark in that variant — it does
				     not once a tenant supplies a logo image (Logo then renders the image and nothing
				     else), so any firm whose branding set logo_variant='icon' lost its portal label
				     altogether. The label identifies the PORTAL, not the lockup, so it is independent of
				     the lockup; the icon-collapsed rail hides it, and so does `portalLabel={false}`. -->
				{#if portalLabel}
					<span class="text-muted-foreground/80 text-[0.7rem] leading-none font-medium">
						{portal}
					</span>
				{/if}
			</span>

			<!-- Collapsed rail: its own square mark (see `railIcon` above), never the shrunk lockup. -->
			<span class="hidden group-data-[collapsible=icon]:flex">
				{#if railIcon === 'symbol'}
					<Logo variant="mark" iconSrc={brand?.iconUrl} name={brandName} markClass="size-8" />
				{:else if railIcon === 'monogram'}
					<span
						class="bg-sidebar-primary text-sidebar-primary-foreground grid size-8 shrink-0 place-items-center rounded-[var(--radius-chrome)] text-xs font-semibold tracking-[0.02em]"
					>
						{monogram}
					</span>
				{:else}
					<Logo variant="mark" markClass="h-8" />
				{/if}
			</span>
		</a>
	</Sidebar.Header>

	{#if context}
		<SidebarContext {context} {interactive} />
	{/if}

	<!--
	  `px-2` on BOTH this and each Sidebar.Group is 16px of inset per side. That is right at 16rem and
	  wrong in the rail: the rail is 56px and the tile a hard `size-9!` 36px, so 32px of inset leaves
	  a 24px box for a 36px tile, which overflows to the RIGHT and sits off the centre line while the
	  hairlines (`mx-auto`) centre honestly. The rail drops the inset entirely and centres the tiles
	  instead, so every icon, rule and monogram lands on one axis with 10px either side.
	-->
	<Sidebar.Content class="px-2 pt-2 group-data-[collapsible=icon]:px-0">
		{#each mainGroups as { group, key, open } (key)}
			{@render navGroup(group, key, open)}
		{/each}

		{#if pinnedGroups.length > 0}
			<!-- The flexible space that pins the Firm group to the bottom. It shrinks to nothing once the
			     nav is tall enough to scroll (`Sidebar.Content` is `overflow-auto`), so on a short viewport
			     the pinned groups simply follow the rest instead of being pushed out of reach. -->
			<div class="min-h-2 flex-1" aria-hidden="true"></div>
			{#each pinnedGroups as { group, key, open } (key)}
				{@render navGroup(group, key, open)}
			{/each}
		{/if}
	</Sidebar.Content>

	<Sidebar.Footer class="border-t [border-top-color:var(--glass-border)]">
		<SidebarUser {user} {menuItems} {interactive} />
	</Sidebar.Footer>
</Sidebar.Root>

{#snippet navItems(group: NavGroup)}
	<Sidebar.Menu class="gap-0.5 group-data-[collapsible=icon]:items-center">
		{#each group.items as item, index (item.href)}
			<!-- Computed once and read by BOTH the inline indicator and the rail badge below. Written
			     out twice, the two pictures could be given different suppression rules and no test
			     that counts class strings would see it. -->
			{@const pending = isPending(item.href)}
			{@const unread = item.unread && !isActive(item.href)}
			{#if item.section && item.section !== group.items[index - 1]?.section}
				<!-- A subsection is introduced by its first VISIBLE item. Keeping it on the item rather
				     than in a parallel section array means permission filtering can never leave a bare
				     heading with no links under it. It is a list item because Sidebar.Menu is a <ul>;
				     in the collapsed rail the WORDS disappear, where text-only furniture has no room —
				     but the BOUNDARY does not, or twenty icons run together as one undifferentiated
				     column. It becomes the hairline below instead. -->
				<li
					class="text-muted-foreground mt-3 px-2.5 pb-1 text-[0.68rem] leading-none font-semibold group-data-[collapsible=icon]:hidden!"
				>
					{item.section}
				</li>
				<!-- Only for a boundary INSIDE the group: at index 0 the group's own rule has already
				     drawn it, and two hairlines 3px apart read as a rendering fault. -->
				{#if index > 0}{@render railRuleItem()}{/if}
			{/if}
			<Sidebar.MenuItem>
				<!--
				  The active row is stated three ways so it never depends on the tinted fill
				  alone (`--sidebar-accent` is ~4% off `--sidebar`, which reads as a smudge
				  once the glass glow is gone): a 2px flush rule in `--primary` down the
				  leading edge, the icon in `--primary`, and a heavier label. All three are
				  branded tokens, so a firm's own primary drives the whole indicator.

				  The rail wears the same three, so the two states are one highlight rather
				  than two designs: the leading rule is the tile's own left edge there, and
				  the fill is stated in `--primary` rather than `--sidebar-accent` because a
				  36px square has no label to carry the colour for it.
				-->
				<Sidebar.MenuButton
					isActive={isActive(item.href)}
					tooltipContent={item.label}
					class="text-muted-foreground h-8 gap-2.5 rounded-[var(--radius-chrome)] px-2.5 font-medium data-active:shadow-[inset_2px_0_0_0_var(--primary)] data-active:[&_svg]:text-primary group-data-[collapsible=icon]:data-active:bg-primary/12 group-data-[collapsible=icon]:data-active:hover:bg-primary/20"
				>
					{#snippet child({ props })}
						<!-- `href` is dropped in a preview for the same reason as the brand lockup:
						     the branding editor's preview must not be able to navigate the app that
						     hosts it. -->
						<a href={interactive ? item.href : undefined} {...props}>
							{#if item.icon}<item.icon />{/if}
							<span
								class="group-data-active/menu-button:font-semibold group-data-[collapsible=icon]:sr-only"
							>
								{item.label}
							</span>
							{#if pending}
								<!-- The row is lit before its page exists, so something has to say the work is
								     still running — otherwise a slow load looks like a page that rendered
								     nothing. Ahead of the unread dot: while a navigation is in flight, "this
								     is loading" is the more useful of the two. -->
								<LoaderCircleIcon
									class="ml-auto size-3.5 shrink-0 animate-spin opacity-70 group-data-[collapsible=icon]:hidden"
									aria-hidden="true"
								/>
							{:else if unread}
								<!-- Suppressed on the item you're CURRENTLY on: an unread indicator on the
								     page already open makes no sense (Slack never dots the open channel
								     either), and it means reading a message clears the dot the instant you
								     navigate here — no poll/invalidation round trip needed for that case. -->
								<span
									class="bg-primary ml-auto size-2 shrink-0 rounded-full group-data-[collapsible=icon]:hidden"
									aria-hidden="true"
								></span>
							{/if}
						</a>
					{/snippet}
				</Sidebar.MenuButton>

				<!--
				  The same two indicators again, for the rail — and they have to live OUT here, on the
				  MenuItem, because the rail button is `size-8 p-2 overflow-hidden` around a `size-4`
				  icon: an `ml-auto` child has nowhere to go and is clipped away entirely. Collapse the
				  sidebar and the unread dot and the loading spinner both simply vanished, which is the
				  one state where the rail is all the reader has. MenuItem is `relative`, so a corner
				  badge here is outside the clip.

				  ONE positioned box holding either shape, rather than two absolutely-positioned
				  elements: given their own offsets the badge jumped as a pending navigation resolved
				  into an unread dot, and only one of them had been given the `--sidebar` ring that
				  separates it from the glyph underneath. Sized to the larger of the two, contents
				  centred, so both land on the same point. `pointer-events-none` so it never eats the
				  click meant for the row beneath it.
				-->
				{#if pending || unread}
					<span
						class="pointer-events-none absolute top-0.5 right-0.5 hidden size-3 place-items-center rounded-full group-data-[collapsible=icon]:grid"
						aria-hidden="true"
					>
						{#if pending}
							<LoaderCircleIcon class="text-primary bg-sidebar size-3 animate-spin rounded-full" />
						{:else}
							<span class="bg-primary ring-sidebar size-2 rounded-full ring-2"></span>
						{/if}
					</span>
				{/if}
			</Sidebar.MenuItem>
		{/each}
	</Sidebar.Menu>
{/snippet}

{#snippet navGroup(group: NavGroup, key: string, open: boolean)}
	{#if ruleKeys.has(key)}{@render railRule()}{/if}
	{#if group.collapsible}
		<!--
		  A collapsible group. `open` is driven, not bound: a `solo` group makes what a group DISPLAYS
		  and what the user asked of it deliberately differ, and a two-way binding would collapse the
		  two — writing the suppression into the stored map and losing the state it exists to restore.
		  The rail's active-group override (see `groupHoldsHref`) is the same hazard: it would persist.
		-->
		<Collapsible.Root
			{open}
			onOpenChange={(open) => setGroupOpen(key, open)}
			class="group/nav-group"
		>
			<Sidebar.Group class="py-1 group-data-[collapsible=icon]:px-0">
				<!--
				  The label IS the trigger. `hidden!` in the rail, not the GroupLabel's default
				  `opacity-0`: an invisible-but-present trigger is still clickable and still in the tab
				  order, so the rail would carry seven ghost buttons. `!` because `display` is also set by
				  the label's own base `flex` and utility order between the two is not guaranteed.
				-->
				<Sidebar.GroupLabel
					class="text-muted-foreground hover:text-sidebar-foreground w-full px-2 text-xs font-semibold transition-colors group-data-[collapsible=icon]:hidden!"
				>
					{#snippet child({ props })}
						<Collapsible.Trigger {...props}>
							{group.label}
							<ChevronDownIcon
								class="ml-auto size-3.5 transition-transform duration-200 group-data-[state=closed]/nav-group:-rotate-90"
							/>
						</Collapsible.Trigger>
					{/snippet}
				</Sidebar.GroupLabel>
				<Collapsible.Content>
					<Sidebar.GroupContent>
						{@render navItems(group)}
					</Sidebar.GroupContent>
				</Collapsible.Content>
			</Sidebar.Group>
		</Collapsible.Root>
	{:else}
		<Sidebar.Group class="py-1 group-data-[collapsible=icon]:px-0">
			{#if group.label}
				<Sidebar.GroupLabel class="text-muted-foreground px-2 text-xs font-semibold">
					{group.label}
				</Sidebar.GroupLabel>
			{/if}
			<Sidebar.GroupContent>
				{@render navItems(group)}
			</Sidebar.GroupContent>
		</Sidebar.Group>
	{/if}
{/snippet}

<!--
  The rail's only structural signal.

  Every heading the sidebar uses to break the nav up — a group label and a subsection heading alike
  — is text, and text has no room at 3rem, so the rail hid all of it and rendered twenty icons as
  one undifferentiated column. The rail keeps the BOUNDARY and drops only the WORDS.

  It is drawn in two places, and they are not the same rule. Between GROUPS: `railRuleKeys` gives
  the keys of every group that renders and has a rendering group before it — a boundary between two
  sections, whether or not either is labelled, and never above a group that is closed and therefore
  draws nothing. Inside a group: from the same `{#if}` as the subsection heading, so a subsection
  added later gets its rule for free.

  The tint is `--primary`, the same branded token the active tile carries (see the note on the
  MenuButton above). Two snippets for one look because the group rule sits among <div>s in Sidebar.Content
  and the subsection rule sits among <li>s inside Sidebar.Menu's <ul>; the look itself is
  `RAIL_RULE_CLASS`, declared once.
-->
{#snippet railRule()}
	<div class={RAIL_RULE_CLASS} aria-hidden="true"></div>
{/snippet}

{#snippet railRuleItem()}
	<li class={RAIL_RULE_CLASS} aria-hidden="true"></li>
{/snippet}
