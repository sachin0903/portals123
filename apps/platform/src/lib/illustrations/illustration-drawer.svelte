<script lang="ts">
	// The pension-illustration form, in a side drawer off the client record.
	//
	// The only shell there is: the record's Illustrations section — a listing plus a full-page
	// composer — was retired, since a filed illustration is a document and reads from Documents with
	// the client's other paperwork. The adviser reaches this from the record's action row, and the
	// field definition stays in `$lib/illustrations/form` so it is testable without the drawer.
	//
	// On a filed generate the document opens: the PDF is the outcome, not a preview stacked on the
	// form that produced it. Generate stays disabled from the click until that navigation, because
	// the engine is paid and a double-submit is a second quotation. The sheet itself is locked for
	// the same reason: Escape, an overlay click, and the header X would otherwise close it while
	// the engine is still running, `reset` would clear `submitting`, and Generate would be live
	// again against a request already in flight.
	//
	// Do not refresh this page (`ongenerated` → `invalidateAll`) before `goto`. Invalidating the
	// client record races the document navigation and wins: the drawer closes (open resets) and the
	// adviser stays on the record. Navigation itself unmounts the drawer.
	//
	// It keeps DynamicForm's section rail, because this form is long: six sections, and an adviser
	// filling in Annuity basis needs to see that Contributions is switched off without scrolling
	// back. Transfer, Contributions and Income start switched off, so the drawer opens as a short
	// form — plan, charges, annuity — that grows only where the adviser opts in.
	import { goto } from '$app/navigation';
	import { tick } from 'svelte';
	import { Button, DynamicForm, Sheet, toast } from '@graphene/ui';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import ClientPanel from '$lib/illustrations/client-panel.svelte';
	import IllustrationOutcome from '$lib/illustrations/illustration-outcome.svelte';
	import ModelAssumptions from '$lib/illustrations/model-assumptions.svelte';
	import type { IllustrationClient } from '$lib/illustrations/client';
	import {
		ILLUSTRATION_SECTIONS,
		illustrationAnswerProblems,
		illustrationFieldsFor,
		illustrationTerm,
		retirementAgeWrittenBack,
		type IllustrationModel
	} from '$lib/illustrations/form';
	import {
		filedDocumentPath,
		generateIllustration,
		type GenerateResult
	} from '$lib/illustrations/generate';

	let {
		open = $bindable(false),
		contactUid,
		clientName,
		/** Read from the record and shown as text — these are the values that go into the request.
		    A retirement age or marital status the record does not hold yet is a field on the form,
		    written back when the illustration is generated, and never an edit of one already set. */
		client,
		/** The firm's models. `target_return` is the growth rate the whole projection is built on, and
		    `model_charge` is the annual charge it is run net of. */
		models = [],
		/** Sum of the client's other SIPP accounts. Null hides the line under the client details. */
		existingSippTotal = null,
		/** Kickoff keeps the adviser in-place so it can immediately select the new run. */
		navigateToDocument = true,
		onfilled,
		ongenerated
	}: {
		open?: boolean;
		/** The client's uid — the generate POST goes to `/clients/[uid]/illustrations`. */
		contactUid: string;
		clientName: string;
		client: IllustrationClient;
		models?: IllustrationModel[];
		existingSippTotal?: number | null;
		navigateToDocument?: boolean;
		/** The facts this illustration wrote onto a record that was missing them, so a host holding
		    its own copy of the client (the kickoff wizard) can stop asking. */
		onfilled?: (facts: { retirement_age?: number; marital_status?: string }) => void;
		/** Fired after a successful generate so the host can refresh its data. */
		ongenerated?: (result: GenerateResult) => void;
	} = $props();

	// Ids are strings, as the <select> stores them.
	const modelRows = $derived(
		models.map((m) => ({ id: String(m.id), name: m.name, target_return: m.target_return }))
	);

	let values = $state<Record<string, unknown>>({});
	let enabledSections = $state<Record<string, boolean>>({});
	let submitting = $state(false);
	/** True once we have a filed PDF and are leaving for it. Stops reset-on-close from re-enabling
	    Generate during the sheet's exit animation, which is a second click on a paid engine. */
	let navigating = $state(false);
	let attempted = $state(false);
	let errors = $state<string[]>([]);
	let result = $state<GenerateResult | null>(null);
	let requestOpen = $state(false);
	let form = $state<ReturnType<typeof DynamicForm> | null>(null);
	const busy = $derived(submitting || navigating);
	/** Escape / overlay / header X must not dismiss while the engine is running. Unlock once we
	    are leaving for the filed PDF, or our own `open = false` would be refused. */
	const dismissLocked = $derived(submitting && !navigating);

	/** A model with no target return cannot be illustrated against — CTC builds the projection from
	    it. Said before submitting rather than met as a 422 afterwards. Declared after `values`,
	    which it reads. */
	const chosenModel = $derived(
		models.find((m) => String(m.id) === String(values.model_id)) ?? null
	);

	const term = $derived(
		illustrationTerm(client, values.quotation_type, new Date(), values.retirement_age)
	);

	/** Everything standing between the adviser and a valid request. Shown only after Generate is
	    pressed — listing the empty form's gaps on first open reads as an error the adviser has
	    already made. */
	const problems = $derived.by(() => {
		const list: string[] = [];
		if (!models.length) list.push('This firm has no investment models configured.');
		else if (!values.model_id)
			list.push('Choose a model. Its target return drives the projection.');
		else if (chosenModel && (chosenModel.target_return ?? null) === null)
			list.push(
				`“${chosenModel.name}” has no target return set, so it cannot be illustrated against.`
			);
		if (!client.dob)
			list.push('A projection is measured from date of birth, and none is recorded.');
		list.push(
			...illustrationAnswerProblems(values, { transferOn: enabledSections.Transfer ?? false })
		);
		if (term.problem) list.push(term.problem);
		return list;
	});

	const fields = $derived(illustrationFieldsFor(client));

	async function generate() {
		if (busy) return;
		attempted = true;
		// Retirement age is always required, and a missing marital status is too, so this stops a
		// generate that has not answered them — and focuses the field.
		const fieldsOk = form?.validate() ?? false;
		if (problems.length || !fieldsOk) return;
		submitting = true;
		errors = [];
		result = null;
		requestOpen = false;
		const collected = form?.collect() ?? values;
		const outcome = await generateIllustration(contactUid, collected, { existingSippTotal });
		if (!outcome.ok) {
			submitting = false;
			errors = outcome.errors;
			return;
		}
		const generated = outcome.result;
		const filled: { retirement_age?: number; marital_status?: string } = {};
		// Only what the record did not already hold. A quote of 60 against a stored 65 is this
		// illustration, not a new fact for kickoff to copy onto the party.
		const writtenAge = retirementAgeWrittenBack(client.retirement_age, collected.retirement_age);
		if (writtenAge != null) filled.retirement_age = writtenAge;
		if (typeof collected.marital_status === 'string' && collected.marital_status.trim())
			filled.marital_status = collected.marital_status.trim();
		if (filled.retirement_age != null || filled.marital_status) onfilled?.(filled);
		if (!generated.stored) toast.error('The illustration was produced but not filed.');
		else if (generated.placeholder) toast.warning('Indicative illustration filed.');
		else toast.success('Illustration filed.');
		const href = filedDocumentPath(contactUid, generated);
		if (href && navigateToDocument) {
			navigating = true;
			open = false;
			await tick();
			try {
				await goto(href);
				return;
			} catch {
				// The PDF is safely filed even if client-side navigation fails. Re-open on the
				// outcome so the adviser has its direct document link rather than a stuck
				// “Opening…” button.
				navigating = false;
				open = true;
				errors = ['The illustration was filed, but its document could not be opened.'];
			}
		}
		ongenerated?.(generated);
		result = generated;
		submitting = false;
	}

	function reset() {
		if (busy) return;
		values = {};
		enabledSections = {};
		attempted = false;
		errors = [];
		result = null;
		requestOpen = false;
		submitting = false;
	}
