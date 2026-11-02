<script lang="ts">
	import type { Snippet } from 'svelte';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import UserMenu, { type AccountMenuContact } from './user-menu.svelte';
	import Logo from '../brand/logo.svelte';
	import NavProgress from './nav-progress.svelte';
	import type { Brand, NavItem } from './types.js';
	import { isNavActive } from './nav-active.js';
	import { cn } from '../../utils.js';

	let {
		user,
		nav = [],
		pathname = '',
		pendingHref = null,
		back = null,
		immersive = false,
		search,
		actions,
		brand,
		heightClass = 'h-dvh',
		interactive = true,
		contacts = [],
		activeContactId = null,
		onSelectContact,
		children
	}: {
		user: { email: string };
		/** Banking nav — shown inline on desktop, as a bottom tab bar on mobile. */
		nav?: NavItem[];
		pathname?: string;
		/**
		 * Where a click is already heading (`navigating.to?.url.pathname`), or null when idle — the
		 * tab/pill highlight follows it so it moves on the click rather than when the page's `load`
		 * finally answers. See nav-progress.
		 */
		pendingHref?: string | null;
		/**
		 * Set on a SUB page — a record (one account, one document), a reader, a utility (profile,
		 * messages) — and null on the tab pages. The shell owns the way back so every sub page has
		 * the same one: on a phone the top bar's left slot becomes "‹ Home" (the parent's name,
		 * from the firm's own page set) in place of the avatar and the bottom tab bar is hidden,
		 * the way the banking apps push a detail screen; on desktop the bar keeps its logo and
		 * nav and the same control leads the content. The destination is the PARENT, never
		 * history.back(): a deep link from a notification must still have a way up.
		 */
		back?: { href: string; label: string } | null;
		/**
		 * The page IS the screen, below `lg`: no top bar, no column padding, the page draws its own
		 * header and takes the full viewport. For the one kind of sub page whose own header is the
		 * better top bar — a conversation, where the person you are talking to is the title and the
		 * way back sits beside them, the way every messaging app draws it; a global bar above that
		 * is two headers. Desktop is unchanged: the bar carries the nav and the page sits in the
		 * column like everything else. Only meaningful with `back` set (the page must draw it).
		 */
		immersive?: boolean;
		/** Optional search affordance (trigger + palette) rendered in the header. */
		search?: Snippet;
		/** Optional extra header controls, left of the avatar. */
		actions?: Snippet;
		/** Per-tenant white-label brand (logo + name). Unset ⇒ Graphene default. */
		brand?: Brand;
		/** Frame height — `h-dvh` in the app; the branding preview passes `h-full` to fit its box. */
		heightClass?: string;
		/**
		 * `false` ⇒ this shell is a PICTURE of the portal (the branding preview renders the real one
		 * inside the admin app). Every link loses its `href`, so a click in the preview can't navigate
		 * the app hosting it. An `<a>` without `href` is not a link: not focusable, not activatable,
		 * and styling is untouched, so the picture is unchanged.
		 */
		interactive?: boolean;
		/** The account avatar becomes a dropdown (client switcher + Account) when there's more
		 * than one — see UserMenu. 0 or 1 ⇒ the avatar stays a plain link, unchanged. */
		contacts?: AccountMenuContact[];
		activeContactId?: number | null;
		onSelectContact?: (id: number) => void;
		children?: Snippet;
	} = $props();

	// Highlighting follows the DESTINATION while a navigation is in flight (see `pendingHref`); a
	// preview never navigates, so it never has one.
	const targetPath = $derived(interactive ? (pendingHref ?? pathname) : pathname);
	const isActive = (href: string) => isNavActive(href, targetPath);
	/** `undefined` in a preview — see `interactive`. */
	const linkTo = (href: string) => (interactive ? href : undefined);

	// Reset the scroll container to the top on navigation. SvelteKit only manages
	// window scroll, but this app-shell scrolls <main> internally — so scroll it to
	// top whenever the route (pathname) changes. `lastPath` is a plain (non-reactive)
	// variable, so writing it doesn't re-trigger the effect.
	let mainEl = $state<HTMLElement>();
	let lastPath = '';
	$effect(() => {
		if (pathname !== lastPath) {
			lastPath = pathname;
			mainEl?.scrollTo({ top: 0 });
		}
	});

	// Below `lg`: the header blends into the page at the top and only turns to glass once content
	// scrolls under it. Desktop keeps the glass bar always (`lg:before:glass-bar`) — the SAME
	// breakpoint as the tab bar's exit and the top nav's entrance. It was `md`, which gave a
	// tablet-width window (768–1023px) a permanently visible top bar AND the floating tabs.
	let scrolled = $state(false);
	$effect(() => {
		const el = mainEl;
		if (!el) return;
		const onScroll = () => (scrolled = el.scrollTop > 2);
		onScroll();
		el.addEventListener('scroll', onScroll, { passive: true });
		return () => el.removeEventListener('scroll', onScroll);
	});

	// Bottom-nav sliding pill. The active tab shows icon + name and the indicator
	// measures it, then animates its left/width to glide along the bar. Recomputed
	// when the route changes or the bar resizes; hidden when no tab matches.
	let tabEls: HTMLAnchorElement[] = $state([]);
	let pill = $state({ left: 0, width: 0, ready: false });
	const activeIndex = $derived(nav.findIndex((item) => isActive(item.href)));

	/** The floating tab bar: tab pages only — a sub page is one level down from it. */
	const showTabBar = $derived(nav.length > 0 && back === null);

	$effect(() => {
		const el = tabEls[activeIndex];
		if (!el) {
			// Fresh object — must NOT read `pill` here (e.g. via spread), or this
			// effect would depend on the value it writes and loop infinitely.
			pill = { left: 0, width: 0, ready: false };
			return;
		}
		const measure = () => {
			pill = { left: el.offsetLeft, width: el.offsetWidth, ready: true };
		};
		measure();
		window.addEventListener('resize', measure);
		return () => window.removeEventListener('resize', measure);
	});
