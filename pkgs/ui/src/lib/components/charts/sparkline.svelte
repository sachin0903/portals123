<script lang="ts">
	import { line as d3line, area as d3area, curveMonotoneX } from 'd3-shape';
	import { scaleLinear } from 'd3-scale';

	let {
		data,
		width = 120,
		height = 36,
		bleed = false,
		class: className
	}: {
		data: number[];
		width?: number;
		height?: number;
		/** Edge-to-edge: the line runs from the very left to the very right of the box and the
		 *  fill fades out towards the bottom edge, for a sparkline that sits ON a card's foot
		 *  rather than in a box of its own. The default keeps a 2px inset on every side so the
		 *  stroke is never clipped. */
		bleed?: boolean;
		class?: string;
	} = $props();

	const uid = $props.id();
	const pad = 2;
	// With `bleed`, the x range is the full width (the stroke's clipped half-pixel at each end is
	// the point — the line is meant to meet the edge); the line's low point is lifted a third of
	// the way up so it never scrapes the card's foot, while the fill's baseline IS the bottom edge.
	const floor = $derived(bleed ? height * 0.35 : pad);
	const x = $derived(
		scaleLinear()
			.domain([0, Math.max(1, data.length - 1)])
			.range(bleed ? [0, width] : [pad, width - pad])
	);
	const y = $derived(
		scaleLinear()
			.domain([Math.min(...data), Math.max(...data)])
			.range([height - floor, pad])
	);
	const linePath = $derived(
		d3line<number>()
			.curve(curveMonotoneX)
			.x((_, i) => x(i))
			.y((d) => y(d))(data) ?? ''
	);
	const areaPath = $derived(
		d3area<number>()
			.curve(curveMonotoneX)
			.x((_, i) => x(i))
			.y0(height)
			.y1((d) => y(d))(data) ?? ''
	);
</script>

<svg
	viewBox="0 0 {width} {height}"
	class={className}
	preserveAspectRatio="none"
	fill="none"
	aria-hidden="true"
>
	{#if bleed}
		<defs>
			<linearGradient id="{uid}-fill" x1="0" y1="0" x2="0" y2="1">
				<stop offset="0" stop-color="currentColor" stop-opacity="0.28" />
				<stop offset="1" stop-color="currentColor" stop-opacity="0" />
			</linearGradient>
		</defs>
		<path d={areaPath} fill="url(#{uid}-fill)" />
	{:else}
		<path d={areaPath} fill="currentColor" opacity="0.12" />
	{/if}
	<path d={linePath} stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke" />
</svg>
