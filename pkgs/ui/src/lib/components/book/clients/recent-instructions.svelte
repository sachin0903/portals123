<script lang="ts">
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down-to-line';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up-from-line';
	import LayersIcon from '@lucide/svelte/icons/layers';
	import ReceiptIcon from '@lucide/svelte/icons/receipt';
	import { Badge } from '../../ui/badge/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardAction, CardContent, CardHeader, CardTitle } from '../../ui/card/index.js';
	import { Money } from '../../ui/money/index.js';
	import type { InstructionKind, InstructionRow } from './types.js';
	import { humanise } from '../../../sentence-case.js';
	import { formatDate } from '../../../dates.js';

	let {
		instructions,
		accountHref,
		oncancel,
		currencyOf,
		limit = 4
	}: {
		instructions: InstructionRow[];
		accountHref: (accountUid: string) => string;
		oncancel?: (uid: string) => void;
		/** The account's currency. Absent callers keep the sterling default. */
		currencyOf?: (instruction: InstructionRow) => string;
		limit?: number;
	} = $props();

	const labels: Record<InstructionKind, string> = {
		expected_in: 'Expected in',
		withdrawal: 'Withdrawal',
		adhoc_fee: 'Ad hoc fee',
		model_topup: 'Model top-up',
		model_withdrawal: 'Model withdrawal'
	};
	const icons = {
		expected_in: ArrowDownIcon,
		withdrawal: ArrowUpIcon,
		adhoc_fee: ReceiptIcon,
		model_topup: LayersIcon,
		model_withdrawal: LayersIcon
	} as const;
	const recent = $derived(
		instructions
			.slice()
			.sort((a, b) => b.created_at.localeCompare(a.created_at))
			.slice(0, Math.max(1, limit))
	);
</script>

{#if recent.length}
	<Card glass class="gap-2">
		<CardHeader class="gap-3">
			<CardTitle class="text-base">Recent instructions</CardTitle>
			<CardAction>
				<span class="text-muted-foreground text-xs tabular-nums">
					{Math.min(instructions.length, Math.max(1, limit))} of {instructions.length}
				</span>
			</CardAction>
		</CardHeader>
		<CardContent>
			<ul class="divide-border divide-y text-xs">
				{#each recent as instruction (instruction.uid)}
					{@const Icon = icons[instruction.kind]}
					{@const incoming =
						instruction.kind === 'expected_in' || instruction.kind === 'model_topup'}
					<!-- An ad hoc fee carries WHICH fee it is (the firm's own fee package). It goes on the
					     heading rather than the sub-line because it is what the row IS, not context for it
					     — and a charge recorded under a type nothing reads back is a field that only looks
					     like a control. Every other kind has none and reads exactly as before. -->
					{@const heading =
						labels[instruction.kind] +
						(instruction.fee_package_name ? ` · ${instruction.fee_package_name}` : '')}
					{@const href = accountHref(instruction.account_uid)}
					<li class="flex min-w-0 items-center gap-2 py-2 first:pt-0 last:pb-0">
						<Icon class="text-muted-foreground size-3.5 shrink-0" />
						<a {href} class="min-w-0 flex-1 rounded-sm hover:underline focus-visible:outline-2">
							<span class="block truncate text-xs font-medium">{heading}</span>
							<span class="text-muted-foreground block truncate text-[11px]">
								{instruction.account_name ?? 'Account'} · {formatDate(instruction.created_at)}
							</span>
						</a>
						<div class="shrink-0 text-right">
							<Money
								amount={incoming ? instruction.amount : -instruction.amount}
								currency={currencyOf?.(instruction) ?? 'GBP'}
								signed
								tone="directional"
								class="text-xs font-medium"
							/>
							{#if instruction.status === 'pending' && oncancel}
								<Button
									type="button"
									variant="ghost"
									size="sm"
									class="text-muted-foreground h-5 px-1 text-[11px]"
									onclick={() => oncancel?.(instruction.uid)}
								>
									Cancel
								</Button>
							{:else}
								<Badge variant="outline" class="h-5 px-1.5 text-[10px] font-normal">
									{humanise(instruction.status)}
								</Badge>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
		</CardContent>
	</Card>
{/if}
