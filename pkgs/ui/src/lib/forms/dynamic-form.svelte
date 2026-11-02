<script lang="ts">
	import { fieldWidthSpan, type FormField } from './types.js';
	import { layoutFields, presentField } from './layout.js';
	import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card/index.js';
	import { Button } from '../components/ui/button/index.js';
	import { Switch } from '../components/ui/switch/index.js';
	import {
		BANK_CHECKING_MESSAGE,
		BankAccountCheck,
		bankDigits,
		bankPairComplete,
		type BankCheckResult,
		type BankCheckStatus
	} from '../components/ui/bank-account-check/index.js';
	import DynamicField from './dynamic-field.svelte';
	import { bankPairs, rejectedKey, type BankPair } from './bank-pair.js';
	import { nationalInsuranceError } from './national-insurance.js';
	import { isFieldVisible } from './editor/visibility';
	import { validateField } from './editor/validate';
	import {
		pendingDefaults,
		computedValues,
		fieldControlId,
		isDisplayField,
		sectionAnchorId
	} from './editor/compute';
	import FormSectionNav from './form-section-nav.svelte';
	import { wantsSectionNav } from './section-nav.js';
	import { tick, untrack, type Snippet } from 'svelte';

	let {
		fields,
		applicationData = {},
		dataSources = {},
		values = $bindable({}),
		showSubmit = true,
		submitLabel = 'Submit',
		sectionNav = false,
		sectionMeta = {},
		enabledSections = $bindable({}),
		onsubmit
	}: {
		fields: FormField[];
		applicationData?: Record<string, unknown>;
		dataSources?: Record<string, unknown[]>;
		values?: Record<string, unknown>;
		showSubmit?: boolean;
		submitLabel?: string;
		/** Allow a sticky left rail listing the sections, with scroll-spy + click-to-jump. It shows
		    only on a form long enough to need it (`wantsSectionNav`): a short form scrolls instead.
		    Off by default so existing single-column usages are unchanged. Set `--form-scroll-offset`
		    to clear a sticky app header. */
		sectionNav?: boolean;
		/**
		 * Per-section presentation, keyed by the section title the fields declare.
		 *
		 * `description` is a line under the heading — what the section is FOR, which a long form
		 * needs and a field label cannot carry.
		 *
		 * `optional: true` puts a switch in the header. A section switched off keeps its answers (so
		 * toggling back does not lose typing) but its fields leave `collect()`, so the host builds a
		 * payload that simply does not mention them. That is the honest shape for "no contributions"
		 * — distinct from "contributions of zero".
		 */
		sectionMeta?: Record<
			string,
			{ description?: string; optional?: boolean; defaultOn?: boolean; lead?: Snippet }
		>;
		/** Which optional sections are on, keyed by title. Bindable so a host can read it back or
		    pre-set it (e.g. switching Income on because the basis is drawdown). */
		enabledSections?: Record<string, boolean>;
		onsubmit?: (values: Record<string, unknown>) => void;
	} = $props();

	/** A section is on unless it is optional and has been switched off. */
	const sectionOn = (title: string): boolean => {
		const meta = sectionMeta[title];
		if (!meta?.optional) return true;
		return enabledSections[title] ?? meta.defaultOn ?? false;
	};

	let errors = $state<Record<string, string>>({});
	let submitted = $state(false);
	let bankStatus = $state<Record<string, BankCheckStatus>>({});
	let bankResult = $state<Record<string, BankCheckResult | null>>({});
	const fieldIdentity = $derived(fields.map((field) => field.uid).join('|'));
	$effect(() => {
		const identity = fieldIdentity;
		untrack(() => {
			submitted = false;
			errors = {};
			void identity;
		});
	});

	// values override cross-entity applicationData (platform-1 order).
	const combined = $derived({ ...applicationData, ...values });

	const visibleFields = $derived(
		fields
			.filter((f) => isFieldVisible(f, combined, applicationData))
			.slice()
			.sort((a, b) => a.sortOrder - b.sortOrder)
	);

	const sections = $derived.by(() => {
		const map = new Map<string, FormField[]>();
		for (const f of visibleFields) {
			if (f.validation?.hidden) continue;
			const key = f.section || 'Additional information';
			const arr = map.get(key) ?? [];
			arr.push(f);
			map.set(key, arr);
		}
		return [...map.entries()];
	});

	// Section anchors for the optional nav rail — id + label per visible section, plus how many of
	// that section's fields are currently failing. The count is what makes a long fact-find
	// answerable after a refused submit: "7 fields need attention" at the bottom of the page does
	// not say WHERE, and a fact-find is exactly the form where the missing answer is three sections
	// above the button you just pressed.
	const sectionNavItems = $derived(
		sections.map(([title, sectionFields]) => ({
			id: sectionAnchorId(title),
			label: title,
			errors: sectionFields.filter((f) => errors[f.fieldKey]).length
		}))
	);

	// Seed declared defaults once, and keep computedFrom fields in step with the value they depend
	// on. Writes run untracked so setting a value never re-triggers the effect that set it.
	$effect(() => {
		// Track `fields` (re-seed only when the field set changes, not on every keystroke); the value
		// reads/writes below stay untracked so seeding never re-triggers this effect.
		const current = fields;
		untrack(() => {
			for (const [key, v] of Object.entries(pendingDefaults(current, values))) values[key] = v;
		});
	});
	$effect(() => {
		// Reads each computedFrom field's matchOn value (+ dataSources), so it re-runs when the
		// selection it depends on changes; the writes below stay untracked.
		const patch = computedValues(fields, values, dataSources);
		untrack(() => {
			for (const [key, v] of Object.entries(patch)) if (values[key] !== v) values[key] = v;
		});
	});

	const pairs = $derived(
		bankPairs(
			visibleFields
				.filter((field) => sectionOn(field.section || 'Additional information'))
				.map((field) => ({ fieldKey: field.fieldKey, fieldType: field.fieldType }))
		)
	);

	/** A 7-digit account number is the same account with a leading zero. The directory wants eight. */
	function directoryAccount(value: unknown): string {
		const digits = bankDigits(value);
		return digits.length === 7 ? digits.padStart(8, '0') : digits;
	}

	function recordBankCheck(
		pair: BankPair,
		next: BankCheckStatus,
		nextResult: BankCheckResult | null
	) {
		bankStatus = { ...bankStatus, [pair.prefix]: next };
		bankResult = { ...bankResult, [pair.prefix]: nextResult };
		if (next !== 'confirmed' || !pair.nameKey || !nextResult?.bank_name) return;
		const current = values[pair.nameKey];
		if (current == null || String(current).trim() === '')
			values[pair.nameKey] = nextResult.bank_name;
	}

	function bankFieldAdornment(fieldKey: string): {
		verified: boolean;
		mismatch: boolean;
	} {
		const member = pairs.find((item) => item.sortKey === fieldKey || item.accountKey === fieldKey);
		if (!member) return { verified: false, mismatch: false };
		const state = bankStatus[member.prefix] ?? 'idle';
		const result = bankResult[member.prefix] ?? null;
		if (fieldKey === member.sortKey) {
			return { verified: state === 'confirmed', mismatch: false };
		}
		if (fieldKey === member.accountKey) {
			return {
				verified: state === 'confirmed',
				mismatch: state === 'rejected' && rejectedKey(member, result?.state) === fieldKey
			};
		}
		return { verified: false, mismatch: false };
	}

	function bankFieldError(pair: BankPair): string | null {
		if (!bankPairComplete(values[pair.sortKey], directoryAccount(values[pair.accountKey])))
			return null;
		const state = bankStatus[pair.prefix] ?? 'idle';
		if (state === 'confirmed' || state === 'unavailable') return null;
		if (state === 'rejected') {
			return (
				bankResult[pair.prefix]?.message || "This account number isn't valid for that sort code."
			);
		}
		return BANK_CHECKING_MESSAGE;
	}

	function computeErrors(): Record<string, string> {
		const next: Record<string, string> = {};
		for (const f of visibleFields) {
			if (isDisplayField(f)) continue; // read-only value, nothing to validate
			if (f.validation?.hidden) continue; // derived sidecar; its owning field validates the input
			// A switched-off section is not being answered, so it cannot be answered wrongly —
			// otherwise a required field in a section the adviser deliberately excluded would block
			// submission with an error they cannot see.
			if (!sectionOn(f.section || 'Additional information')) continue;
			const err = validateField(f, values[f.fieldKey]);
			if (err) next[f.fieldKey] = err;
		}
		for (const pair of pairs) {
			const err = bankFieldError(pair);
			const key = rejectedKey(pair, bankResult[pair.prefix]?.state);
			if (err && !next[key]) next[key] = err;
		}
		const niField = visibleFields.find((field) => field.fieldKey === 'national_identifier');
		if (
			niField &&
			!niField.validation?.hidden &&
			sectionOn(niField.section || 'Additional information') &&
			!next.national_identifier
		) {
			const niErr = nationalInsuranceError(
				values.national_identifier_type,
				values.national_identifier
			);
			if (niErr) next.national_identifier = niErr;
		}
		return next;
	}

	// Once the user has attempted a submit, re-validate live as they fix fields.
	// Only visible fields are validated, so a field that becomes hidden drops its
	// error automatically (its value is likewise excluded from the payload below).
	$effect(() => {
		if (submitted) errors = computeErrors();
	});

	/**
	 * Move to the first field that failed, in the order they appear on screen.
	 *
	 * A refused submit used to leave the adviser where they were, with a count at the bottom of the
	 * page and nothing to click: on a multi-section fact-find the missing answer is commonly off
	 * screen, and "required" fields are marked only by the ABSENCE of the word Optional, so hunting
	 * for it means re-reading every question. Focus rather than scroll alone, because focus is what
	 * a screen reader follows and what puts the caret where the typing has to go; `preventScroll`
	 * then hands the scrolling to `scrollIntoView`, which honours the page's own
	 * `--form-scroll-offset` and centres the field instead of jamming it under the sticky topbar.
	 *
	 * Best-effort by design: a control that cannot take focus (or a kind that stamps its id on a
	 * wrapper) still gets scrolled to, and a field that is not in the DOM at all is skipped rather
	 * than throwing inside a validation call.
	 */
	async function focusFirstError(): Promise<void> {
		const first = visibleFields.find((f) => errors[f.fieldKey]);
		if (!first) return;
		// The errors have just been assigned; the DOM has not been updated with them yet.
		await tick();
		const el = document.getElementById(fieldControlId(first.uid));
		if (!el) return;
		el.focus({ preventScroll: true });
		el.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}

	// Run validation and mark the form submitted. Returns true when valid.
	// Exposed so a host (e.g. the generate flow) can gate its own Next/Review button.
	export function validate(): boolean {
		submitted = true;
		errors = computeErrors();
		const ok = Object.keys(errors).length === 0;
		// Deliberately not awaited: `validate()` answers synchronously because hosts gate their own
		// Next button on it, and the jump is a side effect of having refused.
		if (!ok) void focusFirstError();
		return ok;
	}

	/** The current answers, keyed by fieldKey (visible fields only). */
	export function collect(): Record<string, unknown> {
		const payload: Record<string, unknown> = {};
		// Display fields are read-only, record-derived values — not answers, so they stay out of the
		// collected payload. A switched-off section's fields go the same way: the host should build a
		// request that does not mention them at all.
		for (const f of visibleFields) {
			if (isDisplayField(f)) continue;
			if (!sectionOn(f.section || 'Additional information')) continue;
			payload[f.fieldKey] = values[f.fieldKey];
		}
		return payload;
	}

	function submit() {
		if (!validate()) return;
		onsubmit?.(collect());
	}

	const errorCount = $derived(Object.keys(errors).length);
