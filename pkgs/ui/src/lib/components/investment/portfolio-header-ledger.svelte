<script lang="ts">
	import type { PerformancePoint } from '../charts/performance-chart.svelte';
	import Sparkline from '../charts/sparkline.svelte';
	import { Money } from '../ui/money/index.js';
	import { formatPct, formatPctPlain } from '../../money.js';
	import AnimatedMoney from './animated-money.svelte';
	import HeaderActions, {
		shownMoneyButtons,
		type HeaderActionsMode
	} from './header-actions.svelte';
	import PeriodPills from './period-pills.svelte';
	import {
		changeDirection,
		PERIOD_LABELS,
		slicePeriod,
		type ChangeDirection,
		type PeriodKey
	} from './periods.js';
	import { asAt, periodBasis } from './provenance.js';

	// The portfolioHeader 'ledger' variant — the private-bank, typographic look. Deliberately
	// NO ink card and NO big chart: page ground, hairline rules, the total set very large in the
	// display face, and the period change as a quiet inline figure with a tiny sparkline. Think
	// engraved statement, not app. Explicit choice only — auto never picks it.
	let {
		total,
		gain,
		gainPct,
		performance,
		label = 'Portfolio value',
		actions = 'deposit_withdraw',
		disabledActions = [],
		defaultPeriod = '6M',
		asOf = null,
		period = null,
		showChart = true,
		interactive = true
	}: {
		total: number;
		gain: number;
		gainPct: number | null;
		performance: PerformancePoint[];
		label?: string;
		actions?: HeaderActionsMode;
		/** Action keys the firm has switched off. The pair omits those buttons. */
		disabledActions?: readonly string[];
		defaultPeriod?: PeriodKey;
		/** Valuation stamp (ISO date). Rendered only when present — never fabricated. */
		asOf?: string | null;
		period?: { from: string; to: string } | null;
		/** There is no big chart to hide, so this only stills the inline sparkline. */
		showChart?: boolean;
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
	const gainDirection = $derived(gainPct == null ? null : changeDirection(gain));
	const invested = $derived(total - gain);
	const periodHigh = $derived(sliced.length ? Math.max(...sliced.map((p) => p.value)) : total);

	// Page-ground change ink (NOT periods.ts' DIRECTION_COLOR, which is tuned for hero ink
	// surfaces): the ledger sits on paper, so it uses the page-level tokens.
	const PAGE_DIRECTION: Record<ChangeDirection, string> = {
		up: 'var(--positive)',
		down: 'var(--negative)',
		flat: 'var(--muted-foreground)'
	};

	const stamp = $derived(asAt(asOf));
	const basis = $derived(periodBasis(period));
	const money = $derived(shownMoneyButtons(actions, disabledActions));
	const showMoney = $derived(actions === 'contact' || money.payment || money.withdraw);
</script>

<div class="ledger-header py-1">
	<div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
		<p class="text-muted-foreground text-xs font-medium tracking-[0.18em] uppercase">
			{label}
		</p>
		{#if stamp}
			<p class="text-muted-foreground text-xs">{stamp}</p>
		{/if}
	</div>

	<div class="border-border mt-3 border-t"></div>

	<!-- The total, set in the display face. `--font-display` is the tenant-themable display
	     family; the exact fallback chain matters — an unthemed tenant gets the body face, not
	     the UA serif. -->
	<p
		class="mt-5 text-5xl font-medium tracking-tight tabular-nums sm:text-6xl"
		style="font-family: var(--font-display, var(--font-sans))"
	>
		<AnimatedMoney amount={total} animate={interactive} minorClass="text-muted-foreground" />
	</p>

	<div class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2.5">
		<p class="flex items-center gap-1.5 text-sm tabular-nums">
			<span class="font-medium" style="color: {PAGE_DIRECTION[direction]}">
				<!-- Flat is unsigned: no change is not a gain. -->
				{#if direction === 'flat'}
					<Money amount={change} /> ({formatPctPlain(changePct)})
				{:else}
					<Money amount={change} signed /> ({formatPct(changePct)})
				{/if}
			</span>
			<span class="text-muted-foreground"
				>· {PERIOD_LABELS[selected]}{#if basis}&nbsp;· {basis}{/if}</span
			>
		</p>
		{#if showChart && sliced.length > 1}
			<!-- The quiet inline trace — deliberately monochrome (muted ink), because the signed
			     figure beside it already carries the direction; colouring both would shout. -->
			<span class="text-muted-foreground" aria-hidden="true">
				<Sparkline data={sliced.map((p) => p.value)} width={96} height={24} class="h-6 w-24" />
			</span>
		{/if}
		<div class="ms-auto">
			<PeriodPills {selected} onselect={(key) => (selected = key)} tone="page" {interactive} />
		</div>
	</div>

	<div class="border-border mt-5 border-t"></div>

	<!-- The statement figures: a keyed row of engraved label/value pairs. -->
	<dl class="mt-4 flex flex-wrap gap-x-10 gap-y-3">
		{#if invested >= 0}
			<!-- Withdrawals can push total − gain negative, and a negative "Invested" is
			     nonsense — omit the stat rather than show it (matches the hero). -->
			<div>
				<dt class="text-muted-foreground text-xs tracking-[0.14em] uppercase">Invested</dt>
				<dd class="mt-1 text-sm font-medium tabular-nums"><Money amount={invested} /></dd>
			</div>
		{/if}
		<div>
			<dt class="text-muted-foreground text-xs tracking-[0.14em] uppercase">
				Growth since investment
			</dt>
			<dd
				class="mt-1 text-sm font-medium tabular-nums"
				style="color: {gainDirection == null || gainDirection === 'flat'
					? 'inherit'
					: PAGE_DIRECTION[gainDirection]}"
			>
				{#if gainDirection == null}
					—
				{:else if gainDirection === 'flat'}
					<Money amount={gain} /> ({formatPctPlain(gainPct ?? 0)})
				{:else}
					<Money amount={gain} signed /> ({formatPct(gainPct ?? 0)})
				{/if}
			</dd>
		</div>
		<div>
			<dt class="text-muted-foreground text-xs tracking-[0.14em] uppercase">Period high</dt>
			<dd class="mt-1 text-sm font-medium tabular-nums"><Money amount={periodHigh} /></dd>
		</div>
	</dl>

	{#if showMoney}
		<div class="mt-5">
			<HeaderActions mode={actions} disabled={disabledActions} tone="page" {interactive} />
		</div>
	{/if}
</div>
