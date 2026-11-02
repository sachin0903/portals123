<script lang="ts">
	import type { Snippet } from 'svelte';
	import * as Sidebar from '../ui/sidebar/index.js';
	import * as Breadcrumb from '../ui/breadcrumb/index.js';
	import { Separator } from '../ui/separator/index.js';
	import PanelLeftIcon from '@lucide/svelte/icons/panel-left';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import TenantSwitcher from './tenant-switcher.svelte';
	import type { BackLink, Crumb, Tenant } from './types.js';

	let {
		back,
		breadcrumb = [],
		tenants,
		activeTenantId,
		notice,
		search,
		actions,
		interactive = true
	}: {
		/**
		 * An origin-aware back arrow, rendered BEFORE the trail.
		 *
		 * The trail says where a page sits; this says where the reader came from, and the two differ
		 * the moment a record is reachable from more than one place — a task opened from the dashboard
		 * sat under a trail reading "Tasks / …", whose only link went somewhere the reader had never
		 * been. Both are useful, so both render.
		 *
		 * Unset ⇒ just the trail, exactly as before. That is the admin portal and the branding
		 * preview, and it is also the platform on a hard reload, where there is no history to offer
		 * and the trail's own parent link is the honest answer.
		 */
		back?: BackLink;
		breadcrumb?: Crumb[];
		tenants?: Tenant[];
		activeTenantId?: string;
		/** A status the app wants in the bar itself — admin's "acting on behalf of" mode. Sits in the
		 *  flexible gap between the page name and the utilities, so it never displaces either. */
		notice?: Snippet;
		search?: Snippet;
		/** Header actions (e.g. the notification bell), right of search. */
		actions?: Snippet;
		/**
		 * `false` ⇒ this bar is a PICTURE of the chrome (the branding preview renders the real shell),
		 * so every control that mutates the surrounding app is replaced by a static stand-in.
		 *
		 * `ThemeToggle` is the reason this prop exists: it writes the `gp-theme` cookie and toggles
		 * `.dark` on `document.documentElement`. Inside the branding preview that is the ADMIN app's
		 * root — so /tenants/{id}/branding shipped two `[aria-label="Colour theme"]` triggers and
		 * clicking the inner one re-themed the portal you were working in. The stand-in keeps the bar's
		 * composition (and its sun/moon state, driven by `.dark` on the preview frame) without owning
		 * any write side.
		 */
		interactive?: boolean;
	} = $props();
</script>

<!--
  Staff-tool chrome, not a floating glass bar. Three deliberate changes from `glass-bar`:

  0. `h-[var(--topbar-h)]` — the bar's height is a TOKEN published by the shell (see saas-shell), not
     a number repeated per file. Raising it from 3.5rem to 4rem to line the divider up with the
     sidebar header silently broke three full-height editors in the platform portal that had
     hardcoded `calc(100svh-3.5rem)`; a var is the only version of this that a page can consume.
  1. `bg-sidebar` (= the branded `--card`) instead of the bar's `--glass-bg`. With the sharp preset's
     `--glass-tint:100%` that resolved to `--background` — the same colour as the page canvas — so
     the bar dissolved into the content and only a hairline hinted at it. Sharing the sidebar's
     surface makes the chrome one continuous L against a distinct canvas.
  2. A real `border-b` in `--glass-border` rather than an inset box-shadow, so it is the same
     hairline the sidebar draws and lines up with the sidebar header's own border.
  3. The same height as that header (both read `--topbar-h`), so the divider is unbroken across the
     full width.

  `glass-bar` also carried `backdrop-filter: saturate(185%) brightness(1.06)`, which only ever meant
  anything with a blur behind it; the sharp preset zeroes the blur, so it was pure compositing cost.
  This component is rendered by SaaSShell only — the platform and admin portals, both sharp. The
  client portal's bar lives in client-shell and still uses `glass-bar` untouched.

  `--radius-chrome` mirrors the sidebar's declaration (see app-sidebar) so the switcher, the toggle
  and the sidebar rail all use the same capped corner.
-->
<header
	data-slot="saas-topbar"
	class="bg-sidebar sticky top-0 z-10 flex h-[var(--topbar-h,4rem)] shrink-0 items-center gap-2 border-b px-4 [--radius-chrome:min(var(--radius),0.25rem)] [border-bottom-color:var(--glass-border)]"