</script>

{#snippet answeredField(field: FormField)}
	{@const span = fieldWidthSpan(field.validation?.width)}
	{@const pair = pairs.find((item) => item.anchorKey === field.fieldKey)}
	{@const adornment = bankFieldAdornment(field.fieldKey)}
	<div class="min-w-0 @2xl:col-[span_var(--span)_/_span_var(--span)]" style:--span={span}>
		<DynamicField
			field={presentField(field, applicationData, values)}
			{values}
			{errors}
			{dataSources}
			{applicationData}
			verified={adornment.verified}
			mismatch={adornment.mismatch}
		/>
	</div>
	{#if pair}
		<div class="min-w-0 @2xl:col-span-12">
			<BankAccountCheck
				sortCode={bankDigits(values[pair.sortKey])}
				accountNumber={directoryAccount(values[pair.accountKey])}
				onchecked={(next, nextResult) => recordBankCheck(pair, next, nextResult)}
			/>
		</div>
	{/if}
{/snippet}

{#snippet formBody()}
	<form
		class="space-y-6"
		onsubmit={(e) => {
			e.preventDefault();
			submit();
		}}
	>
		{#each sections as [section, sectionFields] (section)}
			{@const meta = sectionMeta[section]}
			{@const on = sectionOn(section)}
			<section id={sectionAnchorId(section)} class="scroll-mt-[var(--form-scroll-offset,1rem)]">
				<!-- Card is overflow-visible so AddressLookup's inline list can escape the section.
				     Kept explicit: a form section has no edge-to-edge image that needs the radius
				     clip, and Combobox/AddressLookup cannot portal (a Popover lands outside a Sheet's
				     focus trap). -->
				<Card class="overflow-visible">
					<CardHeader>
						<div class="flex items-center justify-between gap-4">
							<CardTitle class="text-xl">{section}</CardTitle>
							{#if meta?.optional}
								<!-- The switch is the section's own control, so it reads as "include this" rather
								     than as one more field inside it. -->
								<Switch
									checked={on}
									onCheckedChange={(next) => (enabledSections[section] = next)}
									aria-label="Include {section}"
								/>
							{/if}
						</div>
						{#if meta?.description}
							<p class="text-muted-foreground text-sm">{meta.description}</p>
						{/if}
					</CardHeader>
					{#if on}
						<CardContent>
							{#if meta?.lead}
								<div class="mb-4">
									{@render meta.lead()}
								</div>
							{/if}
							<!-- 12-column grid once the FORM is wide enough (@2xl is the section's own width, not
							     the window). A field's span is applied only then: on one column, `span 4`
							     would invent extra tracks and shove the next field sideways.
							     A `column: left` field and the `right` fields after it are one row: the
							     option on the left, its fields stacked on the right. -->
							<div class="@container space-y-4">
								{#each layoutFields(sectionFields) as row, i (row.kind === 'pair' ? row.option.uid : `g${i}`)}
									{#if row.kind === 'pair'}
										<div class="grid grid-cols-1 items-start gap-4 @2xl:grid-cols-2">
											<DynamicField
												field={presentField(row.option, applicationData, values)}
												{values}
												{errors}
												{dataSources}
												{applicationData}
											/>
											{#if row.details.length}
												<div class="grid grid-cols-1 items-start gap-4 @2xl:grid-cols-12">
													{#each row.details as field (field.uid)}
														{@render answeredField(field)}
													{/each}
												</div>
											{/if}
										</div>
									{:else}
										<div class="grid grid-cols-1 items-start gap-4 @2xl:grid-cols-12">
											{#each row.fields as field (field.uid)}
												{@render answeredField(field)}
											{/each}
										</div>
									{/if}
								{/each}
							</div>
						</CardContent>
					{/if}
				</Card>
			</section>
		{:else}
			<p class="text-muted-foreground text-sm">This form has no visible fields.</p>
		{/each}

		{#if showSubmit && sections.length > 0}
			<div class="flex items-center justify-end gap-3">
				{#if submitted && errorCount > 0}
					<!-- A BUTTON, not a sentence. It says the same thing it did before and now takes you
					     there, which is the whole difficulty with a long form: the count was true and
					     useless. Pressing Save again does the same jump. -->
					<Button
						type="button"
						variant="ghost"
						class="text-destructive hover:text-destructive h-auto px-2 py-1 text-sm font-normal"
						onclick={() => void focusFirstError()}
					>
						{errorCount} field{errorCount === 1 ? '' : 's'} need attention — go to the first
					</Button>
				{/if}
				<Button type="submit">{submitLabel}</Button>
			</div>
		{/if}
	</form>
{/snippet}

{#if sectionNav && sections.length > 1 && wantsSectionNav(fields, sectionMeta)}
	<!-- The rail appears when this form is wide enough to spare a column, not when the window is. -->
	<div class="@container">
		<div class="grid gap-6 @4xl:grid-cols-[13rem_minmax(0,1fr)]">
			<FormSectionNav sections={sectionNavItems} />
			{@render formBody()}
		</div>
	</div>
{:else}
	{@render formBody()}
{/if}
