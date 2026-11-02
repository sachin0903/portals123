<script lang="ts">
	// The top of the ink portfolio headers (hero and canvas): the value, the period's change and
	// growth as two labelled figures, the period switcher and the action buttons. One component so
	// both looks place their controls the same way at every width.
	//
	// Laid out by the CARD's width (`@container` on the root), not the viewport — the same block
	// sits full-width on a phone and in a 7-of-12 cell on a desktop. The period switcher shares the
	// eyebrow's line, top right; on a narrow card it collapses to the selected period and grows
	// left along that line when opened. Payment/Withdraw follow the figures at their own size on a
	// narrow card and sit beside the value once it is wide, and "Contact your adviser" only appears
	// there — the shell header already messages the adviser.
	//
	// No dates on purpose: on a narrow card the "as at" stamp and the series basis wrapped into a
	// third and fourth line of dates above the figures they qualified.
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import ArrowDownRightIcon from '@lucide/svelte/icons/arrow-down-right';
	import { Money } from '../ui/money/index.js';
	import { formatPct, formatPctPlain } from '../../money.js';
	import AnimatedMoney from './animated-money.svelte';
	import HeaderActions, {
		shownMoneyButtons,
		type HeaderActionsMode
	} from './header-actions.svelte';
	import PeriodPills from './period-pills.svelte';
	import {
		DIRECTION_COLOR,
		PERIOD_TITLES,
		type ChangeDirection,
		type PeriodKey
	} from './periods.js';

	let {
		label,
		total,
		change,
		changePct,
		direction,
		selected,
		onselect,
		actions,
		disabledActions = [],
		interactive,
		size = 'hero'
	}: {
		label: string;
		total: number;
		change: number;
		changePct: number;
		direction: ChangeDirection;
		selected: PeriodKey;
		onselect: (key: PeriodKey) => void;
		actions: HeaderActionsMode;
		/** Action keys the firm has switched off. The pair omits those buttons. */
		disabledActions?: readonly string[];
		interactive: boolean;
		/** 'canvas' is the immersive look's larger type; 'hero' the standard card. */
		size?: 'hero' | 'canvas';
	} = $props();

	const buttons = $derived(shownMoneyButtons(actions, disabledActions));
	const showActions = $derived(actions === 'contact' || buttons.payment || buttons.withdraw);
	// 'contact' is dropped on a narrow card: the shell's header already carries "Message your
	// adviser", and a full-width button under the figures dominated the card. Payment/Withdraw
	// have no such twin, so they stay — at their own size, never stretched across the card.
	const contact = $derived(actions === 'contact');
</script>

<div class="@container relative">
	<div
		class={[
			'grid gap-4',
			showActions && !contact
				? "[grid-template-areas:'value'_'figures'_'actions'] @md:[grid-template-areas:'value'_'figures']"
				: "[grid-template-areas:'value'_'figures']"
		]}
	>
		<div class="min-w-0 [grid-area:value]">
			<!-- The switcher shares the eyebrow's line, so opening it grows left along that line
			     and never down over the figure. -->
			<div class="flex items-center justify-between gap-3">
				<p
					class={[
						'min-w-0 truncate font-medium tracking-[0.18em] uppercase',
						size === 'canvas' ? 'text-xs' : 'text-[0.65rem]'
					]}
					style="color: var(--hero-muted)"
				>
					{label}
				</p>
				<PeriodPills {selected} {onselect} tone="hero" {interactive} collapsible />
			</div>
			<div class="flex items-end justify-between gap-4">
				<p
					class={[
						'font-semibold tracking-tight tabular-nums',
						size === 'canvas' ? 'mt-2 text-4xl sm:text-5xl' : 'mt-1 text-3xl sm:text-4xl'
					]}
				>
					<AnimatedMoney
						amount={total}
						animate={interactive}
						minorClass="text-[color:var(--hero-muted)]"
					/>
				</p>
				{#if showActions}
					<div class="hidden @md:block">
						<HeaderActions mode={actions} disabled={disabledActions} tone="hero" {interactive} />
					</div>
				{/if}
			</div>
		</div>

		{#if showActions && !contact}
			<div class="[grid-area:actions] @md:hidden">
				<HeaderActions mode={actions} disabled={disabledActions} tone="hero" {interactive} />
			</div>
		{/if}

		<div class="[grid-area:figures]">
			<dl class="flex gap-x-8 tabular-nums">
				<div>
					<dt class="text-xs" style="color: var(--hero-muted)">{PERIOD_TITLES[selected]}</dt>
					<dd
						class="mt-0.5 inline-flex items-center gap-0.5 text-base font-semibold"
						style="color: {DIRECTION_COLOR[direction]}"
					>
						{#if direction === 'up'}<ArrowUpRightIcon
								class="size-4"
							/>{:else if direction === 'down'}<ArrowDownRightIcon class="size-4" />{/if}
						<!-- Flat is unsigned and arrow-less: "+£0.00" with a green arrow reads as a gain. -->
						<Money
							amount={change}
							signed={direction !== 'flat'}
							minorClass="text-[color:var(--hero-muted)]"
						/>
					</dd>
				</div>
				<div>
					<dt class="text-xs" style="color: var(--hero-muted)">Growth</dt>
					<dd class="mt-0.5 text-base font-semibold" style="color: {DIRECTION_COLOR[direction]}">
						{direction === 'flat' ? formatPctPlain(changePct) : formatPct(changePct)}
					</dd>
				</div>
			</dl>
		</div>
	</div>
</div>
