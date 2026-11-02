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
		LoadErrorBanner,
		PageHeader,
		Segmented,
		StatusBadge,
		Textarea,
		toast
	} from '@graphene/ui';
	import { untrack } from 'svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const item = $derived(data.item);
	const canManage = $derived(data.canManage);
	const meta = $derived(
		item ? FORM_STATUS_META[item.status as keyof typeof FORM_STATUS_META] : undefined
	);
	const initial = untrack(() => data.item);
	let title = $state(initial?.title ?? '');
	let bodyKind = $state(initial?.body_kind === 'pdf' ? 'pdf' : 'html');
	let bodyMd = $state(initial?.body_md ?? '');
	let always = $state(Boolean(initial?.applicability?.always));
	let hasTransfer = $state(Boolean(initial?.applicability?.has_transfer));
	let products = $state<string[]>(
		Array.isArray(initial?.applicability?.products)
			? (initial.applicability.products as string[])
			: []
	);
	let requiredOnboarding = $state(initial?.required_at.includes('onboarding') ?? true);
	let requiredLogin = $state(initial?.required_at.includes('login') ?? false);
	let archiveOpen = $state(false);

	$effect(() => {
		if (!data.item) return;
		title = data.item.title;
		bodyKind = data.item.body_kind === 'pdf' ? 'pdf' : 'html';
		bodyMd = data.item.body_md ?? '';
		always = Boolean(data.item.applicability?.always);
		hasTransfer = Boolean(data.item.applicability?.has_transfer);
		products = Array.isArray(data.item.applicability?.products)
			? (data.item.applicability.products as string[])
			: [];
		requiredOnboarding = data.item.required_at.includes('onboarding');
		requiredLogin = data.item.required_at.includes('login');
	});

	$effect(() => {
		if (form && 'error' in form && form.error) toast.error(String(form.error));
		if (form && 'ok' in form && form.ok) toast.success('Saved.');
	});

	function toggleProduct(code: string, on: boolean) {
		products = on ? [...new Set([...products, code])] : products.filter((p) => p !== code);
	}
</script>

<svelte:head><title>{item?.title ?? 'Terms'} · Operate</title></svelte:head>

<PageHeader
	title={item?.title ?? 'Terms'}
	description="Legal copy this firm's clients read before they agree."
	back={{ href: `/tenants/${data.tenantUid}/operate/terms`, label: 'Terms' }}
>
	{#snippet actions()}
		{#if item}
			<StatusBadge tone={meta?.tone}>{meta?.label}</StatusBadge>
		{/if}
	{/snippet}
</PageHeader>

{#if data.loadError || !item}
	<LoadErrorBanner />
{:else}
	<form method="POST" action="?/save" class="mt-6 space-y-6" use:enhance>
		<input type="hidden" name="body_kind" value={bodyKind} />
		{#if requiredOnboarding}<input type="hidden" name="required_at" value="onboarding" />{/if}
		{#if requiredLogin}<input type="hidden" name="required_at" value="login" />{/if}
		{#if always}<input type="hidden" name="always" value="on" />{/if}
		{#if hasTransfer}<input type="hidden" name="has_transfer" value="on" />{/if}
		{#each products as product (product)}
			<input type="hidden" name="products" value={product} />
		{/each}

		<div class="space-y-1.5">
			<Label for="title">Title</Label>
			<Input id="title" name="title" bind:value={title} disabled={!canManage} />
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

		<div class="space-y-2">
			<Label>Applies when</Label>
			<label class="flex items-center gap-2 text-sm">
				<Checkbox bind:checked={always} disabled={!canManage} />
				Every account
			</label>
			<label class="flex items-center gap-2 text-sm">
				<Checkbox bind:checked={hasTransfer} disabled={!canManage || always} />
				A transfer in
			</label>
			<div class="flex flex-wrap gap-3 text-sm">
				{#each ['ISA', 'JISA', 'GIA', 'SIPP'] as product (product)}
					<label class="flex items-center gap-2">
						<Checkbox
							checked={products.includes(product)}
							disabled={!canManage || always}
							onCheckedChange={(v) => toggleProduct(product, v === true)}
						/>
						{product}
					</label>
				{/each}
			</div>
		</div>

		<div class="space-y-3">
			<Label>Body</Label>
			<Segmented
				size="sm"
				shape="square"
				label="Body kind"
				options={[
					{ value: 'html', label: 'Written copy' },
					{ value: 'pdf', label: 'PDF' }
				]}
				value={bodyKind}
				disabled={!canManage}
				onValueChange={(v) => (bodyKind = v === 'pdf' ? 'pdf' : 'html')}
			/>
			{#if bodyKind === 'html'}
				<Textarea name="body_md" rows={14} bind:value={bodyMd} disabled={!canManage} />
			{:else}
				<p class="text-muted-foreground text-sm">
					{item.has_file
						? 'A PDF is attached. Publishing pins the current file.'
						: 'Upload a PDF, then publish.'}
				</p>
			{/if}
		</div>

		{#if canManage}
			<div class="flex flex-wrap gap-2">
				<Button type="submit">Save draft</Button>
				<Button formaction="?/publish" variant="secondary">Publish</Button>
				<Button type="button" variant="ghost" onclick={() => (archiveOpen = true)}>Archive</Button>
			</div>
		{/if}
	</form>

	{#if canManage && bodyKind === 'pdf'}
		<form
			method="POST"
			action="?/upload"
			enctype="multipart/form-data"
			class="mt-4 space-y-2"
			use:enhance
		>
			<Label for="file">PDF</Label>
			<input id="file" name="file" type="file" accept="application/pdf" class="text-sm" />
			<Button type="submit" variant="outline" size="sm">Upload</Button>
		</form>
	{/if}

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
