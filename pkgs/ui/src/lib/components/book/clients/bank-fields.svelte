<script lang="ts">
	import type { Snippet } from 'svelte';
	import BankAccountNumberInput from '../../../forms/field-inputs/bank-account-number-input.svelte';
	import SortCodeInput from '../../../forms/field-inputs/sort-code-input.svelte';
	import {
		BankAccountCheck,
		bankDigits,
		type BankCheckResult,
		type BankCheckStatus
	} from '../../ui/bank-account-check/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import { BANK_METHOD_OPTIONS, BANK_SCHEME_OPTIONS } from './bank.js';

	/** Posted under these names when the editor sits in a classic form action (the client
	 *  record). The account editor saves from the bound values and leaves them unset. */
	export type BankFieldNames = {
		scheme: string;
		name: string;
		sortCode: string;
		accountNumber: string;
		iban: string;
		bic: string;
		method: string;
	};

	let {
		scheme = $bindable('bacs'),
		name = $bindable(''),
		sortCode = $bindable(''),
		accountNumber = $bindable(''),
		iban = $bindable(''),
		bic = $bindable(''),
		method = $bindable('bacs'),
		directoryStatus = $bindable<BankCheckStatus>('idle'),
		directoryResult = $bindable<BankCheckResult | null>(null),
		errors,
		idPrefix,
		names = undefined,
		onSchemeChange,
		footer
	}: {
		scheme?: string;
		name?: string;
		sortCode?: string;
		accountNumber?: string;
		iban?: string;
		bic?: string;
		method?: string;
		/** The live sort-code check. A parent reads it to refuse a save the directory has not accepted. */
		directoryStatus?: BankCheckStatus;
		directoryResult?: BankCheckResult | null;
		errors: Partial<Record<string, string>>;
		idPrefix: string;
		names?: BankFieldNames;
		onSchemeChange?: () => void;
		footer?: Snippet;
	} = $props();

	// A 7-digit account number is the same account with a leading zero. The directory wants eight,
	// and the check stays hidden until it has eight, so the editor pads before it asks.
	const directoryAccount = $derived.by(() => {
		const digits = bankDigits(accountNumber);
		return digits.length === 7 ? digits.padStart(8, '0') : digits;
	});
	const sortUnknown = $derived(
		directoryStatus === 'rejected' && directoryResult?.state === 'sort_code_unknown'
	);

	// Switching to an IBAN must not keep a UK rejection that would block the international save.
	$effect(() => {
		if (scheme !== 'iban') return;
		if (directoryStatus !== 'idle') directoryStatus = 'idle';
		if (directoryResult !== null) directoryResult = null;
	});
</script>

<!-- Three equal columns for the rail fields (name · sort · number, or name · IBAN · BIC).
     Always thirds, not `sm:` — the client card often sits in a half-width column where the
     breakpoint never trips, and the account editor opens this same grid in a dialog because
     its card lives in a 22rem rail. `min-w-0` lets the inputs shrink inside the grid item;
     without it `min-width: auto` overflows an IBAN. Account type stays full-width above. -->
<div class="grid grid-cols-3 gap-3">
	{#if names}
		<input type="hidden" name={names.scheme} value={scheme} />
	{/if}
	<div class="col-span-full space-y-1">
		<Label>Account type</Label>
		<!-- Content-width, not row-width: Segmented is already `inline-flex`. `w-full` stretched
		     UK / International across the card. -->
		<Segmented
			bind:value={scheme}
			options={BANK_SCHEME_OPTIONS}
			label="Account type"
			shape="square"
			onValueChange={() => onSchemeChange?.()}
		/>
		<p class="text-muted-foreground text-xs">
			UK bank accounts include the following crown territories: Jersey, Guernsey, Isle of Man,
			Gibraltar.
		</p>
	</div>
	<div class="min-w-0 space-y-1">
		<Label for="{idPrefix}name" class="whitespace-nowrap">Account name</Label>
		<Input
			id="{idPrefix}name"
			name={names?.name}
			bind:value={name}
			autocomplete="off"
			aria-invalid={errors.name ? 'true' : undefined}
		/>
		{#if errors.name}
			<p class="text-destructive text-xs">{errors.name}</p>
		{/if}
	</div>
	{#if scheme === 'iban'}
		<div class="min-w-0 space-y-1">
			<Label for="{idPrefix}iban" class="whitespace-nowrap">IBAN</Label>
			<Input
				id="{idPrefix}iban"
				name={names?.iban}
				bind:value={iban}
				placeholder="GB82 WEST 1234 5698 7654 32"
				autocomplete="off"
				aria-invalid={errors.iban ? 'true' : undefined}
			/>
			{#if errors.iban}
				<p class="text-destructive text-xs">{errors.iban}</p>
			{/if}
		</div>
		<div class="min-w-0 space-y-1">
			<Label for="{idPrefix}bic" class="whitespace-nowrap">SWIFT/BIC</Label>
			<Input
				id="{idPrefix}bic"
				name={names?.bic}
				bind:value={bic}
				placeholder="BARCGB22"
				autocomplete="off"
				aria-invalid={errors.bic ? 'true' : undefined}
			/>
			{#if errors.bic}
				<p class="text-destructive text-xs">{errors.bic}</p>
			{/if}
		</div>
	{:else}
		<div class="min-w-0 space-y-1">
			<Label for="{idPrefix}sort" class="whitespace-nowrap">Sort code</Label>
			<SortCodeInput
				id="{idPrefix}sort"
				name={names?.sortCode}
				value={sortCode}
				invalid={Boolean(errors.sort_code)}
				verified={directoryStatus === 'confirmed'}
				onchange={(v) => (sortCode = v)}
			/>
			{#if errors.sort_code}
				<p class="text-destructive text-xs">{errors.sort_code}</p>
			{/if}
		</div>
		<div class="min-w-0 space-y-1">
			<Label for="{idPrefix}number" class="whitespace-nowrap">Account number</Label>
			<BankAccountNumberInput
				id="{idPrefix}number"
				name={names?.accountNumber}
				value={accountNumber}
				invalid={Boolean(errors.account_number)}
				verified={directoryStatus === 'confirmed'}
				mismatch={directoryStatus === 'rejected' && !sortUnknown}
				onchange={(v) => (accountNumber = v)}
			/>
			{#if errors.account_number}
				<p class="text-destructive text-xs">{errors.account_number}</p>
			{/if}
		</div>
		<div class="col-span-full">
			<BankAccountCheck
				{sortCode}
				accountNumber={directoryAccount}
				bind:status={directoryStatus}
				bind:result={directoryResult}
			/>
		</div>
		{#if method === 'faster_payment' && directoryStatus === 'confirmed' && directoryResult && !directoryResult.supports.faster_payments}
			<p class="text-muted-foreground col-span-full text-xs">
				This account doesn't accept Faster Payments.
			</p>
		{/if}
		{#if names}
			<input type="hidden" name={names.method} value={method} />
		{/if}
		<div class="col-span-full space-y-1">
			<Label>Payment method</Label>
			<Segmented
				bind:value={method}
				options={BANK_METHOD_OPTIONS}
				label="Payment method"
				shape="square"
			/>
			<p class="text-muted-foreground text-xs">
				BACS is free. This is the default for withdrawals. A withdrawal can use the other method.
			</p>
		</div>
	{/if}
	{@render footer?.()}
</div>
