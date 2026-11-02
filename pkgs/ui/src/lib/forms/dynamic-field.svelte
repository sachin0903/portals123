<script lang="ts">
	import type { FormField } from './types.js';
	import { Input } from '../components/ui/input/index.js';
	import { Textarea } from '../components/ui/textarea/index.js';
	import { Checkbox } from '../components/ui/checkbox/index.js';
	import { Label } from '../components/ui/label/index.js';
	import { Button } from '../components/ui/button/index.js';
	import { fieldChoices } from './editor/options';
	import { fieldControlId } from './editor/compute';
	import { getNestedValue } from './editor/visibility';
	import MoneyInput from './field-inputs/money-input.svelte';
	import PercentageInput from './field-inputs/percentage-input.svelte';
	import SortCodeInput from './field-inputs/sort-code-input.svelte';
	import PhoneNumberInput from './field-inputs/phone-number-input.svelte';
	import BankAccountNumberInput from './field-inputs/bank-account-number-input.svelte';
	import DateInput from './field-inputs/date-input.svelte';
	import EmailInput from './field-inputs/email-input.svelte';
	import TextareaWithMinCount from './field-inputs/textarea-with-min-count.svelte';
	import ComboboxInput from './field-inputs/combobox-input.svelte';
	import MultiSelectInput from './field-inputs/multi-select-input.svelte';
	import BeneficiariesInput from './field-inputs/beneficiaries-input.svelte';
	import PhoneNumbersInput from './field-inputs/phone-numbers-input.svelte';
	import RepeatingInput from './field-inputs/repeating-input.svelte';
	import { SelectMenu } from '../components/ui/select-menu/index.js';
	import Segmented from '../components/ui/segmented/segmented.svelte';
	import { AddressLookup, type Address } from '../components/ui/address-lookup/index.js';
	import {
		computeConcat,
		pickAllIdentifierSpecs,
		pickIdentifierCountry,
		type IdentifierCountry
	} from '../national-identifier.js';
	import {
		isNinoType,
		nationalInsuranceError,
		NATIONAL_INSURANCE_MESSAGE,
		NATIONAL_INSURANCE_PATTERN
	} from './national-insurance.js';

	/** Form-stored address: city/region rather than AddressLookup's town/county. */
	type AddressValue = {
		line1: string;
		line2?: string;
		city: string;
		region?: string;
		postcode: string;
		country: string;
	};

	function toLookupAddress(v: AddressValue | null | undefined): Address | null {
		if (!v || typeof v !== 'object') return null;
		return {
			line1: v.line1 ?? '',
			line2: v.line2,
			town: v.city ?? '',
			county: v.region,
			postcode: v.postcode ?? '',
			country: v.country ?? '',
			countryCode: v.country ?? ''
		};
	}

	function fromLookupAddress(a: Address): AddressValue {
		return {
			line1: a.line1,
			...(a.line2 ? { line2: a.line2 } : {}),
			city: a.town,
			...(a.county ? { region: a.county } : {}),
			postcode: a.postcode,
			country: a.countryCode || a.country
		};
	}

	function formatAddressValue(v: AddressValue): string {
		return [v.line1, v.line2, v.city, v.region, v.postcode, countryName(v.country)]
			.filter(Boolean)
			.join(', ');
	}

	function asAddressValue(raw: unknown): AddressValue | null {
		if (!raw || typeof raw !== 'object') return null;
		const o = raw as Record<string, unknown>;
		if (typeof o.line1 !== 'string') return null;
		return {
			line1: o.line1,
			...(typeof o.line2 === 'string' ? { line2: o.line2 } : {}),
			city: typeof o.city === 'string' ? o.city : '',
			...(typeof o.region === 'string' ? { region: o.region } : {}),
			postcode: typeof o.postcode === 'string' ? o.postcode : '',
			country: typeof o.country === 'string' ? o.country : ''
		};
	}

	let {
		field,
		values,
		errors = {},
		dataSources = {},
		applicationData = {},
		verified = false,
		mismatch = false
	}: {
		field: FormField;
		values: Record<string, unknown>;
		errors?: Record<string, string>;
		dataSources?: Record<string, unknown[]>;
		/** Read-only record context — a `display` field reads its value from here (by its key, dotted
		    paths supported), falling back to the collected values. */
		applicationData?: Record<string, unknown>;
		/** A bank details check confirmed the sort code or account number in this field. */
		verified?: boolean;
		/** A sort-code check refused the account number beside this field. */
		mismatch?: boolean;
	} = $props();

	const value = $derived(values[field.fieldKey]);
	const error = $derived(errors[field.fieldKey]);
	const invalid = $derived(Boolean(error));
	const options = $derived(fieldChoices(field, dataSources, value));
	// An optional dropdown starts with a blank, so an answer picked by mistake can be cleared.
	// A required one has no blank: it must have an answer.
	const menuItems = $derived(
		field.required || options.some((o) => o.value === '')
			? options
			: [{ value: '', label: '—' }, ...options]
	);
	const inputId = $derived(fieldControlId(field.uid));
	// A display field shows a record value (from applicationData) when present, else a seeded/computed
	// answer — so it works both for record-context questionnaires and code-seeded forms.
	const displayValue = $derived(getNestedValue(applicationData, field.fieldKey) ?? value);

	// Canonical kind (folds platform-1's legacy aliases onto the offered types).
	const kind = $derived.by(() => {
		switch (field.fieldType) {
			case 'textarea':
				return 'textarea';
			case 'number':
				return 'number';
			case 'checkbox':
				return 'checkbox';
			case 'select':
				return 'select';
			case 'segmented':
				return 'segmented';
			case 'combobox':
				return 'combobox';
			case 'multiSelect':
			case 'multiPicker':
				return 'multiselect';
			case 'date':
			case 'dateOfBirth':
				return 'date';
			case 'currency':
			case 'money':
				return 'money';
			case 'percentage':
				return 'percentage';
			case 'phone':
			case 'phoneNumber':
				return 'phone';
			case 'email':
				return 'email';
			case 'bankAccountNumber':
			case 'accountNumber':
				return 'bank';
			case 'sortCode':
				return 'sortcode';
			case 'address':
				return 'address';
			case 'beneficiaries':
				return 'beneficiaries';
			// Not an alias of `phone`: the singular is one number as a string, this is the repeatable
			// labelled list beside it, whose answer is `PhoneNumber[]`.
			case 'phoneNumbers':
				return 'phoneNumbers';
			case 'repeating':
				return 'repeating';
			case 'nationalIdentifier':
				return 'nationalIdentifier';
			case 'title':
			case 'marital':
			case 'employment':
				return 'select';
			case 'gender':
				return 'segmented';
			case 'display':
				return 'display';
			default:
				return 'text';
		}
	});

	const placeholder = $derived(field.validation?.placeholder ?? undefined);
	const currencyCode = $derived(
		field.options &&
			typeof field.options === 'object' &&
			!Array.isArray(field.options) &&
			'currency' in field.options
			? String((field.options as Record<string, unknown>).currency || 'GBP')
			: 'GBP'
	);

	function set(v: unknown) {
		values[field.fieldKey] = v;
	}

	function patchAddress(patch: Partial<AddressValue>) {
		const current = asAddressValue(value) ?? {
			line1: '',
			city: '',
			postcode: '',
			country: 'GB'
		};
		set({ ...current, ...patch });
	}

	const addressValue = $derived(asAddressValue(value));
	let selectedAddressCountry = $state('GB');
	const addressCountry = $derived(
		addressValue?.country?.trim().toUpperCase() || selectedAddressCountry
	);
	let editingAddress = $state(false);

	type AddressCountryRow = { code?: unknown; name?: unknown; label?: unknown };
	const addressCountryOptions = $derived.by(() => {
		const names = new Map<string, string>();
		for (const raw of dataSources.nationalities ?? []) {
			const row = raw as AddressCountryRow;
			const code = String(row.code ?? '').toUpperCase();
			if (code) names.set(code, String(row.name ?? row.label ?? code));
		}
		const seen = new Set<string>();
		const resolved = (dataSources.countries ?? [])
			.map((raw) => {
				const row = raw as AddressCountryRow;
				const code = String(row.code ?? '').toUpperCase();
				if (!code || seen.has(code)) return null;
				seen.add(code);
				return { value: code, label: String(row.name ?? row.label ?? names.get(code) ?? code) };
			})
			.filter(Boolean) as { value: string; label: string }[];
		if (resolved.length) return resolved.sort((a, b) => a.label.localeCompare(b.label));
		return [...names]
			.map(([value, label]) => ({ value, label }))
			.sort((a, b) => a.label.localeCompare(b.label));
	});

	function countryName(code: string): string {
		return addressCountryOptions.find((option) => option.value === code)?.label ?? code;
	}

	function selectAddressCountry(next: string) {
		if (next === addressCountry) return;
		selectedAddressCountry = next;
		// Postcoder suggestions and every manually-entered line belong to the selected country.
		// Keeping lines after changing scope creates a mixed-country address.
		set(null);
		editingAddress = false;
	}

	function chooseLookupAddress(address: Address | null) {
		if (!address) {
			set(null);
			return;
		}
		set(fromLookupAddress(address));
		selectedAddressCountry = address.countryCode || addressCountry;
		editingAddress = false;
	}

	function enterAddressManually() {
		set({
			line1: '',
			city: '',
			postcode: '',
			country: addressCountry
		} satisfies AddressValue);
		editingAddress = true;
	}

	// ── National identifier ──────────────────────────────────────────────
	let useSecondaryIdentifier = $state(false);

	const nationalityCodes = $derived.by(() => {
		const raw = values['nationality'];
		if (Array.isArray(raw)) return raw.map((c) => String(c));
		if (typeof raw === 'string' && raw) return [raw];
		return [] as string[];
	});

	const taxResidencyCodes = $derived.by(() => {
		const raw = values['tax_residency'];
		if (Array.isArray(raw)) return raw.map((c) => String(c));
		if (typeof raw === 'string' && raw) return [raw];
		return [] as string[];
	});

	const countriesByCode = $derived.by(() => {
		const rows = (dataSources.countries ?? []) as IdentifierCountry[];
		const map = new Map<string, IdentifierCountry>();
		for (const row of rows) {
			if (row?.code) map.set(String(row.code).toUpperCase(), row);
		}
		return map;
	});

	const hasCountriesSource = $derived(countriesByCode.size > 0);

	const niCountryCode = $derived(
		pickIdentifierCountry(
			taxResidencyCodes.length ? taxResidencyCodes : nationalityCodes,
			countriesByCode
		)
	);

	const niSpecs = $derived(pickAllIdentifierSpecs(niCountryCode, countriesByCode));

	const activeNiSpec = $derived.by(() => {
		if (niSpecs.length === 0) return null;
		if (useSecondaryIdentifier && niSpecs.length >= 2) return niSpecs[1]!;
		return niSpecs[0]!;
	});

	const niLabel = $derived(activeNiSpec?.name || activeNiSpec?.localeName || field.label);
	const niPattern = $derived(
		isNinoType(activeNiSpec?.name ?? values['national_identifier_type'])
			? NATIONAL_INSURANCE_PATTERN
			: undefined
	);
	// Shown as the number is typed. The fact-find questions are not inside the posting form, so a
	// `pattern` attribute never runs, and the form's own errors stay hidden until Continue.
	const niShapeError = $derived(
		kind === 'nationalIdentifier'
			? nationalInsuranceError(activeNiSpec?.name ?? values['national_identifier_type'], value)
			: null
	);

	// Keep type + CONCAT value in sync with nationality / DOB / name.
	$effect(() => {
		if (kind !== 'nationalIdentifier' || !hasCountriesSource) return;
		if (field.fieldKey === 'national_identifier') {
			values['national_identifier_type'] = activeNiSpec?.name ?? '';
		}
		if (!activeNiSpec) return;
		if (activeNiSpec.name === 'CONCAT') {
			const concat = computeConcat({
				dob: (values['dob'] as string | Date | null | undefined) ?? null,
				firstName: (values['first_name'] as string | null | undefined) ?? null,
				surname: (values['surname'] as string | null | undefined) ?? null
			});
			if (concat != null && values[field.fieldKey] !== concat) {
				values[field.fieldKey] = concat;
			}
		}
	});
