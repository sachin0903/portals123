<script lang="ts">
	/**
	 * `theme` comes from OUR scheme store, not `mode-watcher`. Importing `mode` here also imported
	 * mode-watcher's side effect, which wrote an inline `style.colorScheme` off the OS preference and
	 * overrode `theme.css` — see the note in `../theme-toggle/scheme.svelte.ts`. `colourScheme` reads
	 * the `.dark` class, which is the same source of truth the SSR paint, the head script and
	 * `ThemeToggle` all write.
	 */
	import { Toaster as Sonner, type ToasterProps as SonnerProps } from 'svelte-sonner';
	import { colourScheme } from '../theme-toggle/scheme.svelte.js';
	import Loader2Icon from '@lucide/svelte/icons/loader-2';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import OctagonXIcon from '@lucide/svelte/icons/octagon-x';
	import InfoIcon from '@lucide/svelte/icons/info';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';

	/**
	 * Clear the topbar. Sonner offsets a stack 24px from the edge, which puts a top-positioned one
	 * straight over the chrome — and the notification BELL is in that corner, so an arriving
	 * notification covered the very control it is telling you to look at, badge and all.
	 *
	 * 4.75rem is the shells' `--topbar-h` (4rem, declared by both `saas-shell` and `client-shell`)
	 * plus a 0.75rem gap. The token is not read directly because this Toaster is mounted OUTSIDE
	 * the shell in every portal — a sibling of it, not a descendant — so the property is not in
	 * scope here and would only ever resolve to its fallback. `chrome.test.ts` pins the 4rem, so a
	 * change there fails a test rather than quietly leaving this stale.
	 *
	 * ONLY `top` is named. Sonner fills each unnamed edge of a partial offset with its own default,
	 * so the sides and bottom are untouched and the platform/admin action toasts do not move. That
	 * default is 24px, not the 32px its prop docs claim — which is exactly why the value is left to
	 * sonner rather than restated here.
	 *
	 * An offset, not a different position. Moving these to a bottom corner would merge them with
	 * those action toasts, which is the stack separation the bell deliberately arranges by pinning
	 * itself to `top-right`.
	 */
	const CLEAR_TOPBAR = { top: '4.75rem' };

	let { offset = CLEAR_TOPBAR, ...restProps }: SonnerProps = $props();
</script>

<Sonner
	theme={colourScheme.current}
	class="toaster group"
	style="--normal-bg: var(--color-popover); --normal-text: var(--color-popover-foreground); --normal-border: var(--color-border);"
	{offset}
	{...restProps}
>
	{#snippet loadingIcon()}
		<Loader2Icon class="size-4 animate-spin" />
	{/snippet}
	{#snippet successIcon()}
		<CircleCheckIcon class="size-4" />
	{/snippet}
	{#snippet errorIcon()}
		<OctagonXIcon class="size-4" />
	{/snippet}
	{#snippet infoIcon()}
		<InfoIcon class="size-4" />
	{/snippet}
	{#snippet warningIcon()}
		<TriangleAlertIcon class="size-4" />
	{/snippet}
</Sonner>
