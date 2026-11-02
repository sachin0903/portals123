<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		Button,
		Checkbox,
		ConfirmDialog,
		FORM_STATUS_META,
		Input,
		Label,
		PageHeader,
		SelectMenu,
		StatusBadge,
		Textarea,
		toast
	} from '@graphene/ui';
	import { untrack } from 'svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const item = $derived(data.item);
	const canManage = $derived(data.canManage);
	const meta = $derived(FORM_STATUS_META[item.status as keyof typeof FORM_STATUS_META]);

	const initial = untrack(() => data.item);
	let title = $state(initial.title);
	let audience = $state(initial.audience);
	let bodyMd = $state(initial.body_md ?? '');
	let requiredOnboarding = $state(initial.required_at.includes('onboarding'));
	let requiredLogin = $state(initial.required_at.includes('login'));
	let archiveOpen = $state(false);

	$effect(() => {
		title = data.item.title;
		audience = data.item.audience;
		bodyMd = data.item.body_md ?? '';
		requiredOnboarding = data.item.required_at.includes('onboarding');
		requiredLogin = data.item.required_at.includes('login');
	});

	$effect(() => {
		if (form && 'error' in form && form.error) toast.error(String(form.error));
		if (form && 'ok' in form && form.ok) toast.success('Saved.');
	});
</script>

<svelte:head><title>{item.title} · Terms</title></svelte:head>

<PageHeader
	title={item.title}
	description="Graphene-owned legal copy."
	back={{ href: '/operations/terms', label: 'Terms' }}
>
	{#snippet actions()}
		<StatusBadge tone={meta?.tone}>{meta?.label}</StatusBadge>
	{/snippet}
</PageHeader>

<form method="POST" action="?/save" class="mt-6 space-y-6" use:enhance>
	<input type="hidden" name="body_kind" value="html" />
	{#if requiredOnboarding}<input type="hidden" name="required_at" value="onboarding" />{/if}
	{#if requiredLogin}<input type="hidden" name="required_at" value="login" />{/if}

	<div class="space-y-1.5">
		<Label for="title">Title</Label>
		<Input id="title" name="title" bind:value={title} disabled={!canManage} />
	</div>

	<div class="space-y-1.5">
		<Label>Audience</Label>
		<SelectMenu
			name="audience"
			bind:value={audience}
			disabled={!canManage}
			items={[
				{ value: 'all', label: 'Everyone' },
				{ value: 'staff', label: 'Staff' },
				{ value: 'client', label: 'Clients' }
			]}
		/>
	</div>

	<div class="space-y-2">
		<Label>Required at</Label>
		<label class="flex items-center gap-2 text-sm">
			<Checkbox bind:checked={requiredOnboarding} disabled={!canManage} />
			Onboarding
		</label>
		<label class="flex items-center gap-2 text-sm">
			<Checkbox bind:checked={requiredLogin} disabled={!canManage} />
			Login
		</label>
	</div>

	<div class="space-y-1.5">
		<Label for="body">Written copy</Label>
		<Textarea id="body" name="body_md" rows={14} bind:value={bodyMd} disabled={!canManage} />
		<p class="text-muted-foreground text-xs">
			Markdown or pasted HTML. Published as sanitised HTML.
		</p>
	</div>

	{#if canManage}
		<div class="flex flex-wrap gap-2">
			<Button type="submit">Save draft</Button>
			<Button formaction="?/publish" variant="secondary">Publish</Button>
			<Button type="button" variant="ghost" onclick={() => (archiveOpen = true)}>Archive</Button>
		</div>
	{/if}
</form>

{#if data.versions.length}
	<div class="mt-8 space-y-2">
		<h2 class="text-sm font-semibold">Published versions</h2>
		<ul class="text-muted-foreground space-y-1 text-sm">
			{#each data.versions as version (version.uid)}
				<li>
					v{version.version_number} · {new Date(version.published_at).toLocaleDateString('en-GB')}
				</li>
			{/each}
		</ul>
	</div>
{/if}

<ConfirmDialog
	bind:open={archiveOpen}
	title="Archive these terms?"
	description="They stay on file for anyone who already accepted them."
	confirmLabel="Archive"
	onconfirm={async () => {
		const res = await fetch('?/archive', { method: 'POST', body: new FormData() });
		await invalidateAll();
		if (!res.ok) toast.error('Could not archive.');
	}}
/>
