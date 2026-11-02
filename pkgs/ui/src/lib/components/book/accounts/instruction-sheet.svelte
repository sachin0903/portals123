<script lang="ts">
	// Payment, Withdrawal, Ad hoc fee, and the regular-schedule counterparts, in a side drawer
	// from the account record.
	//
	// The POST/PUT is injected: each portal has its own same-origin proxy. Absent a successful
	// submit the drawer stays open with the refusal, because closing on a 409 would hide the
	// reason the amount was too large.
	//
	// THE NOTE IS GONE. It was a free-text box on a money movement nobody downstream reads.
	// Payment states the collection account and the reference instead; Withdrawal states value,
	// cash and the destination bank; Ad hoc fee is the same money without a bank.
	//
	// A SIPP payment asks contribution type (personal or employer) and sends that category
	// with the instruction. Personal shows the relief-at-source figure below Expected On.
	// Relief in advance stays on the contributions workbench — it classifies money that has landed.
	//
	// An optional adviser fee on a payment is its own section at the foot of the form, not a
	// second instruction: the typed amount is what the client pays, the fee is taken off, and
	// Net Payment is what lands. Seccl is instructed as two members of one group so they share
	// an order number.
	//
	// A pension withdrawal is still this cash form. An ML-linked SIPP starts its DCube session
	// from the account record, so this drawer does not replace that path with a dead-end alert.
	//
	// FORM DRAWER (`sm:max-w-xl`). The primitive is the illustration width (`sm:w-[58vw]`); this
	// cap is the previous wide, now the money-movement size. Transfer uses the same.
	import {
		CREATABLE_CONTRIBUTION_CATEGORIES,
		type ContributionCategory
	} from '../contributions/types.js';
	import GrossUp from '../contributions/gross-up.svelte';
	import { Alert, AlertDescription, AlertTitle } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { CopyButton } from '../../ui/copy-button/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import { Label } from '../../ui/label/index.js';
	import { Money } from '../../ui/money/index.js';
	import MoneyInput from '../../ui/money-input/money-input.svelte';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import * as Sheet from '../../ui/sheet/index.js';
	import { Switch } from '../../ui/switch/index.js';
	import {
		bankDirectoryError,
		bankDisplayRows,
		bankSchemeOf,
		validateBank,
		type BankInput
	} from '../clients/bank.js';
	import type { BankCheckResult, BankCheckStatus } from '../../ui/bank-account-check/index.js';
	import BankMark from '../../../forms/field-inputs/bank-mark.svelte';
	import BankAccountFields from '../clients/bank-fields.svelte';
	import type { InstructionRow } from '../clients/types.js';
	import type { FeePackageOption, WithdrawalFees } from './types.js';
	import { accountBankSaveBody } from './bank.js';
	import { SCHEDULE_FREQUENCIES } from './regular.js';
	import {
		accountSheetTitle,
		feeTypeItems,
		isAccountSheetKind,
		isCashInstruction,
		isPayInSheet,
		isRegularSchedule,
		isWithdrawSheet,
		payInReference,
		payInSortCode,
		remainingCash,
		spendsCash,
		netPayment,
		withdrawalBankCaption,
		withdrawalCash,
		withdrawalDestination,
		withdrawalRecipientReceives,
		clientBorneRailFee,
		railFeeForMethod,
		ukWithdrawalMethod,
		WITHDRAWAL_METHOD_LABEL,
		withdrawalTotalDebit,
		type WithdrawalPaymentMethod,
		type AccountSheetKind,
		type SheetSubmitBody
	} from './instruction.js';

	const COPYABLE = new Set(['Sort code', 'Account number', 'IBAN', 'SWIFT/BIC']);

	let {
		kind = $bindable(null),
		accountId,
		accountUid,
		platformIdentifier = null,
		custodianAccountNumber = null,
		payInAccountName = null,
		payInAccountSortCode = null,
		payInAccountNumber = null,
		currency = 'GBP',
		accountValue = 0,
		cash = 0,
		bankAccounts = null,
		clientBank = null,
		instructions = [],
		withdrawalFees,
		custodian = null,
		feePackages = [],
		isPension = false,
		justificationRequired = false,
		bankReviewRequired = false,
		onSaveBank,
		submit
	}: {
		kind: AccountSheetKind | null;
		accountId: number;
		accountUid: string;
		platformIdentifier?: string | null;
		/** The custodian's own account number (SEI's, for a SEI account) — the pay-in reference the
		 *  client quotes so the credit reconciles. Null until the custodian has opened the account. */
		custodianAccountNumber?: string | null;
		/** The custodian's collection bank account — where clients pay money in — from the firm's
		 *  platform settings (`GET /crm/accounts/{uid}`'s `pay_in_*`). Blank until configured. */
		payInAccountName?: string | null;
		payInAccountSortCode?: string | null;
		payInAccountNumber?: string | null;
		currency?: string | null;
		accountValue?: number | null;
		cash: number;
		bankAccounts?: unknown;
		clientBank?: unknown;
		instructions?: InstructionRow[];
		/** SEI's own rail fees (`GET /crm/accounts/{uid}`'s `withdrawal_fees`). Undefined is an
		 *  older API/mount and falls back; null explicitly means this is not an SEI account. */
		withdrawalFees?: WithdrawalFees | null;
		/** Who holds the assets (`platform.accounts.custodian`). SEI requires an ad-hoc fee to name
		 *  a fee TYPE, so the Ad hoc fee form shows a required type picker on a SEI account; Seccl's
		 *  ad-hoc charge is amount-only, so it does not. */
		custodian?: string | null;
		/** The firm's fee packages (the same list the account's ongoing-fee editor uses). The Ad hoc
		 *  fee picker offers the `initial` (one-off) ones. */
		feePackages?: FeePackageOption[];
		/** A pension (the shelf's `is_pension`, wrapper-set fallback). Contribution type and the
		 *  relief-at-source figure belong on a SIPP payment, not on a GIA or ISA. */
		isPension?: boolean;
		/** The firm asks for a reason before a cash instruction is raised. */
		justificationRequired?: boolean;
		/** Four-eye is on for the account's own bank details — the in-drawer bank editor asks why. */
		bankReviewRequired?: boolean;
		/** Saves the account's own bank details from inside the Withdraw drawer — the same
		 *  endpoint as the overview pencil (`PUT …/bank-accounts`, four-eye included). Absent ⇒
		 *  the destination is read-only here. */
		onSaveBank?: (
			payload: Record<string, unknown>
		) => Promise<string | null | void> | string | null | void;
		submit: (body: SheetSubmitBody) => Promise<string | null>;
	} = $props();

	let amount = $state<number | null>(null);
	let due = $state('');
	let addAdviserFee = $state(false);
	let adviserFee = $state<number | null>(null);
	/** An adviser fee is entered as a cash amount or as a percentage of the amount. The backend
	 *  only ever sees the resolved cash figure. */
	let feeBasis = $state<'amount' | 'percent'>('amount');
	let adviserPct = $state<number | null>(null);
	/** Sell assets to raise the cash for a withdrawal (SEI raise-cash / Seccl Proportional). On by
	 *  default — every withdrawal disinvested until this switch existed. Off needs settled cash. */
	let autoDisinvest = $state(true);
	/** Invest a pay-in into the account's model once the cash lands — the inverse of
	 *  `autoDisinvest`. On by default; off leaves the money as cash. */
	let autoInvest = $state(true);
	const FEE_BASIS_OPTIONS: { value: 'amount' | 'percent'; label: string }[] = [
		{ value: 'amount', label: 'Amount' },
		{ value: 'percent', label: 'Percent' }
	];
	/** The chosen ad-hoc fee TYPE (a fee-package id as a string, for the picker). Required on a SEI
	 *  account, where the one-time charge must name a fee type; unused for Seccl's amount-only fee. */
	let feePackageId = $state('');
	let category = $state<ContributionCategory>('personal_contribution');
	let frequency = $state('monthly');
	let saving = $state(false);
	let error = $state<string | null>(null);
	let justification = $state('');
	/** The UK rail for this withdrawal. Starts as the method saved on the bank account. */
	let ukMethod = $state<'bacs' | 'faster_payment'>('bacs');
	/** True passes the rail fee on to the client. False means the firm pays it. */
	let passRailFee = $state(true);
	/** The in-drawer bank editor: the same fields as the overview pencil, saved through the
	 *  same door, so a withdrawal can be paid somewhere new without leaving the form. */
	let editingBank = $state(false);
	let bankSaving = $state(false);
	let bankError = $state<string | null>(null);
	let bankErrors = $state<Partial<Record<keyof BankInput, string>>>({});
	let bankDirectoryStatus = $state<BankCheckStatus>('idle');
	let bankDirectoryResult = $state<BankCheckResult | null>(null);
	let bankJustification = $state('');
	let bankSchemeDraft = $state('bacs');
	let bankNameDraft = $state('');
	let bankSortDraft = $state('');
	let bankNumberDraft = $state('');
	let bankIbanDraft = $state('');
	let bankBicDraft = $state('');
	let bankMethodDraft = $state('bacs');

	const open = $derived(isAccountSheetKind(kind));
	const title = $derived(
		isAccountSheetKind(kind) ? accountSheetTitle(kind, isPension) : accountSheetTitle('expected_in')
	);
	const payIn = $derived(isPayInSheet(kind));
	const withdraw = $derived(isWithdrawSheet(kind));
	const schedule = $derived(isRegularSchedule(kind));
	const cashOut = $derived(spendsCash(kind));
	const showContributionType = $derived(payIn && isPension);
	const showRelief = $derived(showContributionType && category === 'personal_contribution');
	const reference = $derived(
		payInReference(custodianAccountNumber, platformIdentifier, accountUid)
	);
	const sortCode = $derived(payInSortCode(payInAccountSortCode));
	const position = $derived(withdrawalCash(accountId, cash, instructions));
	const destination = $derived(
		withdrawalDestination({
			bank_accounts: bankAccounts,
			client: { bank_account: clientBank }
		})
	);
	const destinationRows = $derived(
		(destination.bank ? bankDisplayRows(destination.bank) : []).filter((row) => {
			const label = row[0];
			return label !== 'Payment method';
		})
	);
	const international = $derived(
		Boolean(destination.bank) && bankSchemeOf(destination.bank) === 'iban'
	);
	const chosenMethod = $derived<WithdrawalPaymentMethod>(
		kind !== 'withdrawal' ? 'bacs' : international ? 'swift' : ukMethod
	);
	// The custodian's own rail fee for the rail this withdrawal will actually use. Ad hoc fee
	// has no bank rail, so it never carries one. `fee` is what is taken from the client.
	const tariff = $derived(
		kind === 'withdrawal' ? railFeeForMethod(chosenMethod, withdrawalFees) : 0
	);
	const fee = $derived(clientBorneRailFee(tariff, passRailFee));
	const feeMethodLabel = $derived(WITHDRAWAL_METHOD_LABEL[chosenMethod]);
	// The adviser fee is the cash typed, or that percentage of the amount, resolved to pennies.
	const resolvedAdviserFee = $derived(
		!addAdviserFee
			? null
			: feeBasis === 'percent'
				? adviserPct != null && amount != null
					? Math.round(amount * adviserPct) / 100
					: null
				: adviserFee
	);
	const chargedFee = $derived(resolvedAdviserFee);
	// On a withdrawal the adviser fee is an extra debit on top of the money out and its rail fee.
	const withdrawalAdviserFee = $derived(withdraw && chargedFee ? chargedFee : 0);
	const totalDebit = $derived(
		Math.round((withdrawalTotalDebit(amount, fee) + withdrawalAdviserFee) * 100) / 100
	);
	const recipientReceives = $derived(withdrawalRecipientReceives(amount));
	const remaining = $derived(remainingCash(position.available, amount, fee + withdrawalAdviserFee));
	// An empty amount spends nothing. The fee on its own must not paint the form as already
	// overdrawn before they have typed the withdrawal.
	const overAvailable = $derived(cashOut && amount != null && amount > 0 && remaining < 0);
	const net = $derived(netPayment(amount, chargedFee));
	const rasAmount = $derived(addAdviserFee ? net : (amount ?? 0));
	const feeMissing = $derived(
		addAdviserFee && (resolvedAdviserFee == null || resolvedAdviserFee <= 0)
	);
	// A payment's fee is taken off it, so it must be smaller than the payment. A withdrawal's fee
	// is an extra debit bounded only by available cash (the funding gate), not capped here.
	const feeTooLarge = $derived(
		payIn && resolvedAdviserFee != null && amount != null && resolvedAdviserFee >= amount
	);
	// SEI charges an ad-hoc fee against a fee TYPE from the firm's register, so the Ad hoc fee form
	// on a SEI account offers the firm's one-off (`initial`) fee types and requires one. Seccl's
	// ad-hoc charge is amount-only, so no picker there. The adviser types the amount either way.
	const oneTimeFeeTypes = $derived(feePackages.filter((item) => item.frequency === 'initial'));
	const showFeeType = $derived(kind === 'adhoc_fee' && custodian === 'sei');
	const feeTypeOptions = $derived(feeTypeItems(oneTimeFeeTypes));
	const feeTypeMissing = $derived(showFeeType && !feePackageId);
	// SEI charges an adviser fee against a fee TYPE too, so a payment/withdrawal fee on a SEI account
	// names one (the same list the ad-hoc picker uses). Seccl's fee leg is amount-only.
	const showAdviserFeeType = $derived((payIn || kind === 'withdrawal') && custodian === 'sei');
	const adviserFeeTypeMissing = $derived(addAdviserFee && showAdviserFeeType && !feePackageId);
	// Only offer an adviser fee when there is something to charge it against: on a SEI account that
	// means a configured one-off fee type (the same for a payment). Seccl's fee is amount-only, so
	// it is always available. No fee types ⇒ the whole section is hidden rather than offering a
	// picker with nothing in it.
	const adviserFeeAvailable = $derived(
		!schedule &&
			(payIn || kind === 'withdrawal') &&
			// A SIPP pay-in is booked as a contribution (which has no per-payment adviser-fee leg —
			// advice is charged on the ongoing fee), so the fee section is withdrawal/non-pension only.
			!showContributionType &&
			(custodian !== 'sei' || feeTypeOptions.length > 0)
	);
	/** A cash instruction asks why when that action's reason switch is on. A regular schedule
	 *  never does: the schedule is not a four-eye door. */
	const needsJustification = $derived(justificationRequired && isCashInstruction(kind));
	const ready = $derived(
		open &&
			amount != null &&
			amount > 0 &&
			!saving &&
			!editingBank &&
			!overAvailable &&
			!feeMissing &&
			!feeTooLarge &&
			!feeTypeMissing &&
			!adviserFeeTypeMissing &&
			(!needsJustification || Boolean(justification.trim()))
	);

	function beginBankEdit() {
		// Seeded from whichever details the withdrawal would use today — the overview pencil does
		// the same — so "pay this one to a different account" starts from the current one.
		const bank = destination.bank;
		bankSchemeDraft = bankSchemeOf(bank);
		bankNameDraft = bank?.name ?? '';
		bankSortDraft = bank?.sort_code ?? '';
		bankNumberDraft = bank?.account_number ?? '';
		bankIbanDraft = bank?.iban ?? '';
		bankBicDraft = bank?.bic ?? '';
		bankMethodDraft = bank?.method === 'faster_payment' ? 'faster_payment' : 'bacs';
		bankErrors = {};
		bankDirectoryStatus = 'idle';
		bankDirectoryResult = null;
		bankError = null;
		bankJustification = '';
		editingBank = true;
	}

	async function saveBank() {
		if (!onSaveBank) return;
		const checked = validateBank({
			scheme: bankSchemeDraft === 'iban' ? 'iban' : 'bacs',
			name: bankNameDraft,
			sort_code: bankSortDraft,
			account_number: bankNumberDraft,
			iban: bankIbanDraft,
			bic: bankBicDraft,
			method: bankMethodDraft === 'faster_payment' ? 'faster_payment' : 'bacs'
		});
		if (!checked.ok) {
			bankErrors = { [checked.field]: checked.error };
			return;
		}
		const directory = bankDirectoryError(
			bankSchemeDraft === 'iban' ? 'iban' : 'bacs',
			bankDirectoryStatus,
			bankDirectoryResult?.state,
			bankDirectoryResult?.message
		);
		if (directory) {
			bankErrors = { [directory.field]: directory.error };
			return;
		}
		if (bankReviewRequired && !bankJustification.trim()) {
			bankError = 'Say why this change is needed.';
			return;
		}
		bankErrors = {};
		bankError = null;
		bankSaving = true;
		try {
			const body = accountBankSaveBody(checked.value, bankAccounts);
			const err = await onSaveBank(
				bankReviewRequired && bankJustification.trim()
					? { ...body, justification: bankJustification.trim() }
					: body
			);
			if (typeof err === 'string' && err) {
				bankError = err;
				return;
			}
			editingBank = false;
		} catch {
			bankError = 'Could not save the bank details.';
		} finally {
			bankSaving = false;
		}
	}

	function reset() {
		amount = null;
		due = '';
		addAdviserFee = false;
		adviserFee = null;
		adviserPct = null;
		feeBasis = 'amount';
		autoDisinvest = true;
		autoInvest = true;
		feePackageId = '';
		category = 'personal_contribution';
		frequency = 'monthly';
		error = null;
		saving = false;
		justification = '';
		ukMethod = ukWithdrawalMethod(destination.bank);
		passRailFee = true;
		editingBank = false;
		bankError = null;
		bankErrors = {};
	}

	async function save() {
		if (!isAccountSheetKind(kind)) return;
		if (amount == null || amount <= 0) {
			error = 'Enter an amount greater than zero.';
			return;
		}
		if (overAvailable) {
			error =
				fee > 0
					? 'The amount plus the payment method fee is more than the available cash.'
					: 'The amount is more than the available cash.';
			return;
		}
		if (feeMissing) {
			error = 'Enter the adviser fee, or switch the section off.';
			return;
		}
		if (feeTooLarge) {
			error = 'The adviser fee must be less than the payment.';
			return;
		}
		if (adviserFeeTypeMissing) {
			error = 'Choose the fee type the adviser fee is charged under.';
			return;
		}
		if (needsJustification && !justification.trim()) {
			error = 'Say why this instruction is needed.';
			return;
		}
		saving = true;
		error = null;
		const next: AccountSheetKind = kind;
		const dueDate = due || null;
		const refusal = await submit(
			isCashInstruction(next)
				? {
						kind: next,
						account_id: accountId,
						amount,
						model_id: null,
						fee_package_id: next === 'adhoc_fee' && feePackageId ? Number(feePackageId) : null,
						due_date: next === 'expected_in' ? dueDate : null,
						...(next === 'expected_in' ? { auto_invest: autoInvest } : {}),
						...(showContributionType && next === 'expected_in' ? { category } : {}),
						...(next === 'expected_in' && chargedFee
							? {
									adviser_fee: chargedFee,
									...(custodian === 'sei' && feePackageId
										? { adviser_fee_package_id: Number(feePackageId) }
										: {})
								}
							: {}),
						...(next === 'withdrawal'
							? {
									payment_method: chosenMethod,
									pass_rail_fee: passRailFee,
									auto_disinvest: autoDisinvest,
									...(chargedFee
										? {
												adviser_fee: chargedFee,
												...(custodian === 'sei' && feePackageId
													? { adviser_fee_package_id: Number(feePackageId) }
													: {})
											}
										: {})
								}
							: {}),
						...(justificationRequired && justification.trim()
							? { justification: justification.trim() }
							: {})
					}
				: {
						kind: next,
						amount,
						frequency,
						next_date: dueDate
					}
		);
		saving = false;
		if (refusal) {
			error = refusal;
			return;
		}
		kind = null;
		reset();
	}
