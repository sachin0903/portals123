<script lang="ts">
	import type { Component } from 'svelte';
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import ArrowDownRightIcon from '@lucide/svelte/icons/arrow-down-right';
	import { splitStatFigure } from '../../money.js';
	import { cn } from '../../utils.js';

	// A KPI figure: a small muted label, a large tabular value, an optional delta chip and an
	// optional sub-line. This is the ONLY KPI treatment in the product — wrap it in `StatTile`
	// for the standard carded version rather than rebuilding the card at the call site.
	let {
		label,
		value,
		sub,
		tone = 'neutral',
		/** Tints the FIGURE by direction, where `tone` tints the sub beneath it. For a gain/loss, the
		 *  one kind of Stat where up and down are the fact being read — same pairing as
		 *  `Money tone="directional"`, so a figure does not change colour depending on which component
		 *  drew it. Omitted ⇒ the figure takes the ordinary foreground, which is right for a balance. */
		valueTone,
		icon: Icon,
		delta,
		class: className
	}: {
		label: string;
		value: string;
		sub?: string;
		tone?: 'positive' | 'negative' | 'neutral';
		valueTone?: 'positive' | 'negative';
		/** Small muted glyph beside the label — helps scanning a row of tiles. */
		icon?: Component<{ class?: string }>;
		/** A change chip under the value, e.g. { value: '+3', direction: 'up' }. */
		delta?: { value: string; direction?: 'up' | 'down' | 'flat' };
		class?: string;
	} = $props();

	const dir = $derived(delta?.direction ?? 'flat');
	// Pounds at full weight, pence smaller. A directional figure keeps the tint on the pence
	// and steps them back with opacity. The same rule as `Money`.
	const figure = $derived(splitStatFigure(value));
</script>

<div class={cn('space-y-1', className)}>
	<p class="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
		{#if Icon}<Icon class="size-3.5 shrink-0" />{/if}
		{label}
	</p>
	<div class="flex items-baseline gap-2">
		<p
			class={cn(
				'text-xl font-semibold tracking-tight tabular-nums',
				figure && '[font-family:var(--money-display)]',
				valueTone === 'positive' && 'text-success-strong',
				valueTone === 'negative' && 'text-destructive'
			)}
		>
			{#if figure}
				{figure.major}<span
					class={cn('text-[0.85em]', valueTone ? 'opacity-70' : 'text-muted-foreground')}
					>{figure.minor}</span
				>
			{:else}
				{value}
			{/if}
		</p>
		{#if delta}
			<span
				class={cn(
					'inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[0.7rem] font-medium tabular-nums',
					dir === 'up' && 'bg-positive-subtle text-positive-strong',
					dir === 'down' && 'bg-negative-subtle text-negative-strong',
					dir === 'flat' && 'bg-muted text-muted-foreground'
				)}
			>
				{#if dir === 'up'}<ArrowUpRightIcon class="size-3" />{:else if dir === 'down'}
					<ArrowDownRightIcon class="size-3" />
				{/if}
				{delta.value}
			</span>
		{/if}
	</div>
	{#if sub}
		<p
			class={cn(
				'text-xs font-medium',
				tone === 'positive' && 'text-positive',
				tone === 'negative' && 'text-negative',
				tone === 'neutral' && 'text-muted-foreground'
			)}
		>
			{sub}
		</p>
	{/if}
</div>
