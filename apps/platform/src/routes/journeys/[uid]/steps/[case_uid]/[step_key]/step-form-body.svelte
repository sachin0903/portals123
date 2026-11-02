<script lang="ts">
	import { untrack } from 'svelte';
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		Card,
		CardContent,
		DynamicForm,
		EmptyState,
		formDataSources,
		PageHeader,
		toast,
		wantsSectionNav
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import { toField } from '$lib/forms';
	import { attachStepAnswers, type StepAnswerSource } from '@graphene/ui/book/journeys';
	import type { ActionData } from './$types';
	import type { StepForm } from './+page.server';

	type StepBodyData = {
		form: StepForm;
		countries:
			| {
					code: string;
					name: string;
					adjective: string | null;
					inEea: boolean;
					identifiers: unknown;
			  }[]
			| null;
		models: { id: number; name: string; [key: string]: unknown }[];
		feePackages: { id: number; name: string; [key: string]: unknown }[];
		transferProviders: { id: number; name: string; [key: string]: unknown }[];
		currencies: {
			code: string;
			label: string;
			currency: string | null;
			symbol: string | null;
			is_base: boolean;
		}[];
		journeyUid: string;
		caseUid: string;
		participant: string | null;
		formContext: { subject: string; purpose: string; owner?: string | null } | null;
		previousForm: { href: string; title: string; status: string } | null;
		nextForm: { href: string; title: string; status: string } | null;
		clientGather?: boolean;
		canManage: boolean;
		canEditForm: boolean;
	};
	import {
		availableFeeIds,
		displayFees,
		feeChargeLabel,
		feesForModel,
		selectFees,
		selectableFeeOption,
		unionDisplayFeeIds
	} from './fee-packages';
	import { withBaseCurrency } from './currency';
	import { stepFormIdentity } from './form-identity';

	// `form` is the action's result. It MUST be read: without JavaScript the browser posts natively,
	// the action refuses the submission (nothing was recorded) and returns that reason here. Relying on
	// the `enhance` callback alone meant a native post re-rendered a blank form with no explanation at
	// all — the adviser saw their answers vanish and was told nothing.
	let { data, form }: { data: StepBodyData; form: ActionData } = $props();

	const fields = $derived(
		data.form.fields.map((raw) => {
			const field = toField(raw);
			if (field.fieldKey !== 'fee_package_ids') return field;
			return {
				...field,
				options: {
					...(typeof field.options === 'object' && field.options ? field.options : {}),
					source: 'fee_packages',
					valueKey: 'id',
					labelKey: 'label'
				}
			};
		})
	);
	// The database is authoritative for countries and their national-identifier rules. Keep the
	// committed reference list as an offline fallback so a temporary reference-read failure never
	// turns valid dropdowns into empty controls.
	function initialValues(): Record<string, unknown> {
		return withBaseCurrency(
			data.form.answers,
			fields.some((field) => field.fieldKey === 'currency'),
			data.currencies
		);
	}
	// Seed once from the load (a prior submission on a `review`-mode step); further edits are local.
	let values = $state<Record<string, unknown>>(untrack(initialValues));
	const formIdentity = $derived(
		stepFormIdentity(data.caseUid, data.form.step_key, data.participant)
	);
	$effect(() => {
		void formIdentity;
		untrack(() => {
			values = initialValues();
		});
	});
	const compatibleFees = $derived(feesForModel(data.feePackages, values.model_id));
	const appliedDisplayFees = $derived(displayFees(compatibleFees));
	const selectableFees = $derived(selectFees(compatibleFees).map(selectableFeeOption));
	const dataSources = $derived({
		...formDataSources,
		...(data.countries?.length ? { nationalities: data.countries, countries: data.countries } : {}),
		models: data.models,
		fee_packages: selectableFees,
		transfer_providers: data.transferProviders,
		currencies: data.currencies
	});
	let formRef = $state<StepAnswerSource>();
	let submitting = $state(false);
	let clientError = $state<string | null>(null);
	const error = $derived(clientError ?? form?.error ?? null);
	const illustrationSource = $derived(
		data.form.context?.illustration && typeof data.form.context.illustration === 'object'
			? (data.form.context.illustration as Record<string, unknown>)
			: null
	);

	// A model change can hide a fee which was selected moments earlier. Remove it from the answer as
	// well as the menu; otherwise an invisible id is submitted and the API can only refuse the form.
	// Display packages are applied outside the picker, so they must not live in this value.
	$effect(() => {
		const current = Array.isArray(values.fee_package_ids) ? values.fee_package_ids.map(String) : [];
		const available = availableFeeIds(current, selectableFees);
		if (
			current.length !== available.length ||
			current.some((id, index) => id !== available[index])
		) {
			values.fee_package_ids = available;
		}
	});

	const alreadyDone = $derived(data.form.status === 'fulfilled');
	const canEdit = $derived(data.canManage && data.form.editable);

	// A staff-assigned step can be anything from three questions to a whole fact-find, and the two
	// want different pages. `wantsSectionNav` is what tells them apart — the same rule DynamicForm
	// applies before it shows its section rail — so the page is wide exactly when the rail is there
	// to fill the extra column. It answers from the declared fields, not the answers, which keeps the
	// page's width from jumping about as the adviser fills the form in.
	const longForm = $derived(wantsSectionNav(fields));