</script>

<Sheet.Root
	{open}
	onOpenChange={(next) => {
		if (!next) {
			kind = null;
			reset();
		} else {
			reset();
		}
	}}
>
	<Sheet.Content side="right" class="gap-0 p-0 sm:max-w-xl">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title>{title}</Sheet.Title>
			<Sheet.Description>
				{#if withdraw}
					{#if schedule}
						A recurring withdrawal from this account’s available cash.
					{:else}
						Request cash from this account’s available balance.
					{/if}
				{:else if kind === 'adhoc_fee'}
					Charge a one-off fee from this account’s available cash.
				{:else if schedule}
					A recurring payment into this account. The client pays into the bank account below,
					quoting the reference so the credit can be matched.
				{:else}
					The client needs to pay this amount into the bank account below, quoting the reference so
					the credit can be matched to this account.
				{/if}
			</Sheet.Description>
		</Sheet.Header>

		<div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">
			<!-- One line per field: CopyButton wrapping the value used to wrap the row
			     (`whitespace-normal`). The value sits on the line, the icon copies, truncate never wraps. -->
			{#snippet bankRow(
				label: string,
				value: string | null,
				copyLabel: string | null,
				alignEnd: boolean = false
			)}
				<div class="flex min-w-0 items-center gap-3">
					<dt class="w-32 shrink-0 text-xs whitespace-nowrap opacity-80">{label}</dt>
					<dd class="flex min-w-0 flex-1 items-center gap-1 {alignEnd ? 'justify-end' : ''}">
						{#if label === 'Bank' && value}
							<BankMark name={value} bic={null} />
						{/if}
						<span
							class="truncate text-xs select-all {alignEnd
								? 'max-w-full'
								: 'min-w-0 flex-1'} {copyLabel || COPYABLE.has(label)
								? 'font-mono tabular-nums'
								: ''} {value ? '' : 'opacity-60'}">{value ?? '—'}</span
						>
						{#if value && copyLabel}
							<CopyButton {value} label={copyLabel} class="-mr-1.5" />
						{/if}
					</dd>
				</div>
			{/snippet}

			{#if withdraw}
				<div class="space-y-3 border-b pb-4">
					{#if editingBank}
						<!-- The same fields and the same door as the overview pencil, inside the drawer:
						     paying this withdrawal somewhere new must not mean closing the form first. -->
						<div class="border-border space-y-3 rounded-md border p-3">
							<p class="text-sm font-medium">Pay to</p>
							<BankAccountFields
								bind:scheme={bankSchemeDraft}
								bind:name={bankNameDraft}
								bind:sortCode={bankSortDraft}
								bind:accountNumber={bankNumberDraft}
								bind:iban={bankIbanDraft}
								bind:bic={bankBicDraft}
								bind:method={bankMethodDraft}
								bind:directoryStatus={bankDirectoryStatus}
								bind:directoryResult={bankDirectoryResult}
								errors={bankErrors}
								idPrefix="account-instruction-bank-"
								onSchemeChange={() => (bankErrors = {})}
							>
								{#snippet footer()}
									<p class="text-muted-foreground col-span-full text-xs">
										Saved to this account, so later withdrawals go here too.
									</p>
								{/snippet}
							</BankAccountFields>
							{#if bankReviewRequired}
								<div class="space-y-1.5">
									<Label for="account-instruction-bank-justification">Why this change</Label>
									<Textarea
										id="account-instruction-bank-justification"
										bind:value={bankJustification}
										rows={2}
										disabled={bankSaving}
									/>
									<p class="text-muted-foreground text-xs">
										A second person must approve before the bank details change.
									</p>
								</div>
							{/if}
							{#if bankError}<p class="text-destructive text-sm">{bankError}</p>{/if}
							<div class="flex justify-end gap-2">
								<Button
									size="sm"
									variant="ghost"
									disabled={bankSaving}
									onclick={() => (editingBank = false)}>Cancel</Button
								>
								<Button size="sm" disabled={bankSaving} onclick={saveBank}>
									{bankSaving ? 'Saving…' : 'Save bank details'}
								</Button>
							</div>
						</div>
					{:else}
						<Alert variant="info">
							{#if destination.bank}
								<AlertDescription class="w-full italic text-pretty">
									Withdrawals will be transferred to the following bank account.
								</AlertDescription>
								<dl class="min-w-0 space-y-1">
									{#each destinationRows as row (row[0])}
										{@render bankRow(row[0], row[1], null, true)}
									{/each}
								</dl>
							{:else}
								<p class="text-sm italic opacity-80">{withdrawalBankCaption(null)}</p>
							{/if}
							{#if onSaveBank && !destination.bank}
								<Button size="sm" variant="outline" class="mt-2" onclick={beginBankEdit}>
									Add bank details
								</Button>
							{/if}
						</Alert>
					{/if}
				</div>
			{/if}

			{#if payIn}
				<Alert variant="info">
					<AlertTitle>Custodian bank details</AlertTitle>
					<dl class="min-w-0 space-y-1">
						{@render bankRow('Account name', payInAccountName, null, true)}
						{@render bankRow('Sort code', sortCode, 'Copy sort code', true)}
						{@render bankRow('Account number', payInAccountNumber, 'Copy account number', true)}
						{@render bankRow('Reference', reference, 'Copy payment reference', true)}
					</dl>
				</Alert>
			{/if}

			{#if cashOut}
				<dl class="space-y-1.5 text-sm">
					<div class="flex items-baseline justify-between gap-3 text-sm">
						<dt class="text-muted-foreground">Value</dt>
						<dd><Money amount={accountValue ?? 0} /></dd>
					</div>
					<div class="flex items-baseline justify-between gap-3 text-sm">
						<dt class="text-muted-foreground">Available cash</dt>
						<dd><Money amount={position.available} /></dd>
					</div>
				</dl>
			{/if}

			{#if showFeeType}
				<div class="space-y-1.5">
					<Label for="account-instruction-fee-type">Fee type</Label>
					{#if feeTypeOptions.length}
						<SelectMenu
							id="account-instruction-fee-type"
							items={feeTypeOptions}
							bind:value={feePackageId}
							placeholder="Choose the fee type"
						/>
						<p class="text-muted-foreground text-xs">
							SEI charges an ad hoc fee against one of the firm's fee types. The amount is what you
							enter below.
						</p>
					{:else}
						<p class="text-destructive text-xs">
							This firm has no one-off fee types set up for SEI. Add one in the firm's fee settings
							before charging an ad hoc fee.
						</p>
					{/if}
				</div>
			{/if}

			<div class="space-y-1.5">
				<Label for="account-instruction-amount">{kind === 'adhoc_fee' ? 'Fee' : 'Amount'}</Label>
				<MoneyInput
					id="account-instruction-amount"
					currency={currency ?? 'GBP'}
					bind:value={amount}
				/>
			</div>

			{#if schedule}
				<div class="space-y-1.5">
					<Label for="account-instruction-frequency">Frequency</Label>
					<SelectMenu
						id="account-instruction-frequency"
						items={[...SCHEDULE_FREQUENCIES]}
						bind:value={frequency}
					/>
				</div>
			{/if}

			<!-- A regular schedule needs its next run date. A one-off payment in does not ask one:
			     the "Expected On" date was optional, and leaving it blank tells SEI to expect the
			     money open-ended and reconcile the credit when it lands. -->
			{#if schedule}
				<div class="space-y-1.5">
					<Label for="account-instruction-due">Next date</Label>
					<Input id="account-instruction-due" type="date" bind:value={due} />
				</div>
			{/if}

			{#if showContributionType}
				<div class="space-y-3">
					<div class="space-y-1.5">
						<Label>Contribution type</Label>
						<Segmented bind:value={category} options={CREATABLE_CONTRIBUTION_CATEGORIES} />
					</div>
					{#if showRelief}
						<div>
							<p class="mb-2 text-xs font-medium">Relief at source</p>
							<GrossUp amount={rasAmount} {category} />
						</div>
					{/if}
				</div>
			{/if}

			{#if kind === 'expected_in' && !showContributionType}
				<!-- Invest the cash into the account's model once it lands (the inverse of a
				     withdrawal's auto-disinvest). On by default; off leaves it uninvested. Not on a
				     SIPP pay-in: that is a contribution, and the SIPP's cash is invested per its model. -->
				<div class="flex items-start gap-3">
					<Switch id="account-instruction-auto-invest" class="mt-0.5" bind:checked={autoInvest} />
					<div class="space-y-0.5">
						<Label for="account-instruction-auto-invest" class="m-0 font-medium">Auto invest</Label>
						<p class="text-muted-foreground text-xs">
							Invest this payment into the account's model once it lands. Off leaves it as cash.
						</p>
					</div>
				</div>
			{/if}

			{#if kind === 'withdrawal'}
				<!-- The rail is the one saved on the account's bank details; there is no picker. Whether
				     to sell assets to fund the money out is the adviser's call, hence this switch. -->
				<div class="flex items-start gap-3">
					<Switch
						id="account-instruction-auto-disinvest"
						class="mt-0.5"
						bind:checked={autoDisinvest}
					/>
					<div class="space-y-0.5">
						<Label for="account-instruction-auto-disinvest" class="m-0 font-medium">
							Auto disinvest
						</Label>
						<p class="text-muted-foreground text-xs">
							Sell assets to raise the cash for this withdrawal. Off needs enough settled cash
							already.
						</p>
					</div>
				</div>
			{/if}

			{#if kind === 'withdrawal' && (tariff > 0 || withdrawalAdviserFee > 0)}
				{#if tariff > 0}
					<!-- Stated up front, not discovered after the fact: SEI charges the RAIL, so £1,000 by
						     Faster Payment debits £1,000.50 unless the firm pays the 50p itself. -->
					<dl class="space-y-1.5 text-xs">
						<div class="flex items-baseline justify-between gap-3">
							<dt class="text-muted-foreground text-xs">{feeMethodLabel} fee</dt>
							<dd><Money amount={tariff} /></dd>
						</div>
					</dl>
					<div class="flex items-start gap-3">
						<Switch id="account-instruction-pass-fee" class="mt-0.5" bind:checked={passRailFee} />
						<Label for="account-instruction-pass-fee" class="m-0 font-medium">
							Pass this fee on to the client
						</Label>
					</div>
					{#if !passRailFee}
						<p class="text-muted-foreground text-xs">
							The firm pays this fee. It is not taken from the client’s cash.
						</p>
					{/if}
				{/if}
				<!-- Both sides, so nobody has to subtract: the recipient receives the amount asked for;
				     the rail fee and the adviser fee sit on top of it, debited from the account. -->
				<dl class="space-y-1 text-xs font-medium">
					<div class="flex items-baseline justify-between gap-3">
						<dt>Total to be debited</dt>
						<dd><Money amount={totalDebit} /></dd>
					</div>
					<div class="flex items-baseline justify-between gap-3">
						<dt>Recipient receives</dt>
						<dd><Money amount={recipientReceives} /></dd>
					</div>
				</dl>
			{/if}

			{#if cashOut}
				<div class="flex items-baseline justify-between gap-3 text-xs">
					<p class="text-muted-foreground text-xs">Remaining cash</p>
					<Money amount={remaining} class={overAvailable ? 'text-destructive' : undefined} />
				</div>
				{#if overAvailable}
					<p class="text-destructive text-xs">
						{fee > 0
							? 'The amount plus the payment method fee is more than the available cash.'
							: 'The amount is more than the available cash.'}
					</p>
				{/if}
			{/if}

			{#if adviserFeeAvailable}
				<div class="border-border space-y-3 border-t pt-4">
					<p class="text-sm font-medium">Adviser fee</p>
					<div class="flex items-center gap-3">
						<Switch id="account-instruction-add-fee" bind:checked={addAdviserFee} />
						<Label for="account-instruction-add-fee" class="m-0 font-medium"
							>Include Adviser Fee</Label
						>
					</div>
					{#if addAdviserFee}
						{#if showAdviserFeeType}
							<div class="space-y-1.5">
								<Label for="account-instruction-adviser-fee-type">Fee type</Label>
								{#if feeTypeOptions.length}
									<SelectMenu
										id="account-instruction-adviser-fee-type"
										items={feeTypeOptions}
										bind:value={feePackageId}
										placeholder="Choose the fee type"
									/>
									<p class="text-muted-foreground text-xs">
										SEI charges the fee against one of the firm's fee types. The amount is what you
										set below.
									</p>
								{:else}
									<p class="text-destructive text-xs">
										This firm has no fee types set up for SEI. Add one in the firm's fee settings
										before charging an adviser fee.
									</p>
								{/if}
							</div>
						{/if}
						<div class="space-y-1.5">
							<Label>Fee basis</Label>
							<Segmented
								bind:value={feeBasis}
								options={FEE_BASIS_OPTIONS}
								label="Fee basis"
								shape="square"
							/>
						</div>
						{#if feeBasis === 'percent'}
							<div class="space-y-1.5">
								<Label for="account-instruction-adviser-pct">
									Percentage of {payIn ? 'payment' : 'withdrawal'}
								</Label>
								<Input
									id="account-instruction-adviser-pct"
									type="number"
									min="0"
									step="0.01"
									bind:value={adviserPct}
								/>
							</div>
						{:else}
							<div class="space-y-1.5">
								<Label for="account-instruction-adviser-fee">Fee</Label>
								<MoneyInput
									id="account-instruction-adviser-fee"
									currency={currency ?? 'GBP'}
									bind:value={adviserFee}
								/>
							</div>
						{/if}
						{#if feeBasis === 'percent' && chargedFee != null && chargedFee > 0}
							<div class="flex items-baseline justify-between gap-3 text-xs">
								<span class="text-muted-foreground">Adviser fee</span>
								<Money amount={chargedFee} />
							</div>
						{/if}
						{#if payIn}
							<div class="flex items-baseline justify-between gap-3 font-medium">
								<span>Net Payment</span>
								<Money amount={net} class={feeTooLarge ? 'text-destructive' : undefined} />
							</div>
							{#if feeTooLarge}
								<p class="text-destructive text-xs">
									The adviser fee must be less than the payment.
								</p>
							{/if}
						{:else}
							<p class="text-muted-foreground text-xs">
								An extra debit on top of the withdrawal, taken from the account’s cash. The total to
								be debited is shown above.
							</p>
						{/if}
					{/if}
				</div>
			{/if}

			{#if needsJustification}
				<div class="space-y-1.5">
					<Label for="account-instruction-justification">Why this instruction</Label>
					<Textarea id="account-instruction-justification" bind:value={justification} rows={2} />
					<p class="text-muted-foreground text-xs">
						{schedule
							? 'A second person must approve before this schedule changes.'
							: 'A second person must approve before this instruction is raised.'}
					</p>
				</div>
			{/if}

			{#if error}
				<p class="text-destructive text-sm">{error}</p>
			{/if}
		</div>

		<Sheet.Footer class="border-t">
			<Button onclick={save} disabled={!ready}>
				{saving ? 'Saving…' : title}
			</Button>
			<Button variant="ghost" onclick={() => (kind = null)}>Cancel</Button>
		</Sheet.Footer>
	</Sheet.Content>
</Sheet.Root>
