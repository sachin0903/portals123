<script lang="ts">
	import { deserialize, enhance } from '$app/forms';
	import { beforeNavigate, goto, invalidateAll } from '$app/navigation';
	import { untrack } from 'svelte';
	import {
		Button,
		Checkbox,
		ConfirmDialog,
		DocumentViewer,
		FileDropzone,
		FORM_STATUS_META,
		Input,
		Label,
		SelectMenu,
		StatusBadge,
		Textarea,
		toast
	} from '@graphene/ui';
	import { ArticleEditor, DEFAULT_ARTICLE_CSS } from '@graphene/ui/content-editor';
	import BackButton from '$lib/components/back-button.svelte';
	import type { ContentAudience } from '$lib/content';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	const item = $derived(data.item);
	const canManage = $derived(data.canManage);
	const meta = $derived(FORM_STATUS_META[item.status]);

	const initial = untrack(() => data.item);
	let title = $state(initial.title);
	let summary = $state(initial.summary ?? '');
	let audience = $state<ContentAudience>(initial.audience);
	let serviceTypes = $state<string[]>([...initial.service_types]);
	let subTypes = $state<string[]>([...initial.sub_types]);
	let categoryUid = $state(initial.category_uid ?? '');
	let expiresAt = $state(toLocalInput(initial.expires_at));
	let html = $state(initial.schema?.html ?? '');
	// The stylesheet is the editor's, not the item's, so it is never read back off the record: an
	// item saved before `create.ts` stopped round-tripping it through GrapesJS carries a mangled copy
	// (dropped `var()` shorthands — no sheet background, no pull-quote rule), and reading it back
	// would re-publish that copy for anyone who saves without touching the body.
	let css = $state(DEFAULT_ARTICLE_CSS);
	let dirty = $state(false);
	let saveState = $state<'idle' | 'saving' | 'saved' | 'error'>('idle');
	let newCategory = $state('');
	let heroFiles = $state<File[]>([]);
	let guideFiles = $state<File[]>([]);
	let uploading = $state(false);

	$effect(() => {
		title = data.item.title;
		summary = data.item.summary ?? '';
		audience = data.item.audience;
		serviceTypes = [...data.item.service_types];
		subTypes = [...data.item.sub_types];
		categoryUid = data.item.category_uid ?? '';
		expiresAt = toLocalInput(data.item.expires_at);
		html = data.item.schema?.html ?? '';
		dirty = false;
	});

	$effect(() => {
		if (form && 'error' in form && form.error) toast.error(String(form.error));
	});

	function markDirty() {
		dirty = true;
		if (saveState !== 'idle') saveState = 'idle';
	}

	function toggle(list: string[], value: string, on: boolean): string[] {
		if (on) return list.includes(value) ? list : [...list, value];
		return list.filter((v) => v !== value);
	}

	async function save() {
		if (!canManage || saveState === 'saving') return;
		saveState = 'saving';
		const body = new FormData();
		body.set('title', title);
		body.set('summary', summary);
		body.set('audience', audience);
		body.set('service_types', serviceTypes.join(','));
		body.set('sub_types', subTypes.join(','));
		body.set('category_uid', categoryUid);
		body.set('expires_at', expiresAt);
		if (item.kind !== 'guide') {
			body.set('html', html);
			body.set('css', css);
		}
		try {
			const res = await fetch('?/save', {
				method: 'POST',
				body,
				headers: { 'x-sveltekit-action': 'true' }
			});
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				dirty = false;
				saveState = 'saved';
				await invalidateAll();
			} else {
				saveState = 'error';
				toast.error(
					result.type === 'failure'
						? String(result.data?.error ?? 'Could not save.')
						: 'Could not save.'
				);
			}
		} catch {
			saveState = 'error';
			toast.error('Could not save.');
		}
	}

	async function publish() {
		if (dirty) await save();
		const body = new FormData();
		const res = await fetch('?/publish', {
			method: 'POST',
			body,
			headers: { 'x-sveltekit-action': 'true' }
		});
		const result = deserialize(await res.text());
		if (result.type === 'success') {
			toast.success('Published.');
			await invalidateAll();
		} else {
			toast.error(
				result.type === 'failure'
					? String(result.data?.error ?? 'Publish failed.')
					: 'Publish failed.'
			);
		}
	}

	async function uploadTo(path: 'hero' | 'guide', file: File) {
		uploading = true;
		try {
			const body = new FormData();
			body.append('file', file);
			const res = await fetch(`/content/${item.uid}/${path}`, { method: 'POST', body });
			if (!res.ok) {
				toast.error(await res.text());
				return;
			}
			await invalidateAll();
			toast.success(path === 'hero' ? 'Hero image uploaded.' : 'Guide uploaded.');
		} finally {
			uploading = false;
		}
	}

	$effect(() => {
		const file = heroFiles[0];
		if (file) {
			heroFiles = [];
			void uploadTo('hero', file);
		}
	});
	$effect(() => {
		const file = guideFiles[0];
		if (file) {
			guideFiles = [];
			void uploadTo('guide', file);
		}
	});

	let leaveOpen = $state(false);
	let pendingHref: string | null = null;
	let leaveConfirmed = false;

	beforeNavigate((nav) => {
		if (!dirty || leaveConfirmed) return;
		nav.cancel();
		pendingHref = nav.to?.url.href ?? null;
		leaveOpen = true;
	});

	const statusLine = $derived(
		saveState === 'saving'
			? 'Saving…'
			: saveState === 'error'
				? 'Save failed. Try again.'
				: dirty
					? 'Unsaved changes.'
					: saveState === 'saved'
						? 'Draft saved.'
						: 'Changes are kept until you save.'
	);

	const categoryItems = $derived([
		{ value: '__none__', label: 'None' },
		...data.categories.map((c) => ({ value: c.uid, label: c.name }))
	]);

	function toLocalInput(iso: string | null): string {
		if (!iso) return '';
		const d = new Date(iso);
		if (Number.isNaN(d.getTime())) return '';
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
	}