</script>

{#if kind === 'checkbox'}
	<div class="space-y-1">
		<div class="flex w-full items-center gap-2">
			<Checkbox id={inputId} checked={value === true} onCheckedChange={(v) => set(Boolean(v))} />
			<Label for={inputId} class="m-0">{field.label}</Label>
			<!-- The SAME marker every other field carries. This branch had none, so a required tick and
			     an optional one were indistinguishable — and on a form whose whole convention is
			     "unmarked means you must answer it", a control that is never marked either way reads as
			     optional. That is half of "you cannot tell what is mandatory until you press Save".
			     Trailing, like the marker on every other field: sitting it against the words made a
			     short question and a long one end in different places. -->
			{#if !field.required}
				<span class="text-muted-foreground ml-auto shrink-0 text-xs tracking-wide uppercase">
					Optional
				</span>
			{/if}
		</div>
		{#if field.description}<p class="text-muted-foreground text-sm">{field.description}</p>{/if}
		{#if error}<p class="text-destructive text-sm">{error}</p>{/if}
	</div>
{:else}
	<div class="space-y-1.5">
		<!-- "OPTIONAL" is pushed to the right of the row rather than trailing the label in brackets.
		     On a dense form the bracketed version reads as part of the question ("Initial (optional)")
		     and, repeated down a column, it is the loudest thing on screen — which inverts the point:
		     the label is what you scan for, and optionality is the footnote. -->
		<div class="flex items-baseline justify-between gap-2">
			<Label for={inputId} class="min-w-0">
				{kind === 'nationalIdentifier' && hasCountriesSource ? niLabel : field.label}
			</Label>
			{#if !field.required && kind !== 'display'}
				<span class="text-muted-foreground shrink-0 text-xs tracking-wide uppercase">Optional</span>
			{/if}
		</div>
		{#if field.description}<p class="text-muted-foreground text-sm">{field.description}</p>{/if}

		{#if kind === 'textarea'}
			{#if field.validation?.minLength || field.validation?.maxLength}
				<TextareaWithMinCount
					id={inputId}
					{value}
					minCount={field.validation?.minLength}
					maxLength={field.validation?.maxLength}
					rows={field.validation?.rows ?? 4}
					{placeholder}
					{invalid}
					onchange={set}
				/>
			{:else}
				<Textarea
					id={inputId}
					{placeholder}
					value={String(value ?? '')}
					aria-invalid={invalid || undefined}
					oninput={(e) => set(e.currentTarget.value)}
				/>
			{/if}
		{:else if kind === 'number'}
			<Input
				id={inputId}
				type="number"
				{placeholder}
				value={(value ?? '') as string | number}
				autocomplete="off"
				aria-invalid={invalid || undefined}
				oninput={(e) => set(e.currentTarget.value === '' ? '' : Number(e.currentTarget.value))}
			/>
		{:else if kind === 'select'}
			<!-- Default size, NOT `sm`: the sm trigger is h-7 and every Input on the same row is h-8,
			     so a select sat a pixel short of its neighbours on every form in the app. -->
			<SelectMenu
				id={inputId}
				items={menuItems}
				value={value == null ? undefined : String(value)}
				placeholder="Select…"
				onValueChange={set}
				class={invalid ? 'border-destructive' : undefined}
			/>
		{:else if kind === 'segmented'}
			<!-- A short, closed choice — two or three options the adviser should see all of at once.
			     The alternative is a dropdown that hides half the answer behind a click, for a
			     question whose whole content is "which of these two". -->
			<Segmented
				options={options.map((o) => ({ value: o.value, label: o.label }))}
				value={value == null ? undefined : String(value)}
				onValueChange={set}
				shape={field.validation?.shape === 'square' ? 'square' : 'pill'}
				class={invalid ? 'ring-destructive ring-2' : undefined}
			/>
		{:else if kind === 'combobox'}
			<ComboboxInput id={inputId} {value} {options} {invalid} onchange={set} />
		{:else if kind === 'multiselect'}
			<MultiSelectInput id={inputId} {value} {options} {invalid} onchange={set} />
		{:else if kind === 'money'}
			<MoneyInput id={inputId} {value} {currencyCode} {placeholder} {invalid} onchange={set} />
		{:else if kind === 'percentage'}
			<PercentageInput id={inputId} {value} {placeholder} {invalid} onchange={set} />
		{:else if kind === 'date'}
			<DateInput id={inputId} {value} {placeholder} {invalid} onchange={set} />
		{:else if kind === 'phone'}
			<PhoneNumberInput id={inputId} {value} {placeholder} {invalid} onchange={set} />
		{:else if kind === 'email'}
			<EmailInput id={inputId} {value} {placeholder} {invalid} onchange={set} />
		{:else if kind === 'bank'}
			<BankAccountNumberInput
				id={inputId}
				{value}
				{placeholder}
				{invalid}
				{verified}
				{mismatch}
				onchange={set}
			/>
		{:else if kind === 'sortcode'}
			<SortCodeInput id={inputId} {value} {placeholder} {invalid} {verified} onchange={set} />
		{:else if kind === 'address'}
			<div class="space-y-3">
				<div class="grid gap-2 sm:grid-cols-[minmax(10rem,1fr)_minmax(14rem,2fr)]">
					<div class="space-y-1">
						<Label for={`${inputId}-country`} class="text-muted-foreground text-xs">Country</Label>
						<ComboboxInput
							id={`${inputId}-country`}
							value={addressCountry}
							options={addressCountryOptions}
							placeholder="Select a country…"
							{invalid}
							onchange={selectAddressCountry}
						/>
					</div>
					<div class="space-y-1">
						<Label for={inputId} class="text-muted-foreground text-xs">Find address</Label>
						{#key addressCountry}
							<AddressLookup
								id={inputId}
								endpoint="/api/postcoder"
								country={addressCountry}
								placeholder={addressCountry === 'GB'
									? 'Start typing an address or postcode…'
									: 'Start typing an address, city or postal code…'}
								{invalid}
								bind:value={() => toLookupAddress(addressValue), chooseLookupAddress}
							/>
						{/key}
					</div>
				</div>
				{#if !addressValue}
					<div class="flex justify-end">
						<Button type="button" variant="ghost" size="sm" onclick={enterAddressManually}>
							Enter address manually
						</Button>
					</div>
				{:else}
					<div
						class="border-input bg-muted/40 flex items-start justify-between gap-3 rounded-lg border px-2.5 py-2 text-sm"
					>
						<p class="min-w-0 flex-1">{formatAddressValue(addressValue)}</p>
						<div class="flex shrink-0 items-center gap-1">
							<Button
								type="button"
								variant="ghost"
								size="sm"
								onclick={() => (editingAddress = !editingAddress)}
							>
								{editingAddress ? 'Done' : 'Edit'}
							</Button>
							<Button
								type="button"
								variant="ghost"
								size="sm"
								onclick={() => {
									set(null);
									editingAddress = false;
								}}
							>
								Clear
							</Button>
						</div>
					</div>
					{#if editingAddress}
						<div class="grid gap-2 sm:grid-cols-2">
							<div class="space-y-1 sm:col-span-2">
								<Label for={`${inputId}-line1`} class="text-muted-foreground text-xs">Line 1</Label>
								<Input
									id={`${inputId}-line1`}
									value={addressValue.line1}
									autocomplete="address-line1"
									aria-invalid={invalid || undefined}
									oninput={(e) => patchAddress({ line1: e.currentTarget.value })}
								/>
							</div>
							<div class="space-y-1 sm:col-span-2">
								<Label for={`${inputId}-line2`} class="text-muted-foreground text-xs"
									>Line 2 <span class="font-normal">(optional)</span></Label
								>
								<Input
									id={`${inputId}-line2`}
									value={addressValue.line2 ?? ''}
									autocomplete="address-line2"
									aria-invalid={invalid || undefined}
									oninput={(e) =>
										patchAddress({
											line2: e.currentTarget.value || undefined
										})}
								/>
							</div>
							<div class="space-y-1">
								<Label for={`${inputId}-city`} class="text-muted-foreground text-xs"
									>City / town</Label
								>
								<Input
									id={`${inputId}-city`}
									value={addressValue.city}
									autocomplete="address-level2"
									aria-invalid={invalid || undefined}
									oninput={(e) => patchAddress({ city: e.currentTarget.value })}
								/>
							</div>
							<div class="space-y-1">
								<Label for={`${inputId}-postcode`} class="text-muted-foreground text-xs"
									>Postal / ZIP code</Label
								>
								<Input
									id={`${inputId}-postcode`}
									value={addressValue.postcode}
									autocomplete="postal-code"
									aria-invalid={invalid || undefined}
									oninput={(e) => patchAddress({ postcode: e.currentTarget.value })}
								/>
							</div>
							<div class="space-y-1 sm:col-span-2">
								<Label for={`${inputId}-region`} class="text-muted-foreground text-xs"
									>State / region / county <span class="font-normal">(optional)</span></Label
								>
								<Input
									id={`${inputId}-region`}
									value={addressValue.region ?? ''}
									autocomplete="address-level1"
									aria-invalid={invalid || undefined}
									oninput={(e) =>
										patchAddress({
											region: e.currentTarget.value || undefined
										})}
								/>
							</div>
						</div>
					{/if}
				{/if}
			</div>
		{:else if kind === 'beneficiaries'}
			<BeneficiariesInput {value} {invalid} onchange={set} />
		{:else if kind === 'phoneNumbers'}
			<PhoneNumbersInput {value} {invalid} onchange={set} />
		{:else if kind === 'repeating'}
			<RepeatingInput {field} {value} {invalid} id={inputId} onchange={set} />
		{:else if kind === 'nationalIdentifier'}
			{#if hasCountriesSource}
				<div class="space-y-2">
					<Input
						id={inputId}
						placeholder={activeNiSpec?.example ?? placeholder}
						value={String(value ?? '')}
						autocomplete="off"
						aria-invalid={Boolean(error || niShapeError) || undefined}
						readonly={activeNiSpec?.name === 'CONCAT'}
						pattern={niPattern}
						title={niPattern ? NATIONAL_INSURANCE_MESSAGE : undefined}
						oninput={(e) => set(e.currentTarget.value)}
					/>
					{#if niSpecs.length >= 2}
						<label class="flex items-center gap-2 text-sm">
							<Checkbox
								checked={useSecondaryIdentifier}
								onCheckedChange={(v) => (useSecondaryIdentifier = Boolean(v))}
							/>
							<span class="text-muted-foreground">No primary identifier</span>
						</label>
					{/if}
				</div>
			{:else}
				<Input
					id={inputId}
					{placeholder}
					value={String(value ?? '')}
					autocomplete="off"
					aria-invalid={invalid || undefined}
					oninput={(e) => set(e.currentTarget.value)}
				/>
			{/if}
		{:else if kind === 'display'}
			<!-- Read-only value from the record context (or a seeded/computed answer). Not an input, so
			     it never appears in the collected payload. -->
			<div
				id={inputId}
				class="border-input bg-muted/40 text-foreground flex min-h-8 items-center rounded-lg border px-2.5 py-1.5 text-sm"
			>
				{#if displayValue === undefined || displayValue === null || displayValue === ''}
					<span class="text-muted-foreground">—</span>
				{:else}
					{displayValue}
				{/if}
			</div>
		{:else}
			<Input
				id={inputId}
				{placeholder}
				value={String(value ?? '')}
				autocomplete="off"
				aria-invalid={invalid || undefined}
				oninput={(e) => set(e.currentTarget.value)}
			/>
		{/if}

		{#if error || niShapeError}<p class="text-destructive text-sm">{error || niShapeError}</p>{/if}
	</div>
{/if}
