<script lang="ts">
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import type { Snippet } from 'svelte';
	import { cn } from '../../utils.js';

	// The primary submit button for the auth flow — gradient-shadowed pill with a
	// nudging arrow, matching the login form. `class` merges for layout tweaks.
	let {
		class: className,
		disabled = false,
		children
	}: { class?: string; disabled?: boolean; children: Snippet } = $props();
</script>

<button
	type="submit"
	{disabled}
	class={cn(
		// The lifted shadow is a tint of --primary (was a hardcoded oklch of the stock navy), so a
		// firm's brand carries into the glow instead of every portal casting a Graphene-navy one.
		'group bg-primary text-primary-foreground flex h-12 w-full items-center justify-center gap-2 rounded-xl text-sm font-medium shadow-[0_10px_24px_-10px_color-mix(in_oklch,var(--primary)_70%,transparent)] transition-all hover:brightness-110 active:scale-[0.99]',
		// Muted and unresponsive rather than hidden: the button staying put is what makes the note
		// beside it read as the reason, where a vanishing button reads as the form being broken.
		'disabled:pointer-events-none disabled:opacity-50 disabled:shadow-none',
		className
	)}
>
	{@render children()}
	<ArrowRightIcon class="size-4 transition-transform group-hover:translate-x-0.5" />
</button>
