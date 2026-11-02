<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Button,
		Card,
		EmptyState,
		humanise,
		Input,
		LoadErrorBanner,
		PageHeader,
		Sheet
	} from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let open = $state(false);
</script>

<PageHeader title="Terms" description="Declarations and documents this firm's clients must accept.">
	{#snippet actions()}
		{#if data.canManage}
			<Button onclick={() => (open = true)}>New</Button>
		{/if}
	{/snippet}
</PageHeader>

{#if data.loadError}<LoadErrorBanner />{/if}

<Card glass class="divide-y py-0">
	{#each data.items as item (item.uid)}
		<a
			class="hover:bg-muted/20 block px-4 py-3"
			href={`/tenants/${data.tenantUid}/operate/terms/${item.uid}`}
		>
			<strong>{item.title}</strong>
			<div class="text-muted-foreground text-xs">
				{item.code} · {humanise(item.status)}
				{#if item.current_version_number}
					· v{item.current_version_number}
				{/if}
			</div>
		</a>
	{:else}
		<EmptyState
			title="No terms yet"
			description="Publish the declarations and PDFs clients must accept before an account opens."
		/>
	{/each}
</Card>

<Sheet.Root bind:open>
	<Sheet.Content>
		<Sheet.Header><Sheet.Title>New terms</Sheet.Title></Sheet.Header>
		<form method="POST" action="?/create" class="mt-4 space-y-4" use:enhance>
			<Input name="title" placeholder="Title" required />
			<Button type="submit">Create</Button>
		</form>
	</Sheet.Content>
</Sheet.Root>
