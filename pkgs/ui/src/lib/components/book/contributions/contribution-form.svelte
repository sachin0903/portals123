<script lang="ts">
	// "Record a contribution", in a side drawer from an account page.
	//
	// Create only. Categorising an existing payment happens in the contributions workbench, where
	// the queue stays visible while you work — a drawer is the wrong shape for something you do
	// twenty times in a row. This form is the opposite case: one deliberate booking, opened from
	// the account it belongs to, with nothing behind it worth preserving.
	//
	// FORM DRAWER (`sm:max-w-xl`). The primitive is the illustration width (`sm:w-[58vw]`); this
	// cap is the previous wide, now the money-movement size — Payment / Withdraw / Ad hoc fee /
	// Transfer use the same. It used to force `!w-[46vw] !max-w-none` (half the screen for a form
	// whose longest answer is an address) and then, after that was dropped, sat on the illustration
	// floor with no cap. Mobile is full-width on the primitive.
	//
	// FOUR QUESTIONS, and the removals are the design. Gone: the contribution date (the money has
	// not arrived, so any date entered here is a guess — the matcher stamps it from the credit that
	// actually lands, see `app/matching.py::link`), "paid by" (it restated the category: a personal
	// contribution is paid by the client, an employer contribution by the employer) and the tax year
	// (derived from the contribution date, so entering one before the date is known could only
	// contradict it — `contributions.tax_year_of` does it server-side).
	//
	// The relief-at-source consequence is shown before confirming, because the category is what
	// determines it (see relief.ts) — and the gross figure is what actually lands in the pension,
	// which is not the figure being typed in.
	import CheckIcon from '@lucide/svelte/icons/check';
	import GrossUp from './gross-up.svelte';
	import {
		CREATABLE_CONTRIBUTION_CATEGORIES,
		PAYMENT_TYPES,
		REGULAR_FREQUENCIES,
		frequencyFor,
		NEEDS_EMPLOYER,
		type ContributionCategory,
		type ContributionFrequency,
		type LodgeReport,
		type PaidBy,
		type PaymentType
	} from './types';
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { AddressLookup } from '../../ui/address-lookup/index.js';
	import type { Address } from '../../ui/address-lookup/index.js';
	import EmailInput from '../../../forms/field-inputs/email-input.svelte';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import MoneyInput from '../../ui/money-input/money-input.svelte';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import * as Select from '../../ui/select/index.js';
	import { Separator } from '../../ui/separator/index.js';
	import * as Sheet from '../../ui/sheet/index.js';

	let {
		open = $bindable(false),
		account,
		client = null,
		endpoint,
		onbooked
	}: {
		open?: boolean;
		account: {
			id: number;
			/** The account's uid — the portal endpoints these forms post to are addressed by it
			 *  (`/accounts/[uid]/transfers`, `/accounts/[uid]/contributions`). */
			uid: string;
			name: string | null;
			currency?: string | null;
		};
		client?: string | null;
		/** Override for a host portal's same-origin proxy. */
		endpoint?: string;
		/** Fired after a successful booking so the page can refresh its data. */
		onbooked?: (result: { uid: string; sei: LodgeReport }) => void;
	} = $props();

	let category = $state<ContributionCategory | undefined>(undefined);
	let amount = $state<number | null>(null);
	/** Ad-hoc or regular. Presentation only — `frequencyFor` turns it into the frequency the
	 *  database and Morgan Lloyd's `cam_frequency` both take. */
	let paymentType = $state<PaymentType>('adhoc');
	let regular = $state<ContributionFrequency>('monthly');
	let dueInput = $state('');
	let rasAmount = $state<number | null>(null);
	let employerName = $state('');
	let employerLine1 = $state('');
	let employerCity = $state('');
	let employerPostcode = $state('');
	/** What the lookup returned. Writing it into the individual fields rather than replacing them
	 *  keeps the block correctable by hand — a lookup gets an address 95% right and the last line
	 *  is often a "care of" or a floor nobody's database holds. */
	let employerAddress = $state<Address | null>(null);
	$effect(() => {
		const found = employerAddress;
		if (!found) return;
		employerLine1 = [found.line1, found.line2].filter(Boolean).join(', ');
		employerCity = found.town ?? '';
		employerPostcode = found.postcode ?? '';
	});
	let employerEmail = $state('');

	let saving = $state(false);
	let error = $state<string | null>(null);
	let lodged = $state<LodgeReport | null>(null);

	const currency = $derived(account.currency || 'GBP');
	const value = $derived(amount ?? 0);
	const frequency = $derived(frequencyFor(paymentType, regular));
	/** Implied by the category rather than asked: the two are the same fact, and a form that lets
	 *  them disagree records a personal contribution paid by an employer. */
	const paidBy = $derived<PaidBy>(category === 'employer_contribution' ? 'employer' : 'client');
	const needsEmployer = $derived(!!category && NEEDS_EMPLOYER.has(category));
	const employerMissing = $derived(needsEmployer && !employerName.trim());
	const ready = $derived(!!category && value > 0 && !employerMissing && !saving);

	const regularLabel = (v: ContributionFrequency) =>
		REGULAR_FREQUENCIES.find((f) => f.value === v)?.label ?? v;

	function reset() {
		category = undefined;
		amount = null;
		rasAmount = null;
		paymentType = 'adhoc';
		regular = 'monthly';
		dueInput = '';
		employerName = '';
		employerLine1 = '';
		employerAddress = null;
		employerCity = '';
		employerPostcode = '';
		employerEmail = '';
		error = null;
		lodged = null;
	}

	async function book() {
		if (!category || !ready) return;
		saving = true;
		error = null;
		try {
			const res = await fetch(endpoint ?? `/accounts/${account.uid}/contributions`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				// No `contribution_date` and no `tax_year`: the API leaves the date null until the
				// credit is matched and derives the tax year, so sending today's date would write a
				// fact we do not have.
				body: JSON.stringify({
					category,
					amount: value,
					frequency,
					paid_by: paidBy,
					due_date: dueInput || null,
					prefunded_ras_amount: rasAmount,
					employer: needsEmployer
						? {
								employer_name: employerName.trim() || null,
								address_line1: employerLine1.trim() || null,
								city: employerCity.trim() || null,
								postcode: employerPostcode.trim() || null,
								email: employerEmail.trim() || null
							}
						: null
				})
			});
			const body = await res.json();
			if (!res.ok) {
				error = body.error ?? 'Could not record the contribution.';
				return;
			}
			// Booked. The custodian instruction is reported separately — it can fail without
			// losing the contribution, and the adviser needs to know which happened.
			lodged = body.sei as LodgeReport;
			onbooked?.({ uid: body.uid, sei: lodged });
			if (lodged?.status === 'instructed') {
				open = false;
				reset();
			}
		} catch {
			error = 'Could not reach the server.';
		} finally {
			saving = false;
		}
	}
