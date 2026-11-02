<script lang="ts" module>
	export interface PerformancePoint {
		label: string;
		value: number;
	}
</script>

<script lang="ts">
	import { line as d3line, area as d3area, curveMonotoneX } from 'd3-shape';
	import { scaleLinear } from 'd3-scale';
	import { formatGBP } from '../../money.js';
	import { cn } from '../../utils.js';
	import { britishDate } from '../investment/provenance.js';
	import { chartAriaLabel, chipFraction, nearestIndex } from './scrub.js';

	let {
		data,
		height = 240,
		id = 'perf',
		class: className,
		interactive = true,
		tone = 'brand',
		periodDescription = 'Portfolio'
	}: {
		data: PerformancePoint[];
		height?: number;
		/** Unique id for the gradient (set when several charts share a page). */
		id?: string;
		class?: string;
		/** False renders the final, inert state: no scrub, no focus stop, no mount animations —
		 * the studio preview and any other read-only rendering. */
		interactive?: boolean;
		/** 'brand' draws in the firm's accent (the hero); 'luminous' draws a subtle near-white
		 * path for dark immersive surfaces (the canvas hero), keyed off --hero-foreground so it
		 * re-themes with the hero ink rather than assuming white. */
		tone?: 'brand' | 'luminous';
		/** Leads the generated aria-label, e.g. '6-month' → "6-month performance: …". */
		periodDescription?: string;
	} = $props();

	// Measured container width. The viewBox FOLLOWS the measurement, so post-measure the SVG
	// user space is 1:1 with CSS pixels and nothing distorts at wide spans. The 720 fallback is
	// the SSR/pre-measure frame only — `preserveAspectRatio="none"` is kept PURELY for that
	// frame (a stretched-but-full chart beats a letterboxed one for the instant before
	// hydration measures); once width === clientWidth, "none" is the identity mapping.
	let width = $state(0);
	const W = $derived(width > 0 ? width : 720);

	const padX = 4;
	const padY = 18;
	const values = $derived(data.map((d) => d.value));
	const x = $derived(
		scaleLinear()
			.domain([0, Math.max(1, data.length - 1)])
			.range([padX, W - padX])
	);
	const y = $derived(
		scaleLinear()
			.domain([Math.min(...values), Math.max(...values)])
			.range([height - padY, padY])
			.nice()
	);
	const linePath = $derived(
		d3line<PerformancePoint>()
			.curve(curveMonotoneX)
			.x((_, i) => x(i))
			.y((d) => y(d.value))(data) ?? ''
	);
	const areaPath = $derived(
		d3area<PerformancePoint>()
			.curve(curveMonotoneX)
			.x((_, i) => x(i))
			.y0(height)
			.y1((d) => y(d.value))(data) ?? ''
	);

	// Tone-driven inks. Luminous keys off --hero-foreground (with a white-point fallback via the
	// token's own default) so the path stays legible on any brand-derived dark field.
	const lineColor = $derived(
		tone === 'luminous'
			? 'color-mix(in oklch, var(--hero-foreground) 82%, transparent)'
			: 'var(--brand)'
	);
	const fillColor = $derived(tone === 'luminous' ? 'var(--hero-foreground)' : 'var(--brand)');
	const fillOpacity = $derived(tone === 'luminous' ? 0.14 : 0.28);

	const endpoint = $derived(
		data.length > 0 ? { cx: x(data.length - 1), cy: y(data[data.length - 1]!.value) } : null
	);

	// ── scrubbing ────────────────────────────────────────────────────────────────────────────
	// Pointer/touch moves snap to the nearest point (pure maths in scrub.ts); arrow keys walk
	// the series when the chart is focused. All of it exists only when `interactive`.
	let scrub = $state<number | null>(null);
	const scrubbable = $derived(interactive && data.length > 0);
	// Bounds-checked: a period reslice can shrink the series under a live scrub index.
	const scrubPoint = $derived.by(() => {
		if (scrub === null) return null;
		const p = data[scrub];
		return p ? { i: scrub, label: p.label, value: p.value } : null;
	});
	const scrubLeft = $derived(scrubPoint ? chipFraction(x(scrubPoint.i) / W) : 0);
	// ISO labels read as dates ('29 Jul 2026'); the demo's bare month names pass through as-is.
	const scrubLabel = $derived(scrubPoint ? britishDate(scrubPoint.label) || scrubPoint.label : '');

	function onPointer(e: PointerEvent) {
		if (!scrubbable) return;
		const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
		if (rect.width <= 0) return;
		// Normalise into viewBox space (identical post-measure; proportional pre-measure).
		scrub = nearestIndex(((e.clientX - rect.left) * W) / rect.width, W, data.length, padX);
	}

	function onKeydown(e: KeyboardEvent) {
		if (!scrubbable) return;
		const last = data.length - 1;
		const at = scrub ?? last;
		if (e.key === 'ArrowLeft') scrub = Math.max(0, at - 1);
		else if (e.key === 'ArrowRight') scrub = Math.min(last, at + 1);
		else if (e.key === 'Home') scrub = 0;
		else if (e.key === 'End') scrub = last;
		else if (e.key === 'Escape') scrub = null;
		else return;
		e.preventDefault();
	}

	const ariaLabel = $derived(chartAriaLabel(periodDescription, data));
