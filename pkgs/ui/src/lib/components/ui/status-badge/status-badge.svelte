<script lang="ts" module>
	import { tv } from 'tailwind-variants';
	import { statusToneClasses, type StatusToneName } from './tones.js';

	/* The tone map lives in ./tones.ts so it can be unit-tested and so other surfaces can defer to
	   it instead of re-deciding a status colour. Each tone is a pure token lookup — the derived
	   subtle/strong/line trio per status — so a firm re-branding --success cascades here. */
	export const statusBadgeVariants = tv({
		base: 'inline-flex w-fit items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap',
		variants: { tone: statusToneClasses },
		defaultVariants: { tone: 'neutral' }
	});

	/* Aliased off the map's own keys rather than inferred back out of `tv()`: the union is then
	   guaranteed exact for the ~40 call sites across the portals that annotate with it. */
	export type StatusTone = StatusToneName;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import { cn } from '../../../utils.js';

	let {
		tone = 'neutral',
		pulse = false,
		class: className,
		children
	}: {
		tone?: StatusTone;
		/** Show a pulsing dot (e.g. queued/generating in-flight states). */
		pulse?: boolean;
		class?: string;
		children: Snippet;
	} = $props();
</script>

<span data-slot="status-badge" class={cn(statusBadgeVariants({ tone }), className)}>
	{#if pulse}
		<span class="size-1.5 animate-pulse rounded-full bg-current"></span>
	{/if}
	{@render children()}
</span>
