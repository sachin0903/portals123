<script lang="ts">
	import * as Tooltip from '../tooltip/index.js';
	import { cn, type WithElementRef } from '../../../utils.js';
	import type { HTMLAttributes } from 'svelte/elements';
	import {
		SIDEBAR_COOKIE_MAX_AGE,
		SIDEBAR_COOKIE_NAME,
		SIDEBAR_WIDTH,
		SIDEBAR_WIDTH_ICON
	} from './constants.js';
	import { setSidebar } from './context.svelte.js';

	let {
		ref = $bindable(null),
		open = $bindable(true),
		onOpenChange = () => {},
		preview = false,
		class: className,
		style,
		children,
		...restProps
	}: WithElementRef<HTMLAttributes<HTMLDivElement>> & {
		open?: boolean;
		onOpenChange?: (open: boolean) => void;
		/**
		 * This provider is a PREVIEW of an app shell (the branding editor), not the app shell itself.
		 *
		 * Everything below that reaches OUTSIDE this subtree has to be switched off, or a second
		 * provider mounted inside a page fights the real one: `--content-inset-left` is written to
		 * `document.documentElement` (last mount wins, so opening the branding preview moved where the
		 * real app centres its dialogs), the ⌘B shortcut is bound to `window` (one keypress toggled
		 * both sidebars), and `setOpen` writes the shared `sidebar_state` cookie (collapsing the
		 * preview's rail collapsed the real sidebar on the next load). None of that is visible to a
		 * type gate — the preview simply renders and quietly mutates the app around it.
		 */
		preview?: boolean;
	} = $props();

	const sidebar = setSidebar({
		open: () => open,
		setOpen: (value: boolean) => {
			open = value;
			onOpenChange(value);

			// This sets the cookie to keep the sidebar state.
			if (!preview) {
				document.cookie = `${SIDEBAR_COOKIE_NAME}=${open}; path=/; max-age=${SIDEBAR_COOKIE_MAX_AGE}`;
			}
		}
	});

	// Publish the persistent sidebar width to the document root so body-portaled
	// overlays (dialogs) can centre within the content area rather than the whole
	// viewport. Mobile is off-canvas (no persistent gap), so it contributes 0.
	// Falls back to 0px anywhere no provider is mounted (auth pages, sidebar-less apps).
	$effect(() => {
		if (preview) return;
		const width = sidebar.isMobile
			? '0px'
			: sidebar.state === 'expanded'
				? SIDEBAR_WIDTH
				: SIDEBAR_WIDTH_ICON;
		const root = document.documentElement;
		root.style.setProperty('--content-inset-left', width);
		return () => root.style.removeProperty('--content-inset-left');
	});
</script>

<!-- `<svelte:window>` cannot live inside an `{#if}` (it must be top level), so the guard is in the
     handler: a preview provider ignores ⌘B instead of toggling alongside the real sidebar. -->
<svelte:window
	onkeydown={(e) => {
		if (!preview) sidebar.handleShortcutKeydown(e);
	}}
/>

<Tooltip.Provider delayDuration={0}>
	<div
		data-slot="sidebar-wrapper"
		style="--sidebar-width: {SIDEBAR_WIDTH}; --sidebar-width-icon: {SIDEBAR_WIDTH_ICON}; {style}"
		class={cn(
			'group/sidebar-wrapper has-data-[variant=inset]:bg-sidebar flex min-h-svh w-full',
			className
		)}
		bind:this={ref}
		{...restProps}
	>
		{@render children?.()}
	</div>
</Tooltip.Provider>
