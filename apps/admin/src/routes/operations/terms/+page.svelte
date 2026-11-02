<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		Button,
		EmptyState,
		FORM_STATUS_META,
		Input,
		Label,
		LoadErrorBanner,
		PageHeader,
		SelectMenu,
		Sheet,
		StatusBadge,
		Table
	} from '@graphene/ui';
	import type { PageData } from './$types';
	import FileSignatureIcon from '@lucide/svelte/icons/file-signature';
	import PlusIcon from '@lucide/svelte/icons/plus';

	let { data }: { data: PageData } = $props();
	let createOpen = $state(false);
	let title = $state('');
	let audience = $state('all');
	let query = $state('');

	$effect(() => {
		query = data.q ?? '';
	});

	function search() {
		const params = new URLSearchParams(page.url.searchParams);
		if (query.trim()) params.set('q', query.trim());
		else params.delete('q');
		const qs = params.toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

{#snippet createFirst()}
	<Button size="sm" onclick={() => (createOpen = true)}>New terms</Button>
{/snippet}

<svelte:head><title>Terms · Operations</title></svelte:head>

<PageHeader
	title="Terms"
	description="Graphene privacy and service terms that staff and clients accept."
>
	{#snippet actions()}
		{#if data.canManage}
			<Button onclick={() => (createOpen = true)}>
				<PlusIcon class="size-4" />
				New
			</Button>
		{/if}
	{/snippet}
</PageHeader>

{#if data.loadError}
	<div class="mt-6">
		<LoadErrorBanner onRetry={() => invalidateAll()} />
	</div>
{/if}

<div class="mt-6 space-y-4">
	<form
		class="flex gap-2"
		onsubmit={(e) => {
			e.preventDefault();
			search();
		}}
	>
		<Input bind:value={query} placeholder="Search titles…" />
		<Button type="submit" variant="outline">Search</Button>
	</form>

	{#if data.items.length === 0}
		<EmptyState
			icon={FileSignatureIcon}
			title="No platform terms yet"
			description="Publish the privacy policy and service terms Graphene requires at login."
			action={data.canManage ? createFirst : undefined}
		/>
	{:else}
		<Table.Root>
			<Table.Header>
				<Table.Row>
					<Table.Head>Title</Table.Head>
					<Table.Head>Audience</Table.Head>
					<Table.Head>Required</Table.Head>
					<Table.Head>Status</Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{#each data.items as item (item.uid)}
					<Table.Row class="cursor-pointer" onclick={() => goto(`/operations/terms/${item.uid}`)}>
						<Table.Cell class="font-medium">
							<a href="/operations/terms/{item.uid}" class="hover:underline">{item.title}</a>
						</Table.Cell>
						<Table.Cell class="text-muted-foreground">{item.audience}</Table.Cell>
						<Table.Cell class="text-muted-foreground">{item.required_at.join(', ')}</Table.Cell>
						<Table.Cell>
							<StatusBadge
								tone={FORM_STATUS_META[item.status as keyof typeof FORM_STATUS_META]?.tone}
							>
								{FORM_STATUS_META[item.status as keyof typeof FORM_STATUS_META]?.label}
								{#if item.current_version_number}
									· v{item.current_version_number}
								{/if}
							</StatusBadge>
						</Table.Cell>
					</Table.Row>
				{/each}
			</Table.Body>
		</Table.Root>
	{/if}
</div>

<Sheet.Root bind:open={createOpen}>
	<Sheet.Content>
		<Sheet.Header>
			<Sheet.Title>New platform terms</Sheet.Title>
		</Sheet.Header>
		<form
			method="POST"
			action="?/create"
			class="space-y-4"
			use:enhance={() => {
				return ({ result }) => {
					if (result.type === 'redirect') createOpen = false;
				};
			}}
		>
			<div class="space-y-1.5">
				<Label for="title">Title</Label>
				<Input id="title" name="title" bind:value={title} required />
			</div>
			<SelectMenu
				name="audience"
				bind:value={audience}
				items={[
					{ value: 'all', label: 'Everyone' },
					{ value: 'staff', label: 'Staff' },
					{ value: 'client', label: 'Clients' }
				]}
			/>
			<Button type="submit">Create</Button>
		</form>
	</Sheet.Content>
</Sheet.Root>