</script>

{#snippet openFormBuilder()}
	<Button variant="outline" href="/forms/{data.form.form_uid}/edit">Open the form builder</Button>
{/snippet}

{#snippet appliedFeesLead()}
	<div class="rounded-md border p-3">
		<p class="text-xs font-medium">Applied fees</p>
		<p class="text-muted-foreground mb-3 text-xs">
			These charges apply to this account and cannot be removed.
		</p>
		<ul class="space-y-2">
			{#each appliedDisplayFees as fee (fee.id)}
				<li class="flex flex-wrap items-baseline justify-between gap-2 text-xs">
					<span>{fee.name ?? `Fee ${fee.id}`}</span>
					<span class="text-muted-foreground tabular-nums">{feeChargeLabel(fee) || '—'}</span>
				</li>
			{/each}
		</ul>
	</div>
{/snippet}

<svelte:head><title>{data.form.name} · Platform</title></svelte:head>

<!-- `--form-scroll-offset` is what keeps the rail and the scroll-to-section clear of the shell's
     sticky topbar; `--topbar-h` is the token SaaSShell publishes for exactly this (always consumed
     with the 4rem fallback). Without it a jumped-to heading lands underneath the bar. -->
<div
	class={['mx-auto w-full', longForm ? 'max-w-6xl' : 'max-w-3xl']}
	style="--form-scroll-offset: calc(var(--topbar-h, 4rem) + 1rem)"
>
	<PageHeader
		title={data.form.title ?? data.form.name}
		eyebrow={data.formContext ? `For ${data.formContext.subject}` : undefined}
		description={data.formContext?.purpose ??
			'Answers flow into the relevant client or account record.'}
		back={{ href: `/journeys/${data.journeyUid}`, label: 'Back to journey' }}
	/>

	{#if data.formContext}
		<Card glass size="sm" class="mb-4">
			<CardContent class="flex flex-wrap items-start justify-between gap-3">
				<div>
					<p class="text-muted-foreground text-xs">This form is for</p>
					<p class="text-lg font-semibold">{data.formContext.subject}</p>
					{#if data.formContext.owner}
						<p class="text-muted-foreground mt-1 text-sm">
							Assigned to {data.formContext.owner}
						</p>
					{/if}
				</div>
				<p class="text-muted-foreground max-w-md text-sm">{data.formContext.purpose}</p>
			</CardContent>
		</Card>
	{/if}

	{#if data.clientGather}
		<Alert class="mb-4">
			<AlertTitle>Client's form</AlertTitle>
			<AlertDescription>This is the client's form. You may complete it for them.</AlertDescription>
		</Alert>
	{/if}

	{#if illustrationSource}
		<Alert class="mb-4">
			<AlertTitle>Prefilled from the selected SIPP illustration</AlertTitle>
			<AlertDescription>
				{String(
					illustrationSource.name ??
						illustrationSource.output_reference ??
						'The selected illustration'
				)}
				supplied the matching defaults below. Review every answer before saving.
			</AlertDescription>
		</Alert>
	{/if}

	{#if error}
		<Alert variant="destructive" class="mb-4">
			<AlertTitle>Could not submit the form</AlertTitle>
			<AlertDescription>{error}</AlertDescription>
		</Alert>
	{/if}

	{#if alreadyDone}
		<Alert class="mb-4">
			<AlertTitle>{data.form.editable ? 'Completed; amendments allowed' : 'Completed'}</AlertTitle>
			<AlertDescription>
				{data.form.editable
					? 'These are the recorded answers. You can amend them until the application is declared and sent to the client.'
					: 'This application has been sent to the client, so these answers are now read-only.'}
			</AlertDescription>
		</Alert>
	{/if}

	{#if !fields.length}
		<Card glass class="overflow-visible">
			<CardContent>
				<!-- It named the builder and left the reader to go and find it. The form's own uid is
				     already on the payload, so the route is known. Gated on `canManage`: somebody who
				     cannot edit the form is shown the fact, not a door they will be refused at.
				     The full stop went with the sentence becoming a title. -->
				<EmptyState
					icon={FileTextIcon}
					title="This form has no questions yet"
					description="Nothing can be answered here until fields are added to {data.form.name}."
					action={data.canEditForm ? openFormBuilder : undefined}
				/>
			</CardContent>
		</Card>
	{:else}
		<!-- No Card around the form. DynamicForm already renders each section in its own Card, so a
		     wrapper here nested a card inside a card — invisible on a one-section form and a box
		     inside a box on every section of a long one.
		     Overflow behaviour is unchanged and still matters: AddressLookup's suggestion list is
		     intentionally inline (a portalled popover falls outside Sheet/Dialog focus traps), so it
		     is clipped by any ancestor that hides overflow — `z-50` cannot save it. DynamicForm's
		     section Cards carry `overflow-visible` themselves, and nothing here reintroduces a clip. -->
		<fieldset disabled={!canEdit} class:opacity-80={!canEdit}>
			{#key formIdentity}
				<DynamicForm
					bind:this={formRef}
					{fields}
					bind:values
					{dataSources}
					applicationData={data.form.context}
					showSubmit={false}
					sectionNav
					sectionMeta={{
						Fees: {
							lead: appliedDisplayFees.length ? appliedFeesLead : undefined
						}
					}}
				/>
			{/key}
		</fieldset>
	{/if}

	{#if fields.length && canEdit}
		<!-- The participant rides in the action URL as well as the body: a native post replaces the
			 query string with the action key, and without it the re-rendered page would load a
			 participant-less step (a 404 on any fanned step) instead of the one just submitted. -->
		<form
			method="POST"
			action={data.participant ? `?participant=${data.participant}&/submit` : '?/submit'}
			class="mt-4 flex flex-wrap items-center justify-between gap-2"
			use:enhance={({ formData, cancel, submitter }) => {
				// The answers are written into the body HERE, in the synchronous submit listener, so
				// there is no state → DOM ordering to lose them to (see $lib/flows.ts).
				const saving = submitter instanceof HTMLButtonElement && submitter.value === 'save';
				if (saving) {
					if (submitting || !formRef) {
						cancel();
						return;
					}
					const answers = formRef.collect();
					answers.fee_package_ids = unionDisplayFeeIds(answers.fee_package_ids, appliedDisplayFees);
					formData.set('answers', JSON.stringify(answers));
					submitting = true;
					clientError = null;
					return async ({ result, update }) => {
						submitting = false;
						if (result.type === 'success') toast.success('Progress saved');
						else if (result.type === 'failure') {
							clientError = String(result.data?.error ?? 'Something went wrong.');
						}
						await update({ reset: false });
					};
				}
				if (submitting || !attachStepAnswers(formData, formRef)) {
					if (!submitting) toast.error('Some answers need fixing before this can be submitted.');
					cancel();
					return;
				}
				const raw = formData.get('answers');
				if (typeof raw === 'string') {
					try {
						const answers = JSON.parse(raw) as Record<string, unknown>;
						answers.fee_package_ids = unionDisplayFeeIds(
							answers.fee_package_ids,
							appliedDisplayFees
						);
						formData.set('answers', JSON.stringify(answers));
					} catch {
						/* the API will refuse a malformed payload */
					}
				}
				submitting = true;
				clientError = null;
				return async ({ result, update }) => {
					submitting = false;
					if (result.type === 'redirect') {
						toast.success('Form completed');
						await update();
					} else if (result.type === 'failure') {
						clientError = String(result.data?.error ?? 'Something went wrong.');
						await update({ reset: false });
					} else {
						await update();
					}
				};
			}}
		>
			<input type="hidden" name="participant" value={data.participant ?? ''} />
			<input type="hidden" name="continue_to" value={data.nextForm?.href ?? ''} />
			<div>
				{#if data.previousForm}
					<Button variant="outline" href={data.previousForm.href}>
						Previous · {data.previousForm.title}
					</Button>
				{/if}
			</div>
			<div class="flex gap-2">
				<Button variant="outline" href={`/journeys/${data.journeyUid}`}>Back to journey</Button>
				{#if !alreadyDone}
					<Button
						type="submit"
						variant="outline"
						name="intent"
						value="save"
						formaction={data.participant ? `?participant=${data.participant}&/save` : '?/save'}
						disabled={submitting}
					>
						Save progress
					</Button>
				{/if}
				<Button type="submit" disabled={submitting}>
					{submitting
						? 'Saving…'
						: data.nextForm
							? alreadyDone
								? 'Save changes and continue'
								: 'Save and continue'
							: alreadyDone
								? 'Save changes'
								: 'Save form'}
				</Button>
			</div>
		</form>
	{:else if fields.length && data.previousForm}
		<div class="mt-4 flex justify-start">
			<Button variant="outline" href={data.previousForm.href}>
				Previous · {data.previousForm.title}
			</Button>
		</div>
	{/if}
</div>
