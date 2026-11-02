<script lang="ts">
	// GIA / ISA illustration — the same sectioned form as a pension illustration, without the
	// client age, retirement age or marital status that a SIPP projection needs.
	import { goto } from '$app/navigation';
	import { Button, DynamicForm, toast } from '@graphene/ui';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import type { IllustrationModel } from '$lib/illustrations/form';
	import {
		WRAPPER_SECTIONS,
		toWrapperIllustrationPayload,
		wrapperIllustrationFields,
		type PlatformFeeChoice,
		type WrapperKind
	} from '$lib/illustrations/wrapper-form';

	let {
		data,
		wrapper,
		available
	}: {
		data: {
			models: IllustrationModel[];
			detail: { contact: { uid: string } };
			clientName: string;
			platformFees: PlatformFeeChoice[];
			missingDisclosure: string[];
		};
		wrapper: WrapperKind;
		available: boolean;
	} = $props();

	const contactUid = $derived(data.detail.contact.uid);
	const modelRows = $derived(
		data.models.map((m) => ({ id: String(m.id), name: m.name, target_return: m.target_return }))
	);
	const feeRows = $derived(
		data.platformFees.map((fee) => ({
			id: String(fee.id),
			name: `${fee.name} · ${fee.summary}`
		}))
	);
	const chooseFee = $derived(data.platformFees.length > 1);
	const soleFee = $derived(data.platformFees.length === 1 ? data.platformFees[0] : null);
	const fields = $derived(wrapperIllustrationFields(wrapper, chooseFee));

	let values = $state<Record<string, unknown>>({});
	let enabledSections = $state<Record<string, boolean>>({});
	let form = $state<ReturnType<typeof DynamicForm> | null>(null);
	let submitting = $state(false);
	let attempted = $state(false);
	let errors = $state<string[]>([]);

	const chosenModel = $derived(
		data.models.find((m) => String(m.id) === String(values.model_id)) ?? null
	);
	const sectionOn = (title: string) =>
		enabledSections[title] ?? WRAPPER_SECTIONS[title]?.defaultOn ?? false;

	const setup = $derived.by(() => {
		const list: string[] = [];
		if (data.missingDisclosure.length) {
			list.push(
				`This firm’s illustration disclosure is incomplete: ${data.missingDisclosure.join(', ')}.`
			);
		}
		if (!data.platformFees.length) {
			list.push('This firm has no platform fee package.');
		}
		return list;
	});

	const problems = $derived.by(() => {
		const list: string[] = [];
		if (!data.models.length) list.push('This firm has no investment models configured.');
		else if (!values.model_id)
			list.push('Choose a model. Its target return drives the projection.');
		else if (chosenModel && (chosenModel.target_return ?? null) === null)
			list.push(
				`“${chosenModel.name}” has no target return set, so it cannot be illustrated against.`
			);
		const balance = Number(String(values.existing_balance ?? '').trim() || '0');
		const payingIn =
			(sectionOn('Single contribution') && String(values.single_amount ?? '').trim() !== '') ||
			(sectionOn('Regular contribution') && String(values.regular_amount ?? '').trim() !== '');
		if (!(balance > 0) && !payingIn)
			list.push('Enter an existing balance, or switch on a contribution.');
		return list;
	});

	const rateLabel = $derived.by(() => {
		const raw = chosenModel?.target_return;
		if (raw === null || raw === undefined || raw === '') return null;
		const n = Number(raw);
		if (!Number.isFinite(n)) return null;
		return `${Number(n.toFixed(4))}%`;
	});

	async function generate() {
		if (submitting || setup.length) return;
		attempted = true;
		if (form && !form.validate()) return;
		if (problems.length) return;
		submitting = true;
		errors = [];
		const body = toWrapperIllustrationPayload(
			form?.collect() ?? values,
			wrapper,
			soleFee?.id ?? null
		);
		const res = await fetch(`/clients/${contactUid}/illustrations/investment`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(body)
		});
		if (!res.ok) {
			submitting = false;
			const payload = (await res.json().catch(() => null)) as { error?: string } | null;
			errors = [payload?.error ?? 'The illustration could not be produced.'];
			return;
		}
		toast.success('Illustration filed.');
		await goto(`/clients/${contactUid}/illustrations`);
	}
</script>

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
				{wrapper === 'ISA' ? 'An' : 'A'}
				{wrapper} illustration for {data.clientName}, on the assumptions below.
			</p>
		</div>
	</div>

	{#if !available}
		<p class="text-muted-foreground text-sm">
			{wrapper} illustrations are switched off for this firm. SIPP illustrations are unaffected.
		</p>
	{:else}
		<DynamicForm
			bind:this={form}
			{fields}
			bind:values
			dataSources={{ models: modelRows, fees: feeRows }}
			sectionMeta={WRAPPER_SECTIONS}
			bind:enabledSections
			sectionNav
			showSubmit={false}
		/>

		{#if chosenModel && rateLabel}
			<p class="text-muted-foreground text-xs">
				<strong class="text-foreground font-medium">{chosenModel.name}</strong>
				has a target return of {rateLabel} a year. Mid growth is that rate less 2 percentage points, with
				low and high 3 points either side.
				{#if soleFee}
					The platform charge is {soleFee.name} · {soleFee.summary}.
				{/if}
			</p>
		{/if}

		{#if setup.length || (attempted && problems.length)}
			<div class="border-destructive/40 bg-destructive/5 space-y-1 rounded-md border p-3">
				<p class="text-destructive text-sm font-medium">Before this can be generated</p>
				<ul class="text-destructive list-inside list-disc text-sm">
					{#each setup as p (p)}<li>{p}</li>{/each}
					{#if attempted}
						{#each problems as p (p)}<li>{p}</li>{/each}
					{/if}
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
			Calculated here from the model’s target return and this firm’s platform fee. The PDF is filed
			against {data.clientName} once it has been produced.
		</div>

		<div class="flex justify-end gap-2 pb-2">
			<Button variant="ghost" href="/clients/{contactUid}/illustrations" disabled={submitting}>
				Cancel
			</Button>
			<Button onclick={generate} disabled={submitting || setup.length > 0}>
				<SparklesIcon class="mr-1.5 size-4" />
				{submitting ? 'Generating…' : 'Generate illustration'}
			</Button>
		</div>
	{/if}
</div>
