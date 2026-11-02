<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Button,
		EmptyState,
		Input,
		Label,
		PageHeader,
		SelectMenu,
		withToast
	} from '@graphene/ui';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const templateItems = $derived(
		data.templates.map((template) => ({ value: template.uid, label: template.name }))
	);
	const clientItems = $derived(
		data.clients.map((client) => ({
			value: String(client.contact_id),
			label: client.name ?? client.email ?? `Client ${client.contact_id}`
		}))
	);
</script>

<svelte:head><title>Generate document · {data.tenant.name}</title></svelte:head>
<PageHeader
	title="Generate document"
	description="Produce a document for one of this firm’s clients from a published template."
	back={{ href: `/tenants/${data.tenant.uid}/operate/documents`, label: 'Documents' }}
/>
{#if !data.templates.length}
	<!-- The body restated the title. What it owed the reader was WHY (a draft is not generable)
	     and where to go next. -->
	<EmptyState
		title="No published templates"
		description="This firm has no published templates. Publish a template before generating a document."
	>
		{#snippet action()}
			<Button variant="outline" href={`/tenants/${data.tenant.uid}/operate/templates`}>
				Back to templates
			</Button>
		{/snippet}
	</EmptyState>
{:else if data.writing}
	<form method="GET" class="mt-6 flex max-w-xl items-end gap-2">
		<div class="space-y-1.5">
			<Label for="template_uid">Template</Label>
			<SelectMenu
				id="template_uid"
				name="template"
				value={data.selectedTemplate}
				items={templateItems}
			/>
		</div>
		<Button type="submit" variant="outline">Use template</Button>
	</form>
	{#if data.selectedTemplate}
		<form
			method="POST"
			action="?/generate"
			use:enhance={withToast('Document generated')}
			class="mt-6 max-w-xl space-y-4"
		>
			<input type="hidden" name="template_uid" value={data.selectedTemplate} />
			<div class="space-y-1.5">
				<Label for="contact_id">Client</Label>
				<SelectMenu id="contact_id" name="contact_id" items={clientItems} />
			</div>
			<div class="space-y-1.5">
				<Label for="name">Document name</Label>
				<Input id="name" name="name" />
			</div>
			{#each data.variables as variable (variable)}
				<div class="space-y-1.5">
					<Label for={`answer-${variable}`}>{variable}</Label>
					<Input id={`answer-${variable}`} name={`answer:${variable}`} />
				</div>
			{/each}
			<Button type="submit">Generate</Button>
		</form>
	{/if}
{/if}
