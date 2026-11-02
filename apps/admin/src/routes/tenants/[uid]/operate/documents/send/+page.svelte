<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button, Input, Label, MultiSelect, PageHeader, Textarea, withToast } from '@graphene/ui';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	let recipients = $state<string[]>([]);
	const options = $derived(
		data.clients.map((client) => ({
			value: String(client.contact_id),
			label: client.name ?? client.email ?? `Client ${client.contact_id}`
		}))
	);
</script>

<svelte:head><title>Send document · {data.tenant.name}</title></svelte:head>
<PageHeader
	title="Send document"
	description="Put a document in a client’s portal on this firm’s behalf."
	back={{ href: `/tenants/${data.tenant.uid}/operate/documents`, label: 'Documents' }}
/>
{#if data.writing}
	<form method="POST" use:enhance={withToast('Document sent')} class="mt-6 max-w-xl space-y-4">
		<div class="space-y-1.5">
			<Label for="document_uid">Document UID</Label>
			<Input id="document_uid" name="document_uid" value={data.documentUid} />
		</div>
		<div class="space-y-1.5">
			<Label for="contact_ids">Recipients</Label>
			<MultiSelect
				id="contact_ids"
				name="contact_ids"
				bind:value={recipients}
				{options}
				placeholder="Choose clients"
			/>
		</div>
		<div class="space-y-1.5">
			<Label for="message">Message</Label>
			<Textarea id="message" name="message" rows={4} />
		</div>
		<Button type="submit">Send</Button>
	</form>
{/if}
