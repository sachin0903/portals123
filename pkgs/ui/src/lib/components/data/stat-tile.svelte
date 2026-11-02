<script lang="ts">
	import type { Component } from 'svelte';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import Stat from './stat.svelte';
	import { cn } from '../../utils.js';

	/**
	 * The carded KPI tile — the one way a dashboard renders a headline number. Pass `href` to make
	 * the whole tile a link (it gains a hover lift and a chevron), which is how a KPI should behave
	 * when there's a screen behind it. Rendering Card + Stat by hand at the call site is what let
	 * platform and admin drift apart, so don't.
	 */
	let {
		label,
		value,
		sub,
		tone = 'neutral',
		valueTone,
		icon,
		delta,
		href,
		onclick,
		pressed = false,
		class: className
	}: {
		label: string;
		value: string;
		sub?: string;
		tone?: 'positive' | 'negative' | 'neutral';
		/** Tints the FIGURE by direction — passed straight to `Stat`. A tile showing a gain or a
		 *  loss needs it; without the passthrough the only way to colour one was to stop using this
		 *  component, which is exactly the drift it exists to prevent. */
		valueTone?: 'positive' | 'negative';
		icon?: Component<{ class?: string }>;
		delta?: { value: string; direction?: 'up' | 'down' | 'flat' };
		/** Makes the tile a link to the screen behind the number. */
		href?: string;
		/** Makes the tile a button — for a figure that cuts the list on the SAME screen (a filter)
		 *  rather than leading to another. `pressed` says the cut is in force; the tile shows it
		 *  with a ring and says it with `aria-pressed`. */
		onclick?: () => void;
		pressed?: boolean;
		class?: string;
	} = $props();

	// The tile surface is the glass panel material, which the finish axis re-points at the token
	// level (theme.css): under the ledger finish `--glass-panel-tint:0%` opens the fill,
	// `--glass-panel-shadow` + `--glass-sheen-strength` drop the lift and gloss, and `rounded-xl`
	// tracks --radius. Staff chrome (`chrome.css`) zeroes `--glass-border` and the panel's 1px
	// width so the tile is a floating sheet; the client portal keeps the hairline.
	// No per-finish styling belongs here — the tokens carry it.
	const surface =
		'group/tile glass-panel relative flex flex-col justify-between gap-3 rounded-xl p-4 transition-all';
</script>

{#if href}
	<a
		{href}
		class={cn(
			surface,
			'hover:-translate-y-0.5 hover:shadow-[var(--elev-2)]',
			'focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-[3px]',
			className
		)}
	>
		<Stat {label} {value} {sub} {tone} {valueTone} {icon} {delta} />
		<ChevronRightIcon
			class="text-muted-foreground/50 group-hover/tile:text-muted-foreground absolute top-4 right-4 size-4 transition-colors"
		/>
	</a>
{:else if onclick}
	<button
		type="button"
		{onclick}
		aria-pressed={pressed}
		class={cn(
			surface,
			'cursor-pointer text-left hover:-translate-y-0.5 hover:shadow-[var(--elev-2)]',
			'focus-visible:ring-ring/50 focus-visible:outline-none focus-visible:ring-[3px]',
			pressed && 'ring-ring/50 ring-[3px]',
			className
		)}
	>
		<Stat {label} {value} {sub} {tone} {valueTone} {icon} {delta} />
	</button>
{:else}
	<div class={cn(surface, className)}>
		<Stat {label} {value} {sub} {tone} {valueTone} {icon} {delta} />
	</div>
{/if}
