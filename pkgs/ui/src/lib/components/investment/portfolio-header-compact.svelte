<script lang="ts">
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import ArrowDownRightIcon from '@lucide/svelte/icons/arrow-down-right';
	import { Money } from '../ui/money/index.js';
	import { formatPct, formatPctPlain } from '../../money.js';
	// periods.ts owns the three-state sign logic; sharing it keeps every header look in step.
	import { changeDirection } from './periods.js';

	// The portfolioHeader 'compact' variant: the headline number and return as a single-row
	// strip, no performance chart — for secondary pages (and heroes with show_chart off) where
	// the full hero would shout. Same hero surface as the full header so the pair read as one
	// component at two sizes.
	let {
		total,
		gain,
		gainPct,
		label = 'Portfolio value'
	}: {
		total: number;
		gain: number;
		gainPct: number | null;
		/** Uppercase eyebrow label — matches PortfolioHeader's. */
		label?: string;
	} = $props();

	// Three states, not two: zero must render neutral (muted, no arrow, unsigned) — a flat
	// or day-one portfolio pilled as a green "+£0.00" claims a gain that never happened.
	const direction = $derived(gainPct == null ? null : changeDirection(gain));
	const PILL_COLOR = {
		up: 'var(--hero-positive)',
		down: 'var(--hero-negative)',
		flat: 'var(--hero-muted)'
	} as const;
</script>

<div class="hero-surface rounded-xl px-5 py-4">
	<!-- Brand specular hairline along the top edge (shared utility — see theme.css). -->
	<span class="hero-hairline"></span>

	<div class="relative flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
		<div class="min-w-0">
			<p
				class="text-[0.65rem] font-medium tracking-[0.18em] uppercase"
				style="color: var(--hero-muted)"
			>
				{label}
			</p>
			<p class="mt-0.5 text-xl font-semibold tracking-tight tabular-nums">
				<Money amount={total} minorClass="text-[color:var(--hero-muted)]" />
			</p>
		</div>

		<span
			class="inline-flex shrink-0 items-center gap-1 rounded-full px-3 py-1 text-sm font-medium tabular-nums"
			style="background-color: color-mix(in oklch, var(--hero-foreground) 8%, transparent); color: {direction ==
			null
				? 'var(--hero-muted)'
				: PILL_COLOR[direction]}"
		>
			{#if direction === 'up'}<ArrowUpRightIcon
					class="size-4"
				/>{:else if direction === 'down'}<ArrowDownRightIcon class="size-4" />{/if}
			{#if direction == null}
				—
			{:else if direction === 'flat'}
				<Money amount={gain} minorClass="text-[color:var(--hero-muted)]" /> ({formatPctPlain(
					gainPct ?? 0
				)})
			{:else}
				<Money amount={gain} signed minorClass="text-[color:var(--hero-muted)]" /> ({formatPct(
					gainPct ?? 0
				)})
			{/if}
		</span>
	</div>
</div>