>
	{#if interactive}
		<Sidebar.Trigger class="-ml-1 rounded-[var(--radius-chrome)]" />
	{:else}
		<!-- Stand-in for the rail toggle: same box, no handler, no cookie. -->
		<span
			class="text-muted-foreground -ml-1 flex size-8 items-center justify-center"
			aria-hidden="true"
		>
			<PanelLeftIcon class="size-4" />
		</span>
	{/if}
	<Separator orientation="vertical" class="mr-1 data-[orientation=vertical]:h-5" />
	{#if back}
		<!-- Sits BESIDE the trail, not instead of it: they answer different questions. The trail is a
		     map — where this page lives — and it is the same on every visit. The arrow is history —
		     where YOU came from — and it differs by entry point, which is the whole reason a fixed
		     parent link was the wrong answer for a record reachable from six places.
		     Icon only, with the destination in the accessible name: spelling it out would repeat most
		     of the trail beside the trail ("Back to Clients | Clients / Mr Max Oakden"). -->
		<a
			href={back.href}
			aria-label="Back to {back.label}"
			title="Back to {back.label}"
			class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -ml-1 inline-flex size-7 shrink-0 items-center justify-center rounded-[var(--radius-chrome)] transition-colors focus-visible:outline-none focus-visible:ring-[3px]"
		>
			<ChevronLeftIcon class="size-4" />
		</a>
	{/if}
	{#if breadcrumb.length}
		<!-- THE TRAIL IS THE PAGE'S NAME, not decoration above it. The listings dropped their
		     `PageHeader` — the crumb already said "Clients", so a heading underneath repeated it and
		     spent a band of every screen doing so — which leaves this as the ONLY thing naming the
		     page. So the last crumb is typeset as a title (base size, semibold, full contrast) and
		     the ancestors stay small and muted behind it: a hierarchy a reader can see at a glance
		     rather than a uniform grey line they have to read.
		     It is also no longer hidden below `sm`. While a heading existed, dropping the trail on a
		     phone cost the map but kept the name; without one it left the page anonymous. The
		     ancestors are what collapse there instead — the leaf always survives. -->
		<Breadcrumb.Root class="min-w-0">
			<Breadcrumb.List class="flex-nowrap gap-1">
				<!-- Deliberately UNKEYED. A breadcrumb is positional, and labels repeat legitimately — a
				     journey step called "Applicant details" sits under a journey of the same name. Keying
				     on the label threw `each_key_duplicate`, which aborts the surrounding subtree
				     mid-hydration: because this topbar is in every portal's shell, one repeated crumb
				     killed hydration for the WHOLE PAGE. Every popover and select went inert, forms only
				     appeared after a reload, and the theme toggle did nothing — with nothing logged,
				     because the throw surfaced only as an unhandled rejection inside an effect. -->
				{#each breadcrumb as crumb, i}
					{@const leaf = i === breadcrumb.length - 1}
					<Breadcrumb.Item class={leaf ? 'min-w-0' : 'hidden shrink-0 sm:inline-flex'}>
						{#if crumb.href && !leaf}
							<!-- Underlined on hover, not just tinted: an ancestor crumb is the way back now
							     that no in-page control is, and a colour shift alone did not say it was a
							     link until the pointer was already on it. -->
							<Breadcrumb.Link
								href={crumb.href}
								class="hover:text-foreground rounded underline-offset-4 hover:underline"
							>
								{crumb.label}
							</Breadcrumb.Link>
						{:else}
							<Breadcrumb.Page class="text-foreground truncate text-base font-semibold">
								{crumb.label}
							</Breadcrumb.Page>
						{/if}
					</Breadcrumb.Item>
					{#if !leaf}<Breadcrumb.Separator class="hidden shrink-0 sm:inline-flex" />{/if}
				{/each}
			</Breadcrumb.List>
		</Breadcrumb.Root>
	{/if}
	<!-- The topbar only LAUNCHES search; typing happens in the palette. Keep the trigger in the
	     trailing utility cluster beside notifications rather than spending the centre of every page
	     on an inert field-shaped control. The spacer also lets a long trail yield before it can push
	     those utilities out of view. -->
	<div class="flex min-w-0 flex-1 items-center justify-end px-3">
		{#if notice}{@render notice()}{/if}
	</div>
	<div data-slot="topbar-actions" class="flex shrink-0 items-center gap-2">
		{#if search}{@render search()}{/if}
		{#if actions}{@render actions()}{/if}
		<TenantSwitcher {tenants} {activeTenantId} />
	</div>
</header>
