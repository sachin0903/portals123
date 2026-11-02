<script lang="ts">
	// New pension illustration — the full-page shell. The drawer off the client record is the one an
	// adviser normally reaches; this stays for a deep link and for anyone who would rather have the
	// width. Everything the two share lives outside both: the field set and payload shaping in
	// `$lib/illustrations/form`, the client facts in `client-panel.svelte`, the call and its outcome
	// vocabulary in `$lib/illustrations/generate`.
	import { goto } from '$app/navigation';
	import { Button, DynamicForm, toast } from '@graphene/ui';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import ClientPanel from '$lib/illustrations/client-panel.svelte';
	import IllustrationOutcome from '$lib/illustrations/illustration-outcome.svelte';
	import ModelAssumptions from '$lib/illustrations/model-assumptions.svelte';
	import {
		existingSippTotal,
		type IllustrationClient,
		type SippAccountValue
	} from '$lib/illustrations/client';
	import {
		ILLUSTRATION_SECTIONS,
		illustrationAnswerProblems,
		illustrationFieldsFor,
		illustrationTerm,
		type IllustrationModel
	} from '$lib/illustrations/form';
	import {
		filedDocumentPath,
		generateIllustration,
		type GenerateResult
	} from '$lib/illustrations/generate';

	let {
		data
	}: {
		data: {
			models: IllustrationModel[];
			detail: {
				contact: IllustrationClient & { uid: string };
				accounts?: SippAccountValue[] | null;
			};
			clientName: string;
		};
	} = $props();

	const contactUid = $derived(data.detail.contact.uid);
	const client: IllustrationClient = $derived(data.detail.contact);
	const sippTotal = $derived(existingSippTotal(data.detail.accounts));
	const modelRows = $derived(
		data.models.map((m) => ({ id: String(m.id), name: m.name, target_return: m.target_return }))
	);

	let values = $state<Record<string, unknown>>({});
	let enabledSections = $state<Record<string, boolean>>({});
	let submitting = $state(false);
	let navigating = $state(false);
	let attempted = $state(false);
	let errors = $state<string[]>([]);
	let result = $state<GenerateResult | null>(null);
	let requestOpen = $state(false);
	let form = $state<ReturnType<typeof DynamicForm> | null>(null);
	const fields = $derived(illustrationFieldsFor(client));

	const chosenModel = $derived(
		data.models.find((m) => String(m.id) === String(values.model_id)) ?? null
	);

	const term = $derived(
		illustrationTerm(client, values.quotation_type, new Date(), values.retirement_age)
	);

	const problems = $derived.by(() => {
		const list: string[] = [];
		if (!data.models.length) list.push('This firm has no investment models configured.');
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

	const busy = $derived(submitting || navigating);

	async function generate() {
		if (busy) return;
		attempted = true;
		const fieldsOk = form?.validate() ?? false;
		if (problems.length || !fieldsOk) return;
		submitting = true;
		errors = [];
		result = null;
		requestOpen = false;
		const outcome = await generateIllustration(contactUid, form?.collect() ?? values, {
			existingSippTotal: sippTotal
		});
		if (!outcome.ok) {
			submitting = false;
			errors = outcome.errors;
			return;
		}
		const generated = outcome.result;
		if (!generated.stored) toast.error('The illustration was produced but not filed.');
		else if (generated.placeholder) toast.warning('Indicative illustration filed.');
		else toast.success('Illustration filed.');
		const href = filedDocumentPath(contactUid, generated);
		if (href) {
			navigating = true;
			try {
				await goto(href);
				return;
			} catch {
				navigating = false;
				errors = ['The illustration was filed, but its document could not be opened.'];
			}
		}
		result = generated;
		submitting = false;
		if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' });
	}
</script>

<svelte:head><title>New illustration · {data.clientName}</title></svelte:head>

<div
	class="mx-auto w-full max-w-3xl space-y-6"
	style="--form-scroll-offset: calc(var(--topbar-h, 4rem) + 1.5rem)"
>
	<div class="space-y-2">
		<Button
			variant="ghost"
			size="sm"
			href="/clients/{contactUid}/illustrations"
			class="text-muted-foreground -ml-2.5 h-8"
		>
			<ArrowLeftIcon class="mr-1.5 size-4" /> Back to illustrations
		</Button>
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">New illustration</h1>
			<p class="text-muted-foreground mt-1 text-sm">
				Projected for {data.clientName} on the assumptions below.
			</p>
		</div>
	</div>

	{#if result}
		<IllustrationOutcome {result} {contactUid} bind:requestOpen>
			{#snippet extraActions()}
				<Button variant="ghost" size="sm" href="/clients/{contactUid}/illustrations">
					Back to illustrations
				</Button>
			{/snippet}
		</IllustrationOutcome>
	{/if}

	<ClientPanel
		clientName={data.clientName}
		{client}
		existingSippTotal={sippTotal}
		termNote={term.note}
	/>

	<DynamicForm
		bind:this={form}
		{fields}
		bind:values
		dataSources={{ models: modelRows }}
		sectionMeta={ILLUSTRATION_SECTIONS}
		bind:enabledSections
		sectionNav
		showSubmit={false}
	/>

	<ModelAssumptions models={data.models} selected={values.model_id} />

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
		Morgan Lloyd provides the illustration. If a quotation cannot be produced, the filed document
		will be clearly marked as indicative.
	</div>

	<div class="flex justify-end gap-2 pb-2">
		<Button variant="ghost" href="/clients/{contactUid}/illustrations" disabled={busy}
			>Cancel</Button
		>
		<Button onclick={generate} disabled={busy}>
			<SparklesIcon class="mr-1.5 size-4" />
			{submitting ? 'Generating…' : navigating ? 'Opening…' : 'Generate illustration'}
		</Button>
	</div>
</div>