</script>

{#snippet backLink()}
	{#if back}
		<a
			href={linkTo(back.href)}
			data-shell-back
			class="glass text-muted-foreground hover:text-foreground tap-target inline-flex h-9 items-center gap-0.5 rounded-full pr-3.5 pl-2 text-sm font-medium transition-colors"
		>
			<ChevronLeftIcon class="size-5 shrink-0" aria-hidden="true" />
			<span class="max-w-40 truncate">{back.label}</span>
		</a>
	{/if}
{/snippet}

<!-- App-shell: the frame is pinned to the viewport and only <main> scrolls. This
     keeps the document itself from scrolling, so the mobile browser chrome never
     toggles — the floating nav stays put and there's no stray document scroll.
     The header lives *inside* the scroll area (sticky) so it shares the exact same
     width + scrollbar gutter as the content, keeping everything perfectly centred. -->
<!-- `--topbar-h` mirrors SaaSShell's declaration (see saas-shell) so a client page doing full-height
     maths reads the same token as a platform one; the header below consumes it too, so the number
     lives in exactly one place per shell. The client bar stays GLASS — `glass-bar` + `glass-canvas`
     here are the soft treatment and the sharp preset never reaches this portal. -->
<!-- `--shell-pb` is the content column's bottom clearance, published so a page that cancels the
     column's padding to run full-height (messages, the document viewer) can put the SAME number
     back under its own last row: 6.5rem + safe area while the floating tab bar is there, a
     plain 1.5rem + safe area on a sub page without it, 2.5rem on desktop. `--shell-back-h` is
     the height the desktop back row takes above the content (0 on a phone, where the control is
     in the top bar), for the same full-height pages' viewport maths. -->
<div
	class={cn(
		'relative flex flex-col overflow-hidden [--topbar-h:4rem] lg:[--shell-pb:2.5rem]',
		showTabBar
			? '[--shell-pb:calc(6.5rem+env(safe-area-inset-bottom))]'
			: '[--shell-pb:calc(1.5rem+env(safe-area-inset-bottom))]',
		back ? '[--shell-back-h:0px] lg:[--shell-back-h:3.5rem]' : '[--shell-back-h:0px]',
		heightClass
	)}
>
	<!-- Ambient liquid-glass backdrop: soft brand-tinted blobs the glass surfaces
	     above refract. Fixed + decorative, sits behind everything. -->
	<div class="glass-canvas pointer-events-none fixed inset-0 -z-10" aria-hidden="true"></div>

	<NavProgress active={interactive && pendingHref !== null} />

	<!-- Immersive: the stable scrollbar gutter goes too below `lg`. The page is exactly the viewport
	     and does not scroll, and on a browser with classic scrollbars the reserved gutter drew a
	     strip of canvas down both edges of a card that was meant to touch them. -->
	<main
		bind:this={mainEl}
		class={cn(
			'flex-1 overflow-x-hidden overflow-y-auto',
			immersive ? 'lg:[scrollbar-gutter:stable_both-edges]' : '[scrollbar-gutter:stable_both-edges]'
		)}
	>
		<!-- The glass is painted by a FIXED pseudo-element, not by the header box: the header sits
		     inside the scrolling <main> so content passes under it, but <main> reserves a scrollbar
		     gutter on both edges and clips the right one, so a bar painted on the header's own box
		     stops 12px short of each side. A fixed pseudo escapes the scroll container and spans
		     the viewport; it sits behind the header's own content (`-z-10` inside the header's
		     stacking context) and still blurs whatever scrolls under it. -->
		<header
			class={cn(
				// `--shell-top` is the height of anything the host draws ABOVE the shell (the client
				// portal's view-as bar); the fixed glass pseudo is pinned to the viewport, so without
				// it the bar would paint under that notice while the header's own content sat below.
				'sticky top-0 z-20 before:pointer-events-none before:fixed before:inset-x-0 before:top-(--shell-top,0px) before:-z-10 before:h-(--topbar-h,4rem) before:transition-colors before:duration-200 before:content-[""]',
				scrolled && 'before:glass-bar',
				'lg:before:glass-bar',
				immersive && 'max-lg:hidden'
			)}
		>
			<div
				class="relative mx-auto flex h-[var(--topbar-h,4rem)] max-w-6xl items-center gap-3 px-4 sm:px-6"
			>
				<!-- left: the way back or the profile on mobile, brand on desktop -->
				<div class="flex items-center gap-2">
					<div class="lg:hidden">
						{#if back}
							{@render backLink()}
						{:else}
							<UserMenu
								{user}
								{interactive}
								{contacts}
								{activeContactId}
								{onSelectContact}
								accountHref="/profile"
							/>
						{/if}
					</div>
					<a
						href={linkTo('/')}
						class="tap-target hidden items-center lg:flex"
						aria-label="{brand?.name ?? 'Graphene'} — home"
					>
						<Logo
							class="text-foreground"
							variant={brand?.logoVariant === 'icon' ? 'mark' : 'full'}
							markClass="h-7"
							src={brand?.logoUrl}
							darkSrc={brand?.logoDarkUrl}
							iconSrc={brand?.iconUrl}
							name={brand?.name}
						/>
					</a>
				</div>

				<!-- centre: primary nav, absolutely centred so it never shifts with the sides -->
				<nav class="absolute left-1/2 hidden -translate-x-1/2 items-center gap-1 lg:flex">
					{#each nav as item (item.href)}
						<a
							href={linkTo(item.href)}
							class={cn(
								'tap-target flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
								isActive(item.href)
									? 'bg-accent text-accent-foreground'
									: 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
							)}
						>
							{#if item.icon}<item.icon class="size-4" />{/if}
							{item.label}
						</a>
					{/each}
				</nav>

				<!-- right: search (round button) + actions + profile. Search sits left of the
				     avatar, which stays pinned right, so collapsing the search shifts nothing. -->
				<div class="ml-auto flex items-center gap-2">
					{#if search}{@render search()}{/if}
					{#if actions}{@render actions()}{/if}
					<div class="hidden lg:block">
						<UserMenu
							{user}
							{interactive}
							{contacts}
							{activeContactId}
							{onSelectContact}
							accountHref="/profile"
						/>
					</div>
				</div>
			</div>
		</header>

		<div
			class={cn(
				'mx-auto w-full max-w-6xl',
				immersive ? 'lg:px-6 lg:py-10' : 'px-4 py-6 pb-[var(--shell-pb)] sm:px-6 lg:py-10'
			)}
		>
			{#if back}
				<!-- Desktop: the same control the phone carries in its top bar, leading the content
				     (the bar up there is the logo and the nav). mb-5 + h-9 = --shell-back-h. -->
				<div class="mb-5 hidden lg:block">{@render backLink()}</div>
			{/if}
			{@render children?.()}
		</div>
	</main>

	{#if showTabBar}
		<nav
			class="glass fixed inset-x-3 bottom-[max(0.5rem,env(safe-area-inset-bottom))] z-20 rounded-full lg:hidden"
		>
			<div class="relative flex items-stretch gap-1 p-1.5">
				<!-- Sliding active indicator — measured from the active tab. -->
				<span
					aria-hidden="true"
					class={cn(
						'bg-primary/12 ring-primary/20 pointer-events-none absolute top-1.5 bottom-1.5 z-0 rounded-full shadow-[inset_0_1px_0_0_var(--glass-highlight)] ring-1 ring-inset transition-[left,width] duration-300 ease-[cubic-bezier(0.32,0.72,0,1)] motion-reduce:transition-none',
						!pill.ready && 'hidden'
					)}
					style="left: {pill.left}px; width: {pill.width}px;"
				></span>
				{#each nav as item, i (item.href)}
					{@const active = i === activeIndex}
					<a
						bind:this={tabEls[i]}
						href={linkTo(item.href)}
						aria-current={active ? 'page' : undefined}
						class={cn(
							'relative z-10 flex min-w-0 flex-1 flex-col items-center gap-1 rounded-full py-2 text-[0.7rem] font-semibold transition-colors',
							active ? 'text-primary' : 'text-muted-foreground hover:text-foreground'
						)}
					>
						{#if item.icon}<item.icon class="size-5 shrink-0" />{/if}
						<!-- `min-w-0` above and the clamp here: a firm's longer page name ("Documents",
						     "Investments") on a narrow phone otherwise widened its tab past the share the
						     bar gave it, and the labels beside it were cut mid-letter. -->
						<span class="max-w-full truncate px-1">{item.label}</span>
					</a>
				{/each}
			</div>
		</nav>
	{/if}
</div>
