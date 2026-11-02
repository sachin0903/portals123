<script lang="ts">
	import TrendingDownIcon from '@lucide/svelte/icons/trending-down';
	import MinusIcon from '@lucide/svelte/icons/minus';
	import TrendingUpIcon from '@lucide/svelte/icons/trending-up';
	import type { FundLookthroughData } from './types.js';
	import { gainDirection, gainTone, PCT_DIGITS } from '../../../money.js';

	let { history }: { history: FundLookthroughData['nav_history'] } = $props();

	const values = $derived(history.points.map((point) => point.nav));
	const low = $derived(values.length ? Math.min(...values) : 0);
	const high = $derived(values.length ? Math.max(...values) : 0);
	const spread = $derived(high - low || Math.max(high * 0.02, 1));
	const coordinates = $derived(
		history.points.map((point, index) => ({
			...point,
			x: history.points.length === 1 ? 50 : 4 + (index / (history.points.length - 1)) * 92,
			y: 29 - ((point.nav - low) / spread) * 23
		}))
	);
	const line = $derived(coordinates.map((point) => `${point.x},${point.y}`).join(' '));
	const area = $derived(
		coordinates.length ? `4,31 ${line} ${coordinates[coordinates.length - 1]?.x ?? 96},31` : ''
	);
	const number = (value: number) =>
		value.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
	const pct = (value: number) =>
		value.toLocaleString('en-GB', {
			style: 'percent',
			signDisplay: 'always',
			maximumFractionDigits: 1
		});
	const shortDate = (value: string) =>
		new Date(`${value}T00:00:00`).toLocaleDateString('en-GB', {
			day: 'numeric',
			month: 'short'
		});
</script>

<div>
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div>
			<h2 class="font-semibold">NAV performance</h2>
			<p class="text-muted-foreground mt-1 text-xs">
				{history.class_name ?? `Share class ${history.class_code ?? 'fund'}`} · workbook history
			</p>
		</div>
		{#if history.change != null}
			<div
				class="flex items-center gap-1.5 {gainTone(history.change, {
					digits: PCT_DIGITS,
					family: 'directional'
				})}"
			>
				{#if gainDirection(history.change, PCT_DIGITS) === null}
					<MinusIcon class="size-4" />
				{:else if history.change > 0}
					<TrendingUpIcon class="size-4" />
				{:else}
					<TrendingDownIcon class="size-4" />
				{/if}
				<span class="font-semibold tabular-nums">{pct(history.change)}</span>
			</div>
		{/if}
	</div>

	{#if coordinates.length > 1}
		<svg
			viewBox="0 0 100 34"
			class="mt-5 h-44 w-full overflow-visible"
			role="img"
			aria-labelledby="nav-chart-title nav-chart-desc"
		>
			<title id="nav-chart-title">Net asset value history</title>
			<desc id="nav-chart-desc">
				NAV moved from {number(coordinates[0]!.nav)} to {number(
					coordinates[coordinates.length - 1]!.nav
				)} across {coordinates.length} reported dates.
			</desc>
			<defs>
				<!-- The firm's brand under the line, fading to nothing — the same treatment the
				     portfolio chart gives a performance series, so this reads as the same product. -->
				<linearGradient id="nav-area-fill" x1="0" y1="0" x2="0" y2="1">
					<stop offset="0%" stop-color="var(--brand)" stop-opacity="0.32" />
					<stop offset="100%" stop-color="var(--brand)" stop-opacity="0" />
				</linearGradient>
				<linearGradient id="nav-line-stroke" x1="0" y1="0" x2="1" y2="0">
					<stop offset="0%" stop-color="var(--primary)" />
					<stop offset="100%" stop-color="var(--brand)" />
				</linearGradient>
			</defs>
			<line x1="4" y1="6" x2="96" y2="6" class="stroke-border" stroke-width="0.35" />
			<line x1="4" y1="17.5" x2="96" y2="17.5" class="stroke-border" stroke-width="0.35" />
			<line x1="4" y1="29" x2="96" y2="29" class="stroke-border" stroke-width="0.35" />
			<polygon points={area} fill="url(#nav-area-fill)" />
			<polyline
				points={line}
				fill="none"
				stroke="url(#nav-line-stroke)"
				stroke-width="1.15"
				stroke-linecap="round"
				stroke-linejoin="round"
			/>
			{#each coordinates as point, index (point.date)}
				{#if index === 0 || index === coordinates.length - 1}
					<circle
						cx={point.x}
						cy={point.y}
						r="1.6"
						class="fill-background"
						stroke={index === 0 ? 'var(--primary)' : 'var(--brand)'}
						stroke-width="0.8"
					/>
				{/if}
			{/each}
			<text x="4" y="33.5" class="fill-muted-foreground text-[2.3px]">
				{shortDate(coordinates[0]!.date)}
			</text>
			<text x="96" y="33.5" text-anchor="end" class="fill-muted-foreground text-[2.3px]">
				{shortDate(coordinates[coordinates.length - 1]!.date)}
			</text>
			<text x="4" y="4.2" class="fill-muted-foreground text-[2.3px]">{number(high)}</text>
			<text x="4" y="27.2" class="fill-muted-foreground text-[2.3px]">{number(low)}</text>
		</svg>
	{:else}
		<p class="text-muted-foreground py-12 text-center text-sm">
			Performance history will appear after a workbook supplies at least two NAV observations.
		</p>
	{/if}
</div>
