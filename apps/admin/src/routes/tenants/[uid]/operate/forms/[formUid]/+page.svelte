<script lang="ts">
	import {
		Alert,
		Button,
		Card,
		DynamicForm,
		EmptyState,
		humanise,
		StatusBadge,
		toast
	} from '@graphene/ui';
	import { deserialize, enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let values = $state<Record<string, unknown>>({});
	const base = $derived(`/tenants/${page.params.uid}/operate/forms`);
	async function submit(answers: Record<string, unknown>) {
		const body = new FormData();
		body.set('answers', JSON.stringify(answers));
		const response = await fetch('?/submitResponse', {
			method: 'POST',
			body,
			headers: { 'x-sveltekit-action': 'true' }
		});
		const result = deserialize(await response.text());
		if (result.type === 'success') {
			toast.success('Response recorded.');
			await invalidateAll();
		} else toast.error('Could not record the response.');
	}
</script>

{#if !data.form}
	<EmptyState
		title="Form not found"
		description="It may have been deleted, or the link may be out of date."
	>
		{#snippet action()}
			<Button variant="outline" size="sm" href={`/tenants/${page.params.uid}/operate/forms`}
				>Back to forms</Button
			>
		{/snippet}
	</EmptyState>
{:else}
	<div class="space-y-5">
		<Button variant="ghost" href={base}>← Forms</Button>
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div>
				<div class="flex items-center gap-2">
					<h1 class="text-2xl font-semibold">{data.form.name}</h1>
					<StatusBadge tone={data.form.status === 'published' ? 'success' : 'warning'}
						>{humanise(data.form.status)}</StatusBadge
					>
				</div>
				<p class="text-muted-foreground font-mono text-sm">{data.form.code}</p>
			</div>
			{#if data.canManage}<div class="flex gap-2">
					{#if data.form.status === 'draft'}<form method="POST" action="?/publish" use:enhance>
							<Button>Publish</Button>
						</form>{/if}
					{#if data.form.status === 'archived'}<form method="POST" action="?/unarchive" use:enhance>
							<Button variant="outline">Restore</Button>
						</form>
					{:else}<Button href={`${base}/${data.form.uid}/edit`}>Edit fields</Button>
						<form
							method="POST"
							action="?/archive"
							use:enhance={() =>
								async ({ result }) => {
									if (result.type === 'success') goto(base);
								}}
						>
							<Button variant="ghost">Archive</Button>
						</form>{/if}
				</div>{/if}
		</div>
		{#if data.fields.length}<Card class="p-5"
				><DynamicForm
					fields={data.fields}
					bind:values
					showSubmit={data.canSubmit}
					onsubmit={data.canSubmit ? submit : undefined}
				/></Card
			>
		{:else}<EmptyState
				title="This form has no fields yet"
				description="Nothing here for a client to answer yet. Edit fields, above, opens the builder for this form."
			/>{/if}
		{#if data.canReadResponses}<section class="space-y-2">
				<h2 class="font-semibold">Responses ({data.responses.length})</h2>
				{#each data.responses as response (response.uid)}<Card class="p-4"
						><p class="text-muted-foreground text-xs">{response.submittedAt} · {response.via}</p>
						<pre class="mt-2 overflow-auto text-xs">{JSON.stringify(
								response.answers,
								null,
								2
							)}</pre></Card
					>{/each}
			</section>
		{:else}<Alert>Responses require the response-reading capability.</Alert>{/if}
	</div>
{/if}
