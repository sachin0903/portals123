<script lang="ts">
	import { Button, EmptyState, toast, type FormField, type FormScoring } from '@graphene/ui';
	import { FormBuilder, toFieldPayload } from '@graphene/ui/form-builder';
	import { deserialize } from '$app/forms';
	import { page } from '$app/state';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let fields = $state<FormField[] | null>(null);
	let scoring = $state<FormScoring | null | undefined>(undefined);
	let dirty = $state(false);
	const detail = $derived(`/tenants/${page.params.uid}/operate/forms/${page.params.formUid}`);
	async function save() {
		if (!data.canManage || !data.form) return;
		if (!fields && scoring === undefined) return;
		const body = new FormData();
		body.set('fields', JSON.stringify((fields ?? data.fields).map(toFieldPayload)));
		if (scoring !== undefined) body.set('scoring', JSON.stringify(scoring));
		const response = await fetch('?/saveFields', {
			method: 'POST',
			body,
			headers: { 'x-sveltekit-action': 'true' }
		});
		const result = deserialize(await response.text());
		if (result.type === 'success') {
			dirty = false;
			toast.success('Draft saved.');
		} else toast.error('Could not save the form.');
	}
</script>

{#if !data.form}<EmptyState
		title="Form not found"
		description="It may have been deleted, or the link may be out of date."
	>
		{#snippet action()}
			<Button variant="outline" size="sm" href={`/tenants/${page.params.uid}/operate/forms`}
				>Back to forms</Button
			>
		{/snippet}
	</EmptyState>
{:else}<div class="space-y-5">
		<div class="flex items-center justify-between">
			<div>
				<Button variant="ghost" href={detail}>← Form</Button>
				<h1 class="text-2xl font-semibold">{data.form.name}</h1>
			</div>
			{#if data.canManage}<Button onclick={save} disabled={!dirty}>Save draft</Button>{/if}
		</div>
		<FormBuilder
			fields={data.fields}
			scoring={data.form.scoring ?? null}
			onchange={(next) => {
				fields = next;
				dirty = true;
			}}
			onscoring={(next) => {
				scoring = next;
				dirty = true;
			}}
		/>
	</div>{/if}
