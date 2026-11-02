<script lang="ts">
	import type { PerformancePoint } from '../charts/performance-chart.svelte';
	import PerformanceChart from '../charts/performance-chart.svelte';
	import { Money } from '../ui/money/index.js';
	import { formatPct, formatPctPlain } from '../../money.js';
	import type { HeaderActionsMode } from './header-actions.svelte';
	import HeaderSummary from './header-summary.svelte';
	import {
		changeDirection,
		DIRECTION_COLOR,
		PERIOD_DESCRIPTIONS,
		slicePeriod,
		type PeriodKey
	} from './periods.js';

	// The portfolioHeader 'hero' variant: the institutional ink card with the full performance
	// chart. The period/change/actions logic is shared with the canvas and ledger looks via
	// periods.ts / header-actions.svelte / period-pills.svelte, so the three variants answer
	// "what changed, over when, coloured how" identically.
	let {
		total,
		gain,
		gainPct,
		performance,
		label = 'Portfolio value',
		actions = 'deposit_withdraw',
		disabledActions = [],
		defaultPeriod = '6M',
		interactive = true
	}: {
		total: number;
		gain: number;
		gainPct: number | null;
		performance: PerformancePoint[];
		/** Uppercase eyebrow label — override to reuse on the platform dashboard. */
		label?: string;
		/** Which action row to show — 'none' for read-only adviser/firm views. */
		actions?: HeaderActionsMode;
		/** Action keys the firm has switched off. The pair omits those buttons. */
		disabledActions?: readonly string[];
		/** The period the header opens on (the block's `default_period` setting, pre-normalised). */
		defaultPeriod?: PeriodKey;
		/** Accepted for parity with the canvas/ledger looks, which print them. The hero does not:
		 *  on a narrow card the stamp and basis wrapped into a third and fourth line of dates
		 *  above the figures they qualified. */
		asOf?: string | null;
		period?: { from: string; to: string } | null;
		/** False renders the final, inert state: static total, inert pills/chart (previews). */
		interactive?: boolean;
	} = $props();

	// Period selector — reslices the series (by date when the labels carry one — see
	// slicePeriod) and recomputes the return over the visible window, so the headline
	// change reflects the chosen range. Seeding from the prop is deliberate: default_period
	// is the period the header OPENS on; the client's own clicks must win thereafter.
	// svelte-ignore state_referenced_locally
	let selected = $state<PeriodKey>(defaultPeriod);

	const sliced = $derived(slicePeriod(performance, selected));
	const first = $derived(sliced[0]?.value ?? 0);
	const last = $derived(sliced[sliced.length - 1]?.value ?? 0);
	const change = $derived(sliced.length > 1 ? last - first : 0);
	const changePct = $derived(sliced.length > 1 && first ? change / first : 0);
	const direction = $derived(changeDirection(change));
	// Growth since investment is over the WHOLE holding, not the selected window.
	const gainDirection = $derived(gainPct == null ? null : changeDirection(gain));
	const invested = $derived(total - gain);
	const periodHigh = $derived(sliced.length ? Math.max(...sliced.map((p) => p.value)) : total);
</script>

<div class="hero-surface rounded-xl p-5 sm:p-6">
	<!-- Brand specular hairline along the top edge (shared utility — see theme.css). -->
	<span class="hero-hairline"></span>

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
	/>

	<div class="relative mt-3 -mb-1">
		<PerformanceChart
			data={sliced}
			height={84}
			id="hero"
			{interactive}
			periodDescription={PERIOD_DESCRIPTIONS[selected]}
		/>
	</div>

	<!-- compact summary stats — single line -->
	<div
		class="relative mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t pt-3 text-xs"
		style="border-color: color-mix(in oklch, var(--hero-foreground) 12%, transparent); color: var(--hero-muted)"
	>
		<!-- Withdrawals can push total − gain negative, and a negative "Invested" is nonsense —
		     omit the stat rather than show it. -->
		{#if invested >= 0}
			<span
				>Invested <span class="tabular-nums" style="color: var(--hero-foreground)"
					><Money amount={invested} minorClass="text-[color:var(--hero-muted)]" /></span
				></span
			>
		{/if}
		<!-- "Growth since investment", not "Return": it is gain/(value − gain) since inception,
		     different maths from the period change above — sharing that line's name gave two
		     different figures one label. -->
		<span>
			Growth since investment
			<span
				class="tabular-nums"
				style="color: {gainDirection == null || gainDirection === 'flat'
					? 'var(--hero-foreground)'
					: DIRECTION_COLOR[gainDirection]}"
			>
				{#if gainDirection == null}
					—
				{:else if gainDirection === 'flat'}
					<Money amount={gain} minorClass="text-[color:var(--hero-muted)]" /> ({formatPctPlain(
						gainPct ?? 0
					)})
				{:else}
					<Money amount={gain} signed minorClass="text-[color:var(--hero-muted)]" /> ({formatPct(
						gainPct ?? 0
					)})
				{/if}
			</span>
		</span>
		<span
			>Period high <span class="tabular-nums" style="color: var(--hero-foreground)"
				><Money amount={periodHigh} minorClass="text-[color:var(--hero-muted)]" /></span
			></span
		>
	</div>
</div>