</script>

<Sheet.Root
	bind:open
	onOpenChange={(next) => {
		if (!next) reset();
	}}
>
	<Sheet.Content side="right" class="gap-0 p-0 sm:max-w-xl">
		<Sheet.Header class="border-b px-4 pt-4 pb-3">
			<Sheet.Title>Record a contribution</Sheet.Title>
			<Sheet.Description>
				{client ? `${client} · ` : ''}{account.name ?? 'Account'}. Record the payment you expect to
				receive. The custodian will be notified.
			</Sheet.Description>
		</Sheet.Header>

		<!-- The scroller is this wrapper, not `Sheet.Content`: the address lookup renders its
		     suggestions as an absolutely positioned list, and an overflow ancestor clips it. Keeping
		     the employer block low in the form leaves that list room to open downwards. -->
		<div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
			{#if error}
				<Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>
			{/if}
			{#if lodged && lodged.status !== 'instructed'}
				<!-- Booked locally, but the custodian was not told. Said plainly, because an adviser
				     who assumes SEI knows will not chase the payment. -->
				<Alert>
					<AlertDescription>
						<strong>Recorded, but not lodged with the custodian.</strong>
						{lodged.detail}
						{#if lodged.missing?.length}
							<ul class="mt-1 list-disc pl-4 text-xs">
								{#each lodged.missing as item (item)}<li>{item}</li>{/each}
							</ul>
						{/if}
						<span class="mt-1 block text-xs">Retry it from the contributions workbench.</span>
					</AlertDescription>
				</Alert>
			{/if}

			<div class="space-y-1.5">
				<Label>Contribution type</Label>
				<!-- Two options, so a button group rather than a dropdown: the whole answer is visible
				     and it is one click. The wider classification vocabulary (transfer in, not a
				     contribution) belongs to the workbench, not to booking — see
				     CREATABLE_CONTRIBUTION_CATEGORIES. -->
				<Segmented bind:value={category} options={CREATABLE_CONTRIBUTION_CATEGORIES} />
			</div>

			<div class="space-y-1.5">
				<Label for="con-amount">Amount</Label>
				<MoneyInput id="con-amount" bind:value={amount} {currency} min={0} />
			</div>

			<div class="space-y-1.5">
				<Label>Type</Label>
				<Segmented bind:value={paymentType} options={PAYMENT_TYPES} />
			</div>

			{#if paymentType === 'regular'}
				<div class="space-y-1.5">
					<Label for="con-frequency">Frequency</Label>
					<!-- A dropdown, not a third button group: four periods with names this long do not
					     fit a drawer's width, and a segmented control that scrolls sideways hides half
					     its options. Morgan Lloyd's `cam_frequency` needs the actual period, which is
					     why "regular" on its own is never a recordable answer. -->
					<Select.Root type="single" bind:value={regular}>
						<Select.Trigger id="con-frequency" class="w-full"
							>{regularLabel(regular)}</Select.Trigger
						>
						<Select.Content>
							{#each REGULAR_FREQUENCIES as option (option.value)}
								<Select.Item value={option.value} label={option.label} />
							{/each}
						</Select.Content>
					</Select.Root>
				</div>
			{/if}

			<div class="space-y-1.5">
				<Label for="con-due">Expected by</Label>
				<Input id="con-due" type="date" bind:value={dueInput} />
				<p class="text-muted-foreground text-xs">
					When the money is due. The custodian is told to expect it by this date.
				</p>
			</div>

			{#if category}
				<Separator />
				<div>
					<p class="mb-2 text-xs font-medium">Relief at source</p>
					<GrossUp amount={value} {category} />
				</div>

				<div class="space-y-1.5">
					<Label for="con-ras">Relief paid in advance (optional)</Label>
					<MoneyInput id="con-ras" bind:value={rasAmount} {currency} min={0} />
					<p class="text-muted-foreground text-xs">
						Set this when the scheme credits the basic-rate relief to the pension up front, rather
						than waiting for HMRC to repay it. Leave it blank if relief is only added once HMRC
						pays.
					</p>
				</div>
			{/if}

			{#if needsEmployer}
				<Separator />
				<div class="border-border/70 space-y-3 rounded-lg border p-3">
					<p class="text-muted-foreground text-xs">
						Morgan Lloyd needs the employer's details when they are the ones paying.
					</p>
					<div class="space-y-1.5">
						<Label for="con-employer">Contributor name</Label>
						<Input id="con-employer" bind:value={employerName} placeholder="Employer name" />
					</div>
					<!-- Postcoder lookup ahead of the fields it fills. Typing a contributor's address by
					     hand is how a postcode ends up in the town field, and the address block is what
					     Morgan Lloyd validates. -->
					<div class="space-y-1.5">
						<Label for="con-address-lookup">Find the address</Label>
						<AddressLookup
							id="con-address-lookup"
							bind:value={employerAddress}
							label="Search for the contributor's address or postcode"
						/>
					</div>
					<div class="space-y-1.5">
						<Label for="con-line1">Address line 1</Label>
						<Input id="con-line1" bind:value={employerLine1} />
					</div>
					<div class="space-y-1.5">
						<Label for="con-city">Town or city</Label>
						<Input id="con-city" bind:value={employerCity} />
					</div>
					<div class="space-y-1.5">
						<Label for="con-postcode">Postcode</Label>
						<Input id="con-postcode" bind:value={employerPostcode} />
					</div>
					<div class="space-y-1.5">
						<Label for="con-email">Email</Label>
						<EmailInput
							id="con-email"
							value={employerEmail}
							onchange={(v) => (employerEmail = v)}
						/>
					</div>
				</div>
			{/if}
		</div>

		<Sheet.Footer class="border-t">
			{#if employerMissing}
				<p class="text-destructive basis-full text-xs">
					An employer contribution needs the contributor's name.
				</p>
			{/if}
			<Button variant="ghost" onclick={() => (open = false)}>Cancel</Button>
			<Button onclick={book} disabled={!ready}>
				<CheckIcon class="mr-1.5 size-4" />
				{saving ? 'Recording…' : 'Record contribution'}
			</Button>
		</Sheet.Footer>
	</Sheet.Content>
</Sheet.Root>