</script>

<svelte:head><title>{item.title} · Content · Platform</title></svelte:head>

<div class="mb-6 flex flex-col gap-4 border-b pb-5 sm:flex-row sm:items-end sm:justify-between">
	<div>
		<BackButton fallback="/content" fallbackLabel="content" />
		<div class="mt-3 flex items-center gap-2.5">
			<h1 class="text-2xl font-semibold tracking-tight">{item.title}</h1>
			<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
		</div>
		<p
			class="mt-1.5 text-sm"
			class:text-muted-foreground={!dirty && saveState !== 'error'}
			class:text-warning={dirty && saveState !== 'error'}
			class:text-destructive={saveState === 'error'}
		>
			{statusLine}
		</p>
	</div>
	<div class="flex items-center gap-2">
		<Button variant="outline" onclick={() => goto('/content')}>Done</Button>
		{#if canManage}
			<Button variant="outline" onclick={save} disabled={!dirty || saveState === 'saving'}
				>Save draft</Button
			>
			<Button onclick={publish} disabled={saveState === 'saving'}>Publish</Button>
		{/if}
	</div>
</div>

<div class="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
	<main class="min-w-0 space-y-6">
		{#if item.kind === 'guide'}
			<div class="bg-card space-y-4 rounded-xl border p-5 shadow-sm">
				<div>
					<h2 class="font-semibold">Guide document</h2>
					<p class="text-muted-foreground mt-1 text-sm">
						Upload the finished PDF. Clients will read it inside the portal.
					</p>
				</div>
				<FileDropzone
					bind:files={guideFiles}
					accept="application/pdf"
					title="Upload PDF guide"
					formats="PDF"
					rich
					disabled={!canManage || uploading}
					onreject={(reason) => toast.error(reason)}
				/>
				{#if item.has_file}
					<div class="min-h-[70vh] overflow-hidden rounded-xl border">
						<DocumentViewer
							src="/content/{item.uid}/guide-file"
							contentType={item.file_content_type ?? 'application/pdf'}
							name={item.title}
							class="h-full min-h-[70vh] w-full"
						/>
					</div>
				{/if}
			</div>
		{:else}
			<ArticleEditor
				{html}
				onchange={(next) => {
					html = next.html;
					css = next.css;
					markDirty();
				}}
				onUpload={async (file) => {
					const body = new FormData();
					body.append('file', file);
					const res = await fetch(`/content/${item.uid}/images`, { method: 'POST', body });
					if (!res.ok) throw new Error('Upload failed');
					const payload = (await res.json()) as { src: string };
					return payload.src;
				}}
			/>
		{/if}
	</main>

	<aside class="flex w-full flex-col gap-4 xl:sticky xl:top-5">
		<section class="bg-card rounded-xl border p-5 shadow-sm">
			<div class="mb-5">
				<h2 class="font-semibold">Publishing details</h2>
				<p class="text-muted-foreground mt-1 text-xs">
					How this item appears in Insights and the news widget.
				</p>
			</div>
			<div class="flex flex-col gap-4">
				<div class="flex flex-col gap-1.5">
					<Label for="title">Title</Label>
					<Input
						id="title"
						value={title}
						oninput={(e) => {
							title = e.currentTarget.value;
							markDirty();
						}}
						disabled={!canManage}
					/>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="summary">Summary</Label>
					<Textarea
						id="summary"
						value={summary}
						rows={4}
						placeholder="A short reason to read this…"
						oninput={(e) => {
							summary = e.currentTarget.value;
							markDirty();
						}}
						disabled={!canManage}
					/>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label>Folder</Label>
					<SelectMenu
						value={categoryUid || '__none__'}
						disabled={!canManage}
						onValueChange={(v) => {
							categoryUid = !v || v === '__none__' ? '' : v;
							markDirty();
						}}
						items={categoryItems}
					/>
					{#if canManage}
						<form
							method="POST"
							action="?/createCategory"
							use:enhance={() =>
								async ({ result }) => {
									if (result.type === 'success') {
										newCategory = '';
										await invalidateAll();
									} else if (result.type === 'failure') {
										toast.error(String(result.data?.error ?? 'Could not create category.'));
									}
								}}
							class="flex gap-2"
						>
							<Input name="name" bind:value={newCategory} placeholder="New folder" />
							<Button type="submit" variant="outline" disabled={!newCategory.trim()}>Add</Button>
						</form>
					{/if}
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="expires">Expires</Label>
					<Input
						id="expires"
						type="datetime-local"
						value={expiresAt}
						oninput={(e) => {
							expiresAt = e.currentTarget.value;
							markDirty();
						}}
						disabled={!canManage}
					/>
				</div>
			</div>
		</section>

		<section class="bg-card rounded-xl border p-5 shadow-sm">
			<div class="mb-5">
				<h2 class="font-semibold">Audience</h2>
				<p class="text-muted-foreground mt-1 text-xs">
					Choose who receives this item. Empty groups mean everyone in that group.
				</p>
			</div>
			<div class="flex flex-col gap-5">
				<div class="flex flex-col gap-1.5">
					<Label>Client type</Label>
					<SelectMenu
						value={audience}
						disabled={!canManage}
						onValueChange={(v) => {
							if (v === 'all' || v === 'individual' || v === 'organisation') {
								audience = v;
								markDirty();
							}
						}}
						items={[
							{ value: 'all', label: 'Everyone' },
							{ value: 'individual', label: 'Individuals' },
							{ value: 'organisation', label: 'Organisations' }
						]}
					/>
				</div>
				<div class="flex flex-col gap-2">
					<p class="text-sm font-medium">Service</p>
					{#each [['advised', 'Advised'], ['execution_only', 'Execution only'], ['discretionary', 'Discretionary']] as [value, label]}
						<label class="flex items-center gap-2 text-sm">
							<Checkbox
								checked={serviceTypes.includes(value)}
								disabled={!canManage}
								onCheckedChange={(on) => {
									serviceTypes = toggle(serviceTypes, value, on === true);
									markDirty();
								}}
							/>
							{label}
						</label>
					{/each}
				</div>
				<div class="flex flex-col gap-2">
					<p class="text-sm font-medium">Organisation type</p>
					{#each [['trust', 'Trust'], ['company', 'Company'], ['charity', 'Charity']] as [value, label]}
						<label class="flex items-center gap-2 text-sm">
							<Checkbox
								checked={subTypes.includes(value)}
								disabled={!canManage}
								onCheckedChange={(on) => {
									subTypes = toggle(subTypes, value, on === true);
									markDirty();
								}}
							/>
							{label}
						</label>
					{/each}
				</div>
			</div>
		</section>

		<section class="bg-card rounded-xl border p-5 shadow-sm">
			<div class="mb-4">
				<h2 class="font-semibold">Cover image</h2>
				<p class="text-muted-foreground mt-1 text-xs">
					Shown on the Insights card and article page.
				</p>
			</div>
			<FileDropzone
				bind:files={heroFiles}
				accept="image/png,image/jpeg"
				title="Upload image"
				formats="PNG or JPEG"
				size="slim"
				disabled={!canManage || uploading}
				onreject={(reason) => toast.error(reason)}
			/>
			{#if item.hero_url}
				<img src={item.hero_url} alt="" class="mt-3 aspect-[16/9] w-full rounded-xl object-cover" />
			{/if}
		</section>

		{#if canManage && item.status !== 'archived'}
			<form method="POST" action="?/archive" use:enhance>
				<Button type="submit" variant="ghost" class="text-destructive w-full">Archive item</Button>
			</form>
		{/if}
	</aside>
</div>

<ConfirmDialog
	bind:open={leaveOpen}
	title="Discard unsaved changes?"
	description="You have unsaved changes. Leave without saving?"
	confirmLabel="Leave"
	destructive
	onconfirm={() => {
		leaveConfirmed = true;
		if (pendingHref) void goto(pendingHref);
	}}
/>
