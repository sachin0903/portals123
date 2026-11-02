<script lang="ts">
	import {
		Button,
		Card,
		EmptyState,
		humanise,
		Input,
		Label,
		PageHeader,
		Sheet,
		slugifyCode,
		StatusBadge
	} from '@graphene/ui';
	import WorkflowIcon from '@lucide/svelte/icons/workflow';
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	let open = $state(false),
		name = $state(''),
		code = $state(''),
		edited = $state(false),
		search = $state('');
	$effect(() => {
		if (!edited) code = slugifyCode(name);
	});
	const base = $derived(`/tenants/${page.params.uid}/operate/flows`);
	const rows = $derived(
		data.flows.filter((flow) =>
			`${flow.name} ${flow.code}`.toLowerCase().includes(search.toLowerCase())
		)
	);
</script>

{#snippet clearFlowSearch()}
	<Button variant="outline" size="sm" onclick={() => (search = '')}>Clear search</Button>
{/snippet}

<PageHeader title="Flows" description="Design this firm's multi-step processes.">
	{#snippet actions()}{#if data.canManage}<div class="flex gap-2">
				<Button variant="outline" href={`${base}/dead-letters`}>Failed side-effects</Button><Button
					onclick={() => (open = true)}>New flow</Button
				>
			</div>{/if}{/snippet}
</PageHeader>
<div class="mt-5 space-y-4">
	<Input class="max-w-md" placeholder="Search flows…" bind:value={search} />
	{#if rows.length === 0}<EmptyState
			icon={WorkflowIcon}
			title={search.trim() ? 'No flows match that search' : 'No flows yet'}
			description={search.trim()
				? 'Try part of a flow name or code, or clear the search to see every flow this firm has.'
				: 'A flow is a multi-step process such as onboarding, an annual review or a transfer.'}
			action={search.trim() ? clearFlowSearch : undefined}
		/>{:else}<Card class="divide-y py-0"
			>{#each rows as flow (flow.uid)}<div class="flex items-center gap-3 px-4 py-3">
					<a class="min-w-0 flex-1" href={`${base}/${flow.uid}`}
						><p class="font-medium">{flow.name}</p>
						<p class="text-xs">
							ID: <span class="text-muted-foreground font-mono">{flow.code}</span>
							<span class="text-muted-foreground">· {flow.version_count} versions</span>
						</p></a
					><StatusBadge tone={flow.status === 'published' ? 'success' : 'neutral'}
						>{humanise(flow.status)}</StatusBadge
					>{#if data.canManage}<form method="POST" action="?/setStatus" use:enhance>
							<input type="hidden" name="uid" value={flow.uid} /><input
								type="hidden"
								name="status"
								value={flow.status === 'archived' ? 'published' : 'archived'}
							/><Button variant="ghost" size="sm"
								>{flow.status === 'archived' ? 'Restore' : 'Archive'}</Button
							>
						</form>{/if}
				</div>{/each}</Card
		>{/if}
</div>
<Sheet.Root bind:open
	><Sheet.Content
		><Sheet.Header><Sheet.Title>New flow</Sheet.Title></Sheet.Header>
		<form
			class="space-y-4 p-4"
			method="POST"
			action="?/create"
			use:enhance={() =>
				async ({ result }) => {
					if (result.type === 'success' && result.data?.uid) goto(`${base}/${result.data.uid}`);
				}}
		>
			<div><Label for="name">Name</Label><Input id="name" name="name" bind:value={name} /></div>
			<div>
				<Label for="code">Reference code</Label><Input
					id="code"
					name="code"
					bind:value={code}
					oninput={() => (edited = true)}
				/>
			</div>
			<div><Label for="entity">Entity (optional)</Label><Input id="entity" name="entity" /></div>
			<Button disabled={!name || !code}>Create flow</Button>
		</form></Sheet.Content
	></Sheet.Root
>
