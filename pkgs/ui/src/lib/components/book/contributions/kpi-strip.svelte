<script lang="ts">
	// Six figures across the top, one per metric STAGE — and each one is an AMOUNT, not a count.
	//
	// It used to lead with counts ("Classified: 3") and a headline of gross-contributed-this-year.
	// A count answers "how many rows" where the question an adviser and a compliance reviewer both
	// actually ask is "how much money is sitting there" — £3 stuck at the administrator and £30,000
	// stuck at the administrator are the same 1 in a count. So the figure is the money, and the
	// count moved to the sub-line, where it says what it is a count OF: transactions before
	// anything downstream has been told, contributions after.
	//
	// The stages come from `status.ts::METRIC_STAGES` rather than being reduced ad hoc here — the
	// same list drives the queue's status filter, so a bucket cannot exist in one and not the other.
	import { METRIC_STAGES } from './status.js';
	import { isDetected } from './detected.js';
	import type { Contribution, StageSummary } from './types';
	import { Card } from '../../ui/card/index.js';
	import { Money } from '../../ui/money/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';

	let {
		rows,
		summary = null
	}: {
		rows: Contribution[];
		/** WHOLE-BOOK totals from the API (`stage_totals`). When absent — a mount that reads the
		 *  bare list, or a test — the figures fall back to the rows on screen, which is right for a
		 *  page that holds the whole book and honest nowhere else. */
		summary?: StageSummary | null;
	} = $props();

	/** The credits the feed has DETECTED, which the server summary does not know about: they have
	 *  no `platform.contributions` row yet, and they come from a second, unpaged endpoint. Counted
	 *  on top of the summary's To classify rather than instead of it — they are the
	 *  front of that stage, and dropping them would understate the only figure an adviser opened
	 *  the page to act on. */
	const detected = $derived(rows.filter((c) => isDetected(c.uid)));

	// Accents are semantic tokens, not palette colours, so a firm's branding reaches them
	// (token-hygiene.test.ts). They read as one scale rather than six hues: warning for what needs
	// you, info for what is in flight at the administrator, success for relief arriving, and
	// destructive only when something is actually wrong (nothing downstream has been told).
	const ACCENT: Record<string, string> = {
		awaiting_classification: 'border-t-warning',
		with_administrator: 'border-t-info',
		relief_claimed: 'border-t-success',
		relief_in_transit: 'border-t-success',
		relief_credited: 'border-t-success'
	};

	const sum = (list: Contribution[]) => list.reduce((total, c) => total + c.amount, 0);

	const kpis = $derived(
		METRIC_STAGES.map((stage) => {
			const onScreen = rows.filter((c) => stage.statuses.includes(c.status));
			const book = summary?.[stage.key];
			const extra = stage.key === 'awaiting_classification' ? detected : [];
			// The amount PAID, never the gross: the gross includes relief HMRC has not paid yet, so
			// totalling it would state money the scheme does not hold.
			const amount = book ? book.amount + sum(extra) : sum(onScreen);
			const count = book ? book.count + extra.length : onScreen.length;
			const noun = stage.unit === 'transactions' ? 'transaction' : 'contribution';
			return {
				key: stage.key,
				label: stage.label,
				amount,
				count: `${count} ${noun}${count === 1 ? '' : 's'}`,
				actor: stage.actor,
				// "Not lodged" is the only stage that is a FAILURE rather than a step, so it is the
				// only one that goes destructive — and only when it is non-empty, since an empty
				// failure bucket is good news and should not be red.
				accent:
					stage.key === 'not_lodged'
						? count > 0
							? 'border-t-destructive'
							: 'border-t-success'
						: (ACCENT[stage.key] ?? 'border-t-brand')
			};
		})
	);
</script>

<!-- ONE ROW, from `lg`. The strip used to be an `auto-fit, minmax(12rem, 1fr)` grid, which at 1440
     with the sidebar open fits five cards and drops the sixth to a second row: about 290px of a
     900px screen before the queue started, and the queue is what the page is for. Six fixed columns
     instead, at a figure one step down (text-lg holds £9,999,999.99 in the ~150px a tile gets at
     1280), and the label in sentence case, because "AWAITING CLASSIFICATION" in capitals is wider
     than that tile. Below `lg` it is three by two rather than a ragged wrap. -->
<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
	{#each kpis as kpi (kpi.key)}
		<Card glass class="min-w-0 gap-0 border-t-4 {kpi.accent} px-3 py-2">
			<!-- Truncates on the narrowest tile ("Relief in transit" at 1024), with the whole
			     label on hover and focus. -->
			<TruncatedText
				text={kpi.label}
				class="text-muted-foreground text-[11px] leading-4 font-medium"
			/>
			<!-- `Money`, not a formatted string: pounds at full weight with the pence smaller and
			     muted, so a strip of six figures reads as six POUNDS values and the exact amount is
			     still there. It is the same component the queue's Amount column uses, which is the
			     point — one money face across the surface. -->
			<div class="text-lg leading-tight font-semibold tracking-tight whitespace-nowrap">
				<Money amount={kpi.amount} />
			</div>
			<div class="flex min-w-0 items-baseline justify-between gap-2 text-[11px] leading-4">
				<span class="text-muted-foreground shrink-0 tabular-nums">{kpi.count}</span>
				<!-- Who has it. Half these stages are waiting on somebody else, and an adviser looking
				     at "Relief claimed" needs to know those are not theirs to chase. It shares the
				     count's line where a tile has room for both (2xl), and is read out below that. -->
				<span class="text-muted-foreground/70 hidden truncate text-[10px] font-medium 2xl:block">
					{kpi.actor}
				</span>
				<span class="sr-only 2xl:hidden">{kpi.actor}</span>
			</div>
		</Card>
	{/each}
</div>
