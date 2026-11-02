<script lang="ts">
	// "Transfer in", in a side drawer from an account page.
	//
	// ONE VOCABULARY, gated by the receiving account. Cash vs in-specie is asked of every
	// account and never defaulted (Cash means the ceding scheme sells). Full vs partial is
	// hidden on a Seccl account, including a Seccl SIPP — Seccl administers its own scheme.
	// A pension asks drawdown status and, at the bottom, safeguarded rights: a checkbox,
	// and the advising firm's FCA reference once that box is ticked. The ceding scheme's
	// product type (`cam_pensiontype`) and the crystallised split are the SEI SIPP that
	// Morgan Lloyd administers. The crystallised amount is derived from the status so it
	// cannot disagree with the total. A Seccl account starts from the client's address.
	//
	// Still not asked, because the account already answers them: the wrapper (pension / ISA /
	// GIA), the custodian, the base currency. Asking again is a question with one correct
	// answer an adviser can nonetheless get wrong, on a record that instructs a real movement.
	//
	// FORM DRAWER (`sm:max-w-xl`). The primitive is the illustration width (`sm:w-[58vw]`); this
	// cap is the previous wide, now the money-movement size — Payment / Withdraw / Ad hoc fee
	// use the same. Mobile is full-width on the primitive; do not add `w-3/4` or a vw width here.
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { AddressLookup, type Address } from '../../ui/address-lookup/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Combobox } from '../../ui/combobox/index.js';
	import type { ComboboxOption } from '../../ui/combobox/index.js';
	import { Checkbox } from '../../ui/checkbox/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import MoneyInput from '../../ui/money-input/money-input.svelte';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { Switch } from '../../ui/switch/index.js';
	import * as Sheet from '../../ui/sheet/index.js';
	import {
		CEDING_CLIENT_ADDRESS_INFO,
		formSpecFor,
		PENSION_TYPE_OPTIONS,
		SAFEGUARDED_BENEFITS_QUESTION,
		SAFEGUARDED_FCA_INFO,
		TRANSFER_BASIS_OPTIONS,
		TRANSFER_CRYSTALLISATION_OPTIONS,
		TRANSFER_EXTENT_OPTIONS
	} from './api.js';

	export interface TransferProvider {
		id: number;
		name: string;
		masterparty_id?: string | null;
		origo_id?: string | null;
	}

	let {
		open = $bindable(false),
		account,
		client = null,
		clientAddress = null,
		endpoint,
		onraised
	}: {
		open?: boolean;
		/** The account the money is transferring INTO. Its counterparties gate the extra fields;
		 *  its currency and wrapper still make the route/custody questions unnecessary. */
		account: {
			id: number;
			/** The account's uid — the portal endpoints these forms post to are addressed by it
			 *  (`/accounts/[uid]/transfers`, `/accounts/[uid]/contributions`). */
			uid: string;
			name: string | null;
			currency?: string | null;
			custodian?: string | null;
			scheme_administrator?: string | null;
			is_pension?: boolean | null;
		};
		client?: string | null;
		/** The owning client's address. A Seccl transfer starts from this and only asks for
		 *  another when the ceding scheme holds a different one. `lines` is how the contact
		 *  record stores the street; `line1` / `line2` is the flattened shape. */
		clientAddress?: {
			line1?: string | null;
			line2?: string | null;
			lines?: Array<string | null> | null;
			city?: string | null;
			region?: string | null;
			postcode?: string | null;
			country?: string | null;
		} | null;
		/** Override for a host portal's same-origin proxy. */
		endpoint?: string;
		/** Fired after a successful raise so the page can refresh. */
		onraised?: (result: { uid: string }) => void;
	} = $props();

	const spec = $derived(formSpecFor(account));
	const basisOptions = $derived(
		spec.allowCash
			? TRANSFER_BASIS_OPTIONS
			: TRANSFER_BASIS_OPTIONS.filter((option) => option.value !== 'cash')
	);

	let providerName = $state('');
	let reference = $state('');
	/** Major units (pounds), which is what MoneyInput binds and what the API takes. */
	let value = $state<number | null>(null);
	/** No default: Cash means the ceding scheme liquidates, and both values are valid. */
	let basis = $state<string | undefined>(undefined);
	let extent = $state<string | undefined>(undefined);
	let crystallisation = $state<string | undefined>(undefined);
	let crystallisedAmount = $state<number | null>(null);
	let pensionType = $state<string | undefined>(undefined);
	let safeguardedBenefits = $state(false);
	let externalAccountType = $state('');
	let documentReference = $state('');
	let addressLine1 = $state('');
	let addressLine2 = $state('');
	let addressCity = $state('');
	let addressPostcode = $state('');
	/** Visible default the adviser submits. Seccl refuses a blank country; we do not fill it later. */
	let addressCountry = $state('GB');
	/** On until the adviser says the ceding scheme holds a different address. */
	let useClientAddress = $state(true);
	let manualAddress = $state(false);
	let pickedAddress = $state<Address | null>(null);
	let fcaReference = $state('');

	let providers = $state<TransferProvider[]>([]);
	let loadedProviders = $state(false);
	let saving = $state(false);
	let error = $state<string | null>(null);
	/** The local transfer exists. A failed provider call must not raise a second one. */
	let saved = $state(false);

	const currency = $derived(account.currency || 'GBP');
	const needsCrystallisedAmount = $derived(
		crystallisation === 'crystallised' || crystallisation === 'partial'
	);
	/** Street lines from either spelling of the contact address, then city, region, postcode. */
	function clientAddressParts(): {
		line1: string;
		line2: string;
		city: string;
		postcode: string;
		country: string;
		display: string[];
	} {
		const stored = (clientAddress?.lines ?? []).map((part) => (part ?? '').trim()).filter(Boolean);
		const explicitLine1 = (clientAddress?.line1 ?? '').trim();
		const line1 = explicitLine1 || stored[0] || '';
		const explicitLine2 = (clientAddress?.line2 ?? '').trim();
		const rest = explicitLine1 ? '' : stored.slice(1).join(', ');
		const region = (clientAddress?.region ?? '').trim();
		const line2 = [explicitLine2 || rest, region].filter(Boolean).join(', ');
		const city = (clientAddress?.city ?? '').trim();
		const postcode = (clientAddress?.postcode ?? '').trim();
		const country = (clientAddress?.country ?? '').trim() || 'GB';
		return {
			line1,
			line2,
			city,
			postcode,
			country,
			display: [line1, line2, city, postcode, country === 'GB' ? '' : country].filter(Boolean)
		};
	}

	const clientAddressLines = $derived(clientAddressParts().display);
	const needsSeiInstruction = $derived(spec.askSeiInstruction && basis === 'in_specie');
	const ready = $derived(
		!!basis &&
			(!spec.askExtent || !!extent) &&
			!!providerName.trim() &&
			!saving &&
			!!value &&
			value > 0 &&
			(!spec.askPensionFacts || !!crystallisation) &&
			(!spec.askPensionType || !!pensionType) &&
			(!spec.askPensionFacts ||
				!needsCrystallisedAmount ||
				(crystallisedAmount != null &&
					crystallisedAmount > 0 &&
					crystallisedAmount <= value &&
					(crystallisation !== 'partial' || crystallisedAmount < value) &&
					(crystallisation !== 'crystallised' || crystallisedAmount === value))) &&
			(!spec.askCedingAddress ||
				(!!addressLine1.trim() && !!addressPostcode.trim() && !!addressCountry.trim())) &&
			(!(spec.askPensionFacts && safeguardedBenefits) || !!fcaReference.trim()) &&
			(!needsSeiInstruction || (!!externalAccountType.trim() && !!documentReference.trim()))
	);

	$effect(() => {
		if (crystallisation === 'crystallised' && value != null && value > 0) {
			crystallisedAmount = value;
		}
	});

	function applyClientAddress(): void {
		const parts = clientAddressParts();
		addressLine1 = parts.line1;
		addressLine2 = parts.line2;
		addressCity = parts.city;
		addressPostcode = parts.postcode;
		addressCountry = parts.country;
	}

	$effect(() => {
		if (!useClientAddress) return;
		applyClientAddress();
	});

	$effect(() => {
		if (useClientAddress || !pickedAddress) return;
		addressLine1 = pickedAddress.line1;
		addressLine2 = pickedAddress.line2 ?? '';
		addressCity = pickedAddress.town;
		addressPostcode = pickedAddress.postcode;
		addressCountry = (pickedAddress.countryCode || pickedAddress.country || 'GB').trim() || 'GB';
	});

	// Deduplicated BY NAME. A firm's register can hold the same provider twice (observed: two
	// "Carey Pensions UK" rows), and since a transfer records the ceding provider's NAME rather
	// than its id, two entries spelled identically are the same choice — offering both asks the
	// adviser to pick between indistinguishable options. Their counterparty ids are merged into
	// one hint so nothing is hidden.
	const options = $derived<ComboboxOption[]>(
		[...new Map(providers.map((p) => [p.name, p] as const)).keys()].map((name) => {
			const all = providers.filter((p) => p.name === name);
			const ids = [
				...new Set(
					all.flatMap(
						(p) =>
							[
								p.origo_id && `Origo ${p.origo_id}`,
								p.masterparty_id && `MP ${p.masterparty_id}`
							].filter(Boolean) as string[]
					)
				)
			];
			return { value: name, label: name, hint: ids.join(' · ') };
		})
	);

	// Fetch the register the first time the drawer opens, not on mount: most account visits
	// never open it, and the list is per-firm rather than per-account so it is fetched once.
	$effect(() => {
		if (!open || loadedProviders) return;
		loadedProviders = true;
		void (async () => {
			try {
				const res = await fetch(endpoint ?? `/accounts/${account.uid}/transfers`);
				if (res.ok) providers = ((await res.json()).providers ?? []) as TransferProvider[];
			} catch {
				// An unreachable register is not fatal — the combobox still takes a typed name.
			}
		})();
	});

	$effect(() => {
		if (!spec.askExtent) extent = spec.defaultExtent ?? 'full';
	});

	function reset() {
		providerName = '';
		reference = '';
		value = null;
		basis = undefined;
		extent = spec.defaultExtent ?? undefined;
		crystallisation = undefined;
		crystallisedAmount = null;
		pensionType = undefined;
		safeguardedBenefits = false;
		addressLine1 = '';
		addressLine2 = '';
		addressCity = '';
		addressPostcode = '';
		addressCountry = 'GB';
		useClientAddress = true;
		manualAddress = false;
		pickedAddress = null;
		fcaReference = '';
		externalAccountType = '';
		documentReference = '';
		error = null;
		saved = false;
	}

	async function raise() {
		if (!ready) return;
		saving = true;
		error = null;
		try {
			const res = await fetch(endpoint ?? `/accounts/${account.uid}/transfers`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					ceding_provider: providerName.trim(),
					policy_number: reference.trim() || null,
					basis,
					extent: spec.askExtent ? extent : (spec.defaultExtent ?? 'full'),
					total_value: value,
					...(spec.askPensionFacts
						? {
								crystallisation,
								crystallised_value: needsCrystallisedAmount ? crystallisedAmount : 0,
								safeguarded_benefits: safeguardedBenefits,
								...(safeguardedBenefits ? { fca_reference: fcaReference.trim() } : {})
							}
						: {}),
					...(spec.askPensionType ? { pension_type: Number(pensionType) } : {}),
					...(spec.askCedingAddress
						? {
								provider_address_line1: addressLine1.trim(),
								provider_address_line2: addressLine2.trim() || null,
								provider_city: addressCity.trim() || null,
								provider_postcode: addressPostcode.trim(),
								provider_country: addressCountry.trim()
							}
						: {}),
					...(needsSeiInstruction
						? {
								external_account_type: externalAccountType.trim(),
								documents: [{ document_reference_id: documentReference.trim() }]
							}
						: {})
				})
			});
			const body = await res.json();
			if (!res.ok) {
				error = body.error ?? 'Could not raise the transfer.';
				return;
			}
			onraised?.({ uid: body.uid });
			if (body.action_status === 'awaiting_approval') {
				saved = true;
				error = 'The transfer is saved and waiting for approval.';
				return;
			}
			if (body.provider_error) {
				saved = true;
				error = `The transfer is saved. ${body.provider_error}`;
				return;
			}
			open = false;
			reset();
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
			<Sheet.Title>Transfer in</Sheet.Title>
			<Sheet.Description>
				{#if client}{client} —
				{/if}{account.name ?? 'this account'}
			</Sheet.Description>
		</Sheet.Header>

		<!-- The scroller is this wrapper, not Sheet.Content: the header and footer stay put, and
		     the safeguarded-rights checkbox at the bottom stays reachable. The ceding-provider
		     list is absolutely positioned, so it opens inside this scroller. -->
		<div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
			<div class="space-y-1.5">
				<Label>How it arrives</Label>
				<Segmented bind:value={basis} options={basisOptions} label="How it arrives" />
				<p class="text-muted-foreground text-xs">
					{#if spec.allowCash}
						Cash means the ceding scheme sells the holdings (liquidation). In-specie moves the
						assets as they stand.
					{:else}
						This SEI pension cannot take a cash transfer — the ceding scheme must move the assets
						in-specie.
					{/if}
				</p>
			</div>

			{#if spec.askExtent}
				<div class="space-y-1.5">
					<Label>How much of the pot</Label>
					<Segmented
						bind:value={extent}
						options={TRANSFER_EXTENT_OPTIONS}
						label="How much of the pot"
					/>
				</div>
			{/if}

			{#if needsSeiInstruction}
				<div class="space-y-1.5">
					<Label for="transfer-account-type">Ceding account type</Label>
					<Input
						id="transfer-account-type"
						bind:value={externalAccountType}
						placeholder="The type Select expects"
					/>
					<p class="text-muted-foreground text-xs">
						SEI's own code for the ceding account. GIA, ISA or pension is not that code.
					</p>
				</div>
				<div class="space-y-1.5">
					<Label for="transfer-document">Transfer form reference</Label>
					<Input
						id="transfer-document"
						bind:value={documentReference}
						placeholder="Document reference already uploaded to SEI"
					/>
					<p class="text-muted-foreground text-xs">
						SEI refuses a transfer in that has no uploaded form. This does not upload one.
					</p>
				</div>
			{/if}

			<div class="space-y-1.5">
				<Label for="transfer-value">Transfer value</Label>
				<!-- The shared money field: the currency symbol sits inside the control, the figure is
				     right-aligned in tabular numerals and grouped at rest, and the bound value is a
				     plain number in major units. Not a raw `type="number"` — that increments on the
				     scroll wheel, so a mouse passing over a focused transfer value changes it. -->
				<MoneyInput id="transfer-value" bind:value {currency} min={0} class="h-9" />
				<p class="text-muted-foreground text-xs">
					The value the ceding scheme has advised, in the account's base currency ({currency}). It
					can be corrected later — the transfer reconciles against what actually lands.
				</p>
			</div>

			{#if spec.askPensionFacts}
				<div class="space-y-1.5">
					<Label for="transfer-crystallisation">Drawdown status</Label>
					<SelectMenu
						id="transfer-crystallisation"
						items={[...TRANSFER_CRYSTALLISATION_OPTIONS]}
						bind:value={crystallisation}
						placeholder="Choose…"
						onValueChange={(next) => {
							if (next === 'uncrystallised') crystallisedAmount = null;
							else if (next === 'crystallised' && value != null) crystallisedAmount = value;
							else if (next === 'partial') crystallisedAmount = null;
						}}
					/>
				</div>
				{#if spec.askPensionType}
					<div class="space-y-1.5">
						<Label for="transfer-pension-type">Pension type</Label>
						<SelectMenu
							id="transfer-pension-type"
							items={[...PENSION_TYPE_OPTIONS]}
							bind:value={pensionType}
							placeholder="Ceding scheme's product type"
						/>
					</div>
				{/if}
				{#if needsCrystallisedAmount}
					<div class="space-y-1.5">
						<Label for="transfer-crystallised">Crystallised amount</Label>
						<MoneyInput
							id="transfer-crystallised"
							bind:value={crystallisedAmount}
							{currency}
							min={0}
							class="h-9"
						/>
						<p class="text-muted-foreground text-xs">The crystallised amount of the transfer.</p>
					</div>
				{/if}
			{/if}

			<div class="space-y-1.5">
				<Label for="transfer-provider">Ceding provider</Label>
				<Combobox
					id="transfer-provider"
					bind:value={providerName}
					{options}
					allowCustom
					placeholder="Search providers…"
					emptyText="No provider matches."
					aria-label="Ceding provider"
				/>
			</div>

			<div class="space-y-1.5">
				<Label for="transfer-reference">Provider reference</Label>
				<Input
					id="transfer-reference"
					bind:value={reference}
					placeholder="e.g. AV-9912"
					autocomplete="off"
				/>
				<p class="text-muted-foreground text-xs">
					The ceding scheme's own policy or plan number — what a chase call will be answered on.
				</p>
			</div>

			{#if spec.askCedingAddress}
				<div class="space-y-1.5">
					<div class="flex items-start gap-3">
						<Switch
							id="transfer-use-client-address"
							class="mt-0.5"
							checked={useClientAddress}
							onCheckedChange={(on) => {
								useClientAddress = on === true;
								manualAddress = false;
								pickedAddress = null;
								if (on !== true) {
									addressLine1 = '';
									addressLine2 = '';
									addressCity = '';
									addressPostcode = '';
									addressCountry = 'GB';
								}
							}}
						/>
						<div class="min-w-0 space-y-1">
							<Label for="transfer-use-client-address" class="m-0 font-medium">
								Use current client address
							</Label>
							<p class="text-muted-foreground text-xs">{CEDING_CLIENT_ADDRESS_INFO}</p>
						</div>
					</div>
					{#if useClientAddress}
						<p class="text-sm">
							{#if clientAddressLines.length}
								{clientAddressLines.join(', ')}
							{:else}
								<span class="text-muted-foreground">This client has no address on record.</span>
							{/if}
						</p>
					{:else}
						<AddressLookup id="transfer-address-lookup" bind:value={pickedAddress} />
						<div class="flex justify-end">
							<Button
								type="button"
								variant="ghost"
								size="sm"
								onclick={() => (manualAddress = !manualAddress)}
							>
								{manualAddress ? 'Hide manual address' : 'Enter address manually'}
							</Button>
						</div>
						{#if manualAddress}
							<Input
								id="transfer-address-1"
								bind:value={addressLine1}
								placeholder="Address line 1"
								autocomplete="off"
							/>
							<Input
								id="transfer-address-2"
								bind:value={addressLine2}
								placeholder="Address line 2"
								autocomplete="off"
							/>
							<div class="grid grid-cols-2 gap-2">
								<Input id="transfer-city" bind:value={addressCity} placeholder="Town or city" />
								<Input id="transfer-postcode" bind:value={addressPostcode} placeholder="Postcode" />
							</div>
							<Input id="transfer-country" bind:value={addressCountry} placeholder="Country" />
						{/if}
					{/if}
				</div>
			{/if}

			{#if spec.askPensionFacts}
				<div class="flex items-start gap-3">
					<Checkbox
						id="transfer-safeguarded"
						checked={safeguardedBenefits}
						onCheckedChange={(on) => (safeguardedBenefits = on === true)}
					/>
					<Label for="transfer-safeguarded" class="text-sm leading-5">
						{SAFEGUARDED_BENEFITS_QUESTION}
					</Label>
				</div>
				{#if safeguardedBenefits}
					<div class="space-y-1.5">
						<Label for="transfer-fca">FCA reference</Label>
						<Input id="transfer-fca" bind:value={fcaReference} autocomplete="off" />
						<p class="text-muted-foreground text-xs">{SAFEGUARDED_FCA_INFO}</p>
					</div>
				{/if}
			{/if}

			{#if error}
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			{/if}
		</div>

		<Sheet.Footer class="flex-row justify-end border-t">
			<Button variant="ghost" onclick={() => (open = false)}>Cancel</Button>
			<Button disabled={!ready || saved} onclick={raise}>
				<ArrowLeftRightIcon class="mr-1.5 size-4" />
				{saving ? 'Raising…' : 'Raise transfer'}
			</Button>
		</Sheet.Footer>
	</Sheet.Content>
</Sheet.Root>
