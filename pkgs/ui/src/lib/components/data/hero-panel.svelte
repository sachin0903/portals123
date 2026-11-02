<script lang="ts" module>
	export interface HeroMetric {
		label: string;
		value: string;
	}
	export interface HeroDelta {
		value: string;
		direction?: 'up' | 'down' | 'flat';
		/** Trailing context, e.g. "past 6 months". Rendered muted after the delta. */
		note?: string;
	}
</script>

<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import ArrowDownRightIcon from '@lucide/svelte/icons/arrow-down-right';
	import { cn } from '../../utils.js';

	/**
	 * The institutional hero band — the dark anchor at the top of a dashboard or record screen.
	 * One headline figure, an optional delta, an optional metric footer, and slots for actions
	 * (top-right) and an aside (a sparkline, a period toggle). Purely presentational: all depth
	 * comes from the `hero-surface` utility so it re-themes with the tenant's tokens and can
	 * never drift from PortfolioHeader.
	 *
	 * Use ONE per screen, directly under the PageHeader. If a screen has no headline figure worth
	 * anchoring, it doesn't get a hero — an empty one is worse than none.
	 */
	let {
		eyebrow,
		value,
		delta,
		metrics = [],
		icon: Icon,
		actions,
		aside,
		children,
		class: className
	}: {
		/** The small tracked label above the figure, e.g. "Assets under advice". */
		eyebrow: string;
		/** The headline figure. Rendered tabular so digits don't jitter as it updates. */
		value: string;
		delta?: HeroDelta;
		/** Footer strip of supporting figures, divided from the headline by a hairline. */
		metrics?: HeroMetric[];
		icon?: Component<{ class?: string }>;
		actions?: Snippet;
		aside?: Snippet;
		children?: Snippet;
		class?: string;
	} = $props();

	const dir = $derived(delta?.direction ?? 'flat');
	const deltaColour = $derived(
		dir === 'up'
			? 'var(--hero-positive)'
			: dir === 'down'
				? 'var(--hero-negative)'
				: 'var(--hero-muted)'
	);
</script>

<div class={cn('hero-surface rounded-xl p-5 sm:p-6', className)}>
	<span class="hero-hairline"></span>

	<div class="relative flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
		<div class="min-w-0">
			<p class="flex items-center gap-2 text-xs font-medium" style="color: var(--hero-muted)">
				{#if Icon}<Icon class="size-3.5" />{/if}
				{eyebrow}
			</p>
			<p class="mt-1.5 text-3xl font-semibold tracking-tight tabular-nums sm:text-4xl">
				{value}
			</p>
			{#if delta}
				<p class="mt-1.5 flex items-center gap-1.5 text-sm font-medium tabular-nums">
					<span class="inline-flex items-center gap-1" style="color: {deltaColour}">
						{#if dir === 'up'}<ArrowUpRightIcon class="size-4" />{:else if dir === 'down'}
							<ArrowDownRightIcon class="size-4" />
						{/if}
						{delta.value}
					</span>
					{#if delta.note}
						<span style="color: var(--hero-muted)">· {delta.note}</span>
					{/if}
				</p>
			{/if}
		</div>

		{#if actions || aside}
			<div class="flex shrink-0 flex-col items-end gap-2.5">
				{#if actions}<div class="flex items-center gap-2">{@render actions()}</div>{/if}
				{#if aside}{@render aside()}{/if}
			</div>
		{/if}
	</div>

	{#if children}
		<div class="relative mt-4">{@render children()}</div>
	{/if}

	{#if metrics.length}
		<div
			class="relative mt-4 flex flex-wrap gap-x-6 gap-y-1.5 border-t pt-3 text-xs"
			style="border-color: color-mix(in oklch, var(--hero-foreground) 12%, transparent); color: var(--hero-muted)"
		>
			{#each metrics as m (m.label)}
				<span>
					{m.label}
					<span class="tabular-nums" style="color: var(--hero-foreground)">{m.value}</span>
				</span>
			{/each}
		</div>
	{/if}
</div>
