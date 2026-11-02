<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Button,
		Card,
		CardContent,
		Input,
		Label,
		PageHeader,
		SelectMenu,
		withToast
	} from '@graphene/ui';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<svelte:head><title>Document types · {data.tenant.name}</title></svelte:head>
<div class="space-y-5">
	<PageHeader
		title="Document types"
		description="The categories this firm files its documents under."
		back={{ href: `/tenants/${data.tenant.uid}/operate/documents`, label: 'Documents' }}
	/>
	{#if data.writing}
		<Card glass>
			<CardContent>
				<form
					method="POST"
					action="?/create"
					use:enhance={withToast('Type created')}
					class="flex flex-wrap items-end gap-2"
				>
					<div class="space-y-1.5">
						<Label for="slug">Slug</Label><Input id="slug" name="slug" />
					</div>
					<div class="space-y-1.5">
						<Label for="label">Label</Label><Input id="label" name="label" />
					</div>
					<SelectMenu
						name="kind"
						value="document"
						items={[
							{ value: 'document', label: 'Document' },
							{ value: 'file', label: 'Supporting file' }
						]}
					/>
					<Button type="submit">Add type</Button>
				</form>
			</CardContent>
		</Card>
	{/if}
	<div class="space-y-2">
		{#each data.types as type (type.uid)}
			<Card glass>
				<CardContent>
					<form
						method="POST"
						action="?/update"
						use:enhance={withToast('Type updated')}
						class="flex items-center gap-2"
					>
						<input type="hidden" name="uid" value={type.uid} />
						<Input name="label" value={type.label} disabled={!data.writing} />
						<span class="text-muted-foreground text-sm">{type.slug} · {type.kind}</span>
						<input type="hidden" name="archived" value={String(!type.archived)} />
						{#if data.writing}
							<Button type="submit" variant="outline" size="sm">
								{type.archived ? 'Restore' : 'Archive'}
							</Button>
						{/if}
					</form>
				</CardContent>
			</Card>
		{/each}
	</div>
</div>
