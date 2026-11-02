<script lang="ts">
	import type { PerformancePoint } from '../charts/performance-chart.svelte';
	import PerformanceChart from '../charts/performance-chart.svelte';
	import type { HeaderActionsMode } from './header-actions.svelte';
	import HeaderSummary from './header-summary.svelte';
	import { changeDirection, PERIOD_DESCRIPTIONS, slicePeriod, type PeriodKey } from './periods.js';

	// The portfolioHeader 'canvas' variant — the immersive look. A full-card colour field is
	// DERIVED from the firm's own --brand (see the style-block notes: hue passes through,
	// lightness and chroma are pinned the way categorical.ts pins its scale, so a red, a green
	// and a navy firm all get a deep, luxe field with legible near-white ink), with two gradient
	// blobs drifting very slowly under the content. The performance line renders as a subtle
	// luminous path bled across the card's foot. Explicit choice only — auto never picks it.
	// (NB: a literal style-tag string must never appear inside this script — svelte2tsx's tag
	// scanner, unlike the compiler proper, trips on it and svelte-check reports the script as
	// left open.)
	// No `gain`/`gainPct` here on purpose: the canvas is the minimal, immersive look — total +
	// period change only. The since-investment stats belong to the hero's footer row and the
	// ledger's statement figures.
	let {
		total,
		performance,
		label = 'Portfolio value',
		actions = 'deposit_withdraw',
		disabledActions = [],
		defaultPeriod = '6M',
		showChart = true,
		interactive = true
	}: {
		total: number;
		performance: PerformancePoint[];
		label?: string;
		actions?: HeaderActionsMode;
		/** Action keys the firm has switched off. The pair omits those buttons. */
		disabledActions?: readonly string[];
		defaultPeriod?: PeriodKey;
		/** Accepted for parity with the ledger look, which prints them; the canvas shows no dates
		 *  (see header-summary.svelte). */
		asOf?: string | null;
		period?: { from: string; to: string } | null;
		/** The block's show_chart setting: false keeps the field but drops the luminous path. */
		showChart?: boolean;
		/** False renders the final, inert state: static field, static total, inert controls. */
		interactive?: boolean;
	} = $props();

	// Seeded from the prop on purpose (the period the header OPENS on); clicks win thereafter.
	// svelte-ignore state_referenced_locally
	let selected = $state<PeriodKey>(defaultPeriod);

	const sliced = $derived(slicePeriod(performance, selected));
	const first = $derived(sliced[0]?.value ?? 0);
	const last = $derived(sliced[sliced.length - 1]?.value ?? 0);
	const change = $derived(sliced.length > 1 ? last - first : 0);
	const changePct = $derived(sliced.length > 1 && first ? change / first : 0);
	const direction = $derived(changeDirection(change));
</script>

<div class="canvas-surface relative overflow-hidden rounded-xl" class:canvas-live={interactive}>
	<!-- The colour field: painted layers only, never in the a11y tree. -->
	<div class="canvas-field absolute inset-0" aria-hidden="true">
		<span class="canvas-blob canvas-blob-a"></span>
		<span class="canvas-blob canvas-blob-b"></span>
	</div>
	<!-- Brand specular hairline along the top edge (shared utility — see theme.css). -->
	<span class="hero-hairline"></span>

	<div class="relative p-5 sm:p-6">
		<HeaderSummary
			{label}
			{total}
			{change}
			{changePct}
			{direction}
			{selected}
			onselect={(key) => (selected = key)}
			{actions}
			{disabledActions}
			{interactive}
			size="canvas"
		/>
	</div>

	{#if showChart}
		<!-- The luminous path, bled to the card edges so the line reads as part of the field
		     rather than a chart pasted on it. -->
		<div class="relative -mt-1">
			<PerformanceChart
				data={sliced}
				height={88}
				id="canvas"
				tone="luminous"
				{interactive}
				periodDescription={PERIOD_DESCRIPTIONS[selected]}
			/>
		</div>
	{:else}
		<div class="pb-4"></div>
	{/if}
</div>

<style>
	.canvas-surface {
		/* Fallback field for engines without relative colour syntax: the stock brand hue (202 —
		   categorical.ts's STOCK_BRAND_HUE) at the same pinned anchors, so the card is still a
		   deep colour field, just not the firm's own. */
		--canvas-deep: oklch(0.21 0.05 202);
		--canvas-mid: oklch(0.29 0.08 202);
		--canvas-glow: oklch(0.46 0.11 226);
		--canvas-cool: oklch(0.34 0.09 166);
		color: var(--hero-foreground);
		background-color: var(--canvas-deep);
		box-shadow: var(--hero-shadow);
	}
	@supports (color: oklch(from red l c h)) {
		.canvas-surface {
			/* Derived from the firm's --brand exactly the way categorical.ts derives its scale:
			   the HUE passes through arithmetic untouched (so the field is unmistakably the
			   firm's), while LIGHTNESS is pinned dark (0.21–0.46 — near-white ink always reads)
			   and CHROMA is both floored (a near-grey brand still yields a coloured field, not
			   mud) and capped (a neon brand cannot bloom the card into an advert). The glow and
			   cool anchors rotate ±~30° off the brand hue so the field has depth rather than a
			   single flat wash. Numeric anchors on purpose — commented per the design law. */
			--canvas-deep: oklch(from var(--brand) 0.21 clamp(0.04, c, 0.07) h);
			--canvas-mid: oklch(from var(--brand) 0.29 clamp(0.06, c, 0.11) h);
			--canvas-glow: oklch(from var(--brand) 0.46 clamp(0.09, calc(c * 1.2), 0.15) calc(h + 24));
			--canvas-cool: oklch(from var(--brand) 0.34 clamp(0.07, c, 0.12) calc(h - 36));
		}
	}

	.canvas-field {
		background:
			radial-gradient(120% 90% at 85% -10%, var(--canvas-glow), transparent 62%),
			radial-gradient(90% 80% at -10% 110%, var(--canvas-cool), transparent 58%),
			linear-gradient(160deg, var(--canvas-mid), var(--canvas-deep) 72%);
	}
	.canvas-blob {
		position: absolute;
		aspect-ratio: 1;
		border-radius: 50%;
		will-change: transform;
	}
	.canvas-blob-a {
		width: 55%;
		left: 6%;
		top: -60%;
		opacity: 0.7;
		background: radial-gradient(closest-side, var(--canvas-glow), transparent 72%);
	}
	.canvas-blob-b {
		width: 72%;
		right: -20%;
		bottom: -70%;
		opacity: 0.6;
		background: radial-gradient(closest-side, var(--canvas-cool), transparent 70%);
	}
	/* The drift runs only on a live surface (previews stay still) and transform-only (GPU
	   compositing, no repaint). Reduced-motion stills it entirely — the static field IS the
	   design's resting state, not a degraded one. */
	.canvas-live .canvas-blob-a {
		animation: canvas-drift-a 26s ease-in-out infinite alternate;
	}
	.canvas-live .canvas-blob-b {
		animation: canvas-drift-b 34s ease-in-out infinite alternate;
	}
	@keyframes canvas-drift-a {
		from {
			transform: translate3d(0, 0, 0) scale(1);
		}
		to {
			transform: translate3d(12%, 14%, 0) scale(1.15);
		}
	}
	@keyframes canvas-drift-b {
		from {
			transform: translate3d(0, 0, 0) scale(1);
		}
		to {
			transform: translate3d(-10%, -12%, 0) scale(1.08);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.canvas-blob-a,
		.canvas-blob-b {
			animation: none !important;
		}
	}
</style>