</script>

<Sheet.Root
	bind:open
	onOpenChange={(next) => {
		// Close is disabled={busy}, but Escape, overlay click, and the header X are not.
		// bits-ui 'ignore' stops those dismissals; refusing here is the fallback if one
		// still gets through. Use dismissLocked, not busy: after a filed generate we set
		// navigating then open = false, and busy would fight that close.
		if (!next && dismissLocked) {
			open = true;
			return;
		}
		if (!next) reset();
	}}
>
	<Sheet.Content
		class="flex w-full flex-col gap-0 overflow-hidden p-0"
		style="--form-scroll-offset: 1rem"
		showCloseButton={!dismissLocked}
		escapeKeydownBehavior={dismissLocked ? 'ignore' : 'close'}
		interactOutsideBehavior={dismissLocked ? 'ignore' : 'close'}
	>
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title>Pension illustration</Sheet.Title>
			<Sheet.Description>Projected for {clientName} on the assumptions below.</Sheet.Description>
		</Sheet.Header>

		<div class="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5 [--form-nav-bleed:1.5rem]">
			{#if result}
				<IllustrationOutcome {result} {contactUid} bind:requestOpen />
			{/if}

			<ClientPanel {clientName} {client} {existingSippTotal} termNote={term.note} />

			<DynamicForm
				bind:this={form}
				{fields}
				bind:values
				bind:enabledSections
				dataSources={{ models: modelRows }}
				sectionMeta={ILLUSTRATION_SECTIONS}
				sectionNav
				showSubmit={false}
			/>

			<ModelAssumptions {models} selected={values.model_id} />

			{#if attempted && problems.length}
				<div class="border-destructive/40 bg-destructive/5 space-y-1 rounded-md border p-3">
					<p class="text-destructive text-sm font-medium">Before this can be generated</p>
					<ul class="text-destructive list-inside list-disc text-sm">
						{#each problems as p (p)}<li>{p}</li>{/each}
					</ul>
				</div>
			{/if}

			{#if errors.length}
				<div class="border-destructive/40 bg-destructive/5 space-y-1 rounded-md border p-3">
					<p class="text-destructive text-sm font-medium">This request could not be sent</p>
					<ul class="text-destructive list-inside list-disc text-sm">
						{#each errors as e (e)}<li>{e}</li>{/each}
					</ul>
				</div>
			{/if}

			<div class="bg-info-subtle border-info-line text-info-strong rounded-md border p-3 text-sm">
				Morgan Lloyd provides the illustration. If a quotation cannot be produced, the filed
				document will be clearly marked as indicative.
			</div>
		</div>

		<Sheet.Footer class="flex-row items-center justify-end gap-2 border-t">
			<Button variant="ghost" onclick={() => (open = false)} disabled={busy}>Close</Button>
			<Button onclick={generate} disabled={busy}>
				<SparklesIcon class="mr-1.5 size-4" />
				{submitting ? 'Generating…' : navigating ? 'Opening…' : 'Generate illustration'}
			</Button>
		</Sheet.Footer>
	</Sheet.Content>
</Sheet.Root>
