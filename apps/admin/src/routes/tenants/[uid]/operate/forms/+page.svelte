<script lang="ts">
	import {
		Button,
		Card,
		EmptyState,
		humanise,
		Input,
		Label,
		LoadErrorBanner,
		PageHeader,
		RecordId,
		SelectMenu,
		Sheet,
		slugifyCode,
		StatusBadge
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	let open = $state(false);
	let name = $state('');
	let code = $state('');
	let edited = $state(false);
	let search = $state('');
	$effect(() => {
		if (!edited) code = slugifyCode(name);
	});
	const filtered = $derived(
		data.forms.filter((form) =>
			`${form.name} ${form.code}`.toLowerCase().includes(search.toLowerCase())
		)
	);
	const base = $derived(`/tenants/${page.params.uid}/operate/forms`);
</script>

{#snippet clearFormSearch()}
	<Button variant="outline" size="sm" onclick={() => (search = '')}>Clear search</Button>
{/snippet}

<svelte:head><title>Forms · {data.tenant.name}</title></svelte:head>
<div class="space-y-5">
	<PageHeader title="Forms" description="Build this firm's questionnaires and fact-finds.">
		{#snippet actions()}
			{#if data.canManage}<Button onclick={() => (open = true)}
					><PlusIcon class="size-4" /> New form</Button
				>{/if}
		{/snippet}
	</PageHeader>
	{#if data.loadError}<LoadErrorBanner
			message="Couldn’t load forms."
			onRetry={() => invalidateAll()}
		/>{/if}
	<Input class="max-w-md" placeholder="Search forms…" bind:value={search} />
	{#if filtered.length === 0}
		<!-- "No forms match" was shown whether or not anything had been typed, so a firm with no
		     forms was told its (untouched) search had missed. A bare title is also half a state:
		     Carbon's pattern wants the sentence that says what belongs here. -->
		<EmptyState
			icon={ListChecksIcon}
			title={search.trim() ? 'No forms match that search' : 'No forms yet'}
			description={search.trim()
				? 'Try part of a form name or code, or clear the search to see every form this firm has.'
				: 'A form collects client answers, such as a fact-find, risk questionnaire or declaration.'}
			action={search.trim() ? clearFormSearch : undefined}
		/>
	{:else}
		<Card class="divide-y py-0">
			{#each filtered as form (form.uid)}
				<div class="flex items-center gap-3 px-4 py-3">
					<a class="min-w-0 flex-1" href={`${base}/${form.uid}`}>
						<p class="truncate text-sm font-medium">{form.name}</p>
						<RecordId value={form.code} />
					</a>
					<StatusBadge
						tone={form.status === 'published'
							? 'success'
							: form.status === 'archived'
								? 'neutral'
								: 'warning'}>{humanise(form.status)}</StatusBadge
					>
					{#if data.canManage && form.status !== 'archived'}
						<form method="POST" action="?/archive" use:enhance>
							<input type="hidden" name="uid" value={form.uid} /><Button variant="ghost" size="sm"
								>Archive</Button
							>
						</form>
					{/if}
				</div>
			{/each}
		</Card>
	{/if}
</div>

<Sheet.Root bind:open>
	<Sheet.Content>
		<Sheet.Header><Sheet.Title>New form</Sheet.Title></Sheet.Header>
		<form
			method="POST"
			action="?/create"
			class="space-y-4 p-4"
			use:enhance={() =>
				async ({ result }) => {
					if (result.type === 'success' && result.data?.uid)
						goto(`${base}/${result.data.uid}/edit`);
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
			<div>
				<Label for="entity">Applies to</Label><SelectMenu
					id="entity"
					name="entity_type"
					value="applicant"
					items={[
						{ value: 'applicant', label: 'Applicant' },
						{ value: 'account', label: 'Account' }
					]}
				/>
			</div>
			<Button type="submit" disabled={!name.trim() || !code.trim()}>Create form</Button>
		</form>
	</Sheet.Content>
</Sheet.Root>