</script>

<!-- Two honest roles rather than one muddled one: the svg is role="img" with the generated
     performance summary, and the WRAPPER is the scrubber — a genuine role="slider" (arrow keys
     walk the points, aria-valuetext narrates "29 Jul 2026: £171,320.00"), which is exactly the
     interaction model a scrub-a-timeline control has. Inert charts drop the slider semantics
     entirely and are just the picture. The suppression exists because the static checker cannot
     see that tabindex and role="slider" are set under the SAME condition — the element is never
     focusable without its interactive role. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
	class={cn(
		'relative rounded-md focus-visible:outline-2 focus-visible:outline-offset-2',
		className
	)}
	style="outline-color: {lineColor}"
	bind:clientWidth={width}
	role={scrubbable ? 'slider' : undefined}
	aria-label={scrubbable ? `Scrub ${periodDescription} performance` : undefined}
	aria-valuemin={scrubbable ? 0 : undefined}
	aria-valuemax={scrubbable ? data.length - 1 : undefined}
	aria-valuenow={scrubbable ? (scrubPoint?.i ?? data.length - 1) : undefined}
	aria-valuetext={scrubbable && scrubPoint
		? `${scrubLabel}: ${formatGBP(scrubPoint.value)}`
		: undefined}
	tabindex={scrubbable ? 0 : undefined}
	onpointerdown={onPointer}
	onpointermove={onPointer}
	onpointerleave={() => (scrub = null)}
	onkeydown={onKeydown}
	onblur={() => (scrub = null)}
>
	<svg
		viewBox="0 0 {W} {height}"
		class="block w-full touch-pan-y"
		style="height: {height}px"
		preserveAspectRatio="none"
		role="img"
		aria-label={ariaLabel}
	>
		<defs>
			<linearGradient id="grad-{id}" x1="0" y1="0" x2="0" y2="1">
				<stop offset="0%" stop-color={fillColor} stop-opacity={fillOpacity} />
				<stop offset="55%" stop-color={fillColor} stop-opacity={fillOpacity * 0.35} />
				<stop offset="100%" stop-color={fillColor} stop-opacity="0" />
			</linearGradient>
		</defs>
		<path d={areaPath} fill="url(#grad-{id})" />
		<path
			d={linePath}
			fill="none"
			stroke={lineColor}
			stroke-width="2"
			stroke-linejoin="round"
			stroke-linecap="round"
			vector-effect="non-scaling-stroke"
		/>
		{#if scrubPoint}
			<line
				x1={x(scrubPoint.i)}
				y1={padY - 8}
				x2={x(scrubPoint.i)}
				y2={height}
				stroke={lineColor}
				stroke-opacity="0.45"
				stroke-width="1"
				stroke-dasharray="2 3"
				vector-effect="non-scaling-stroke"
			/>
			<circle cx={x(scrubPoint.i)} cy={y(scrubPoint.value)} r="3.5" fill={lineColor} />
		{/if}
		{#if endpoint}
			{#if interactive}
				<!-- One soft pulse on mount (CSS, single iteration, `forwards` so its final state is
				     invisible); reduced-motion never shows it at all. -->
				<circle class="end-pulse" cx={endpoint.cx} cy={endpoint.cy} r="8" fill={lineColor} />
			{/if}
			<circle cx={endpoint.cx} cy={endpoint.cy} r="3.25" fill={lineColor} />
		{/if}
	</svg>

	{#if scrubPoint}
		<!-- Readout chip: popover tokens on purpose — a floating tooltip surface reads correctly
		     over both the page ground and the dark hero/canvas inks. pointer-events-none so it
		     never steals the scrub. -->
		<div
			class="bg-popover text-popover-foreground pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-md border px-2 py-1 text-xs whitespace-nowrap shadow-md"
			style="left: {scrubLeft * 100}%"
		>
			<span class="text-muted-foreground">{scrubLabel}</span>
			<span class="ml-1.5 font-medium tabular-nums">{formatGBP(scrubPoint.value)}</span>
		</div>
	{/if}
</div>

<style>
	.end-pulse {
		transform-box: fill-box;
		transform-origin: center;
		opacity: 0;
		animation: chart-end-pulse 1.1s ease-out 0.25s 1 forwards;
	}
	@keyframes chart-end-pulse {
		0% {
			transform: scale(0.35);
			opacity: 0.55;
		}
		100% {
			transform: scale(2.1);
			opacity: 0;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.end-pulse {
			animation: none;
		}
	}
</style>
