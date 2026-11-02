<script lang="ts">
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		Button,
		DynamicForm,
		PageHeader,
		formDataSources,
		toast
	} from '@graphene/ui';
	import { attachStepAnswers, type StepAnswerSource } from '@graphene/ui/book/journeys';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	let values = $state<Record<string, unknown>>(untrack(() => ({ ...(data.form.answers ?? {}) })));
	const formIdentity = $derived(`${data.caseUid}:${data.stepKey}:${data.participant ?? ''}`);
	$effect(() => {
		void formIdentity;
		untrack(() => {
			values = { ...(data.form.answers ?? {}) };
		});
	});
	let formRef = $state<StepAnswerSource>();
	let submitting = $state(false);
	const back = $derived(`/tenants/${data.tenant.uid}/operate/journeys/${data.journeyUid}`);
</script>

<svelte:head><title>{data.form.name} · {data.tenant.name}</title></svelte:head>

<div class="mx-auto max-w-5xl">
	<PageHeader
		title={data.form.title ?? data.form.name}
		description="Answers are recorded in the target firm's journey."
		back={{ href: back, label: 'Journey' }}
	/>
	{#if form?.error}
		<Alert variant="destructive" class="mb-4">
			<AlertDescription>{form.error}</AlertDescription>
		</Alert>
	{/if}
	{#key formIdentity}
		<DynamicForm
			bind:this={formRef}
			fields={data.form.fields}
			bind:values
			dataSources={formDataSources}
			applicationData={data.form.context}
			showSubmit={false}
		/>
	{/key}
	{#if data.writing && data.form.status !== 'fulfilled'}
		<form
			method="POST"
			action={data.participant ? `?participant=${data.participant}&/submit` : '?/submit'}
			class="mt-4 flex justify-end gap-2"
			use:enhance={({ formData, cancel }) => {
				if (submitting || !attachStepAnswers(formData, formRef)) {
					cancel();
					return;
				}
				submitting = true;
				return async ({ result, update }) => {
					submitting = false;
					if (result.type === 'redirect') toast.success('Form completed');
					await update({ reset: false });
				};
			}}
		>
			<input type="hidden" name="participant" value={data.participant ?? ''} />
			<Button variant="outline" href={back}>Cancel</Button>
			<Button type="submit" disabled={submitting}>
				{submitting ? 'Submitting…' : 'Submit form'}
			</Button>
		</form>
	{/if}
</div>
