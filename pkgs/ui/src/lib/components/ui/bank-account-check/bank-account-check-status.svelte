<script lang="ts">
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import BankMark from '../../../forms/field-inputs/bank-mark.svelte';
	import { Badge } from '../badge/index.js';
	import {
		BANK_CHECKING_MESSAGE,
		BANK_CONFIRMED_NOTE,
		BANK_RAILS,
		BANK_UNAVAILABLE_MESSAGE,
		type BankCheckResult,
		type BankCheckStatus
	} from './types.js';

	let {
		status,
		result = null
	}: {
		status: BankCheckStatus;
		result?: BankCheckResult | null;
	} = $props();

	const rails = $derived(
		result ? BANK_RAILS.filter((rail) => result.supports[rail.key]).map((rail) => rail.label) : []
	);
	const heading = $derived(
		[result?.bank_name, result?.branch_name].filter((part) => part && part.trim()).join(', ')
	);
</script>

{#if status === 'checking'}
	<p class="text-muted-foreground flex items-center gap-2 text-sm">
		<LoaderCircleIcon class="size-3.5 animate-spin" />
		{BANK_CHECKING_MESSAGE}
	</p>
{:else if status === 'confirmed' && result}
	<div class="border-border bg-muted/40 space-y-2 rounded-md border px-3 py-2" role="status">
		{#if heading}
			<div class="flex items-center gap-2">
				{#if result.bank_name?.trim()}
					<BankMark name={result.bank_name} bic={result.bank_bic?.trim() || null} />
				{/if}
				<p class="text-sm font-medium">{heading}</p>
			</div>
		{/if}
		<p class="text-muted-foreground text-xs">{BANK_CONFIRMED_NOTE}</p>
		{#if rails.length}
			<div class="flex flex-wrap gap-1">
				{#each rails as rail (rail)}
					<Badge variant="outline">{rail}</Badge>
				{/each}
			</div>
		{/if}
	</div>
{:else if status === 'rejected'}
	<p class="text-destructive text-sm" role="alert">
		{result?.message || "This account number isn't valid for that sort code."}
	</p>
{:else if status === 'unavailable'}
	<p class="text-muted-foreground text-sm">{BANK_UNAVAILABLE_MESSAGE}</p>
{/if}
