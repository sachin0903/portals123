<script lang="ts">
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import {
		Alert,
		AlertDescription,
		Button,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		Checkbox,
		ConfirmDialog,
		DocumentViewer,
		FileDropzone,
		FORM_STATUS_META,
		formatDate,
		Input,
		Label,
		PageHeader,
		Segmented,
		StatusBadge,
		Textarea,
		toast
	} from '@graphene/ui';
	import InfoIcon from '@lucide/svelte/icons/info';
	import { untrack } from 'svelte';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
	const item = $derived(data.item);
	const canManage = $derived(data.canManage);
	const meta = $derived(FORM_STATUS_META[item.status as keyof typeof FORM_STATUS_META]);

	const initial = untrack(() => data.item);
	let title = $state(initial.title);
	let bodyKind = $state(initial.body_kind === 'pdf' ? 'pdf' : 'html');
	let bodyMd = $state(initial.body_md ?? '');
	let hasTransfer = $state(Boolean(initial.applicability?.has_transfer));
	let products = $state<string[]>(
		Array.isArray(initial.applicability?.products)
			? (initial.applicability.products as string[])
			: []
	);
	let requiredOnboarding = $state(initial.required_at.includes('onboarding'));
	let requiredLogin = $state(initial.required_at.includes('login'));
	let scopeMode = $state<'all' | 'conditions'>(
		initial.applicability?.always ? 'all' : 'conditions'
	);
	let pdfFiles = $state<File[]>([]);
	let submitting = $state<'save' | 'publish' | 'upload' | null>(null);
	let editorForm = $state<HTMLFormElement | null>(null);
	let uploadSubmitter = $state<HTMLButtonElement | null>(null);
	let queuedFile = $state<File | null>(null);
	let archiveOpen = $state(false);

	$effect(() => {
		title = data.item.title;
		bodyKind = data.item.body_kind === 'pdf' ? 'pdf' : 'html';
		bodyMd = data.item.body_md ?? '';
		hasTransfer = Boolean(data.item.applicability?.has_transfer);
		products = Array.isArray(data.item.applicability?.products)
			? (data.item.applicability.products as string[])
			: [];
		requiredOnboarding = data.item.required_at.includes('onboarding');
		requiredLogin = data.item.required_at.includes('login');
		scopeMode = data.item.applicability?.always ? 'all' : 'conditions';
	});

	$effect(() => {
		if (form && 'error' in form && form.error) toast.error(String(form.error));
		if (form && 'ok' in form && form.ok) {
			toast.success(
				form.operation === 'publish'
					? 'Published.'
					: form.operation === 'upload'
						? 'PDF uploaded.'
						: 'Draft saved.'
			);
		}
	});

	// A chosen PDF is the upload instruction. Requiring a second button made the selected-file
	// state look like progress while nothing was happening. The hidden submitter preserves the
	// named SvelteKit action and native multipart form construction.
	$effect(() => {
		const file = pdfFiles[0] ?? null;
		if (!file || file === queuedFile || submitting !== null) return;
		queuedFile = file;
		queueMicrotask(() => {
			if (pdfFiles[0] === file && submitting === null && uploadSubmitter) {
				editorForm?.requestSubmit(uploadSubmitter);
			}
		});
	});

	function toggleProduct(code: string, on: boolean) {
		products = on ? [...new Set([...products, code])] : products.filter((p) => p !== code);
	}

	const scopeIncomplete = $derived(scopeMode === 'conditions' && !products.length && !hasTransfer);

	const applicabilitySummary = $derived.by(() => {
		if (scopeMode === 'all') return 'Every client is asked to accept these terms.';
		const labelOf = (code: string) =>
			data.products.find((choice) => choice.value === code)?.label ?? code;
		const conditions = [
			...products.map(labelOf),
			hasTransfer ? 'an application includes a transfer' : null
		].filter(Boolean);
		if (conditions.length === 0) return 'Choose at least one product or transfer condition.';
		return `Clients are asked when ${conditions.join(' or ')} applies.`;
	});

	const timingSummary = $derived(
		requiredOnboarding && requiredLogin
			? 'Clients agree during onboarding, and again at sign-in when the published version changes.'
			: requiredOnboarding
				? 'Account opening waits until the current version is accepted.'
				: requiredLogin
					? 'Clients must accept the current version before entering the portal.'
					: 'Choose at least one point when clients must agree.'
	);
</script>

<svelte:head><title>{item.title} · Terms</title></svelte:head>

<PageHeader title={item.title} description="Legal copy the client reads before they agree.">
	{#snippet actions()}
		<StatusBadge tone={meta?.tone}>{meta?.label}</StatusBadge>
		{#if canManage}
			<Button type="button" variant="destructive" onclick={() => (archiveOpen = true)}>
				Archive
			</Button>
			<!-- `form` targets the editor below, so the primary acts can sit in the header where every
			     other staff record keeps them rather than stranded under a long column. -->
			<Button type="submit" form="term-editor" variant="outline" disabled={submitting !== null}>
				{submitting === 'save' ? 'Saving…' : 'Save draft'}
			</Button>
			<Button
				type="submit"
				form="term-editor"
				formaction="?/publish"
				disabled={submitting !== null}
			>
				{submitting === 'publish' ? 'Publishing…' : 'Publish'}
			</Button>
		{/if}
	{/snippet}
</PageHeader>

<!-- The document is the work, so it takes the column that can grow; everything that describes WHEN
     and TO WHOM it applies is settings, and settings belong in the rail (the shape `/content`'s
     editor already uses). One form, so the PDF upload posts the same fields: its button carries its
     own `formaction`/`formenctype` rather than nesting a second form inside this one. -->
<form
	id="term-editor"
	method="POST"
	action="?/save"
	enctype="multipart/form-data"
	bind:this={editorForm}
	class="mt-6 grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]"
	use:enhance={({ action }) => {
		const operation =
			action.search === '?/publish' ? 'publish' : action.search === '?/upload' ? 'upload' : 'save';
		submitting = operation;
		return async ({ result, update }) => {
			try {
				await update();
				if (operation === 'upload' && result.type === 'success') {
					pdfFiles = [];
					queuedFile = null;
				}
			} finally {
				submitting = null;
			}
		};
	}}
>
	<input type="hidden" name="body_kind" value={bodyKind} />
	<input type="hidden" name="scope_mode" value={scopeMode} />
	{#if requiredOnboarding}<input type="hidden" name="required_at" value="onboarding" />{/if}
	{#if requiredLogin}<input type="hidden" name="required_at" value="login" />{/if}
	{#if scopeMode === 'all'}<input type="hidden" name="always" value="on" />{/if}
	{#if hasTransfer}<input type="hidden" name="has_transfer" value="on" />{/if}
	{#each products as product (product)}
		<input type="hidden" name="products" value={product} />
	{/each}

	<main class="min-w-0 space-y-6">
		<Card glass>
			<CardHeader>
				<CardTitle>What clients read</CardTitle>
				<CardDescription>
					Publish written copy in the portal, or attach the finished legal PDF.
				</CardDescription>
			</CardHeader>
			<CardContent class="space-y-4">
				<Segmented
					size="sm"
					shape="square"
					label="Document type"
					options={[
						{ value: 'html', label: 'Written copy' },
						{ value: 'pdf', label: 'PDF document' }
					]}
					value={bodyKind}
					disabled={!canManage}
					onValueChange={(value) => (bodyKind = value === 'pdf' ? 'pdf' : 'html')}
				/>
				{#if bodyKind === 'html'}
					<div class="space-y-1.5">
						<Label for="body">Terms copy</Label>
						<Textarea
							id="body"
							name="body_md"
							rows={24}
							placeholder="Write the terms clients must read and accept…"
							bind:value={bodyMd}
							disabled={!canManage}
							class="min-h-[28rem] font-mono text-xs leading-relaxed"
						/>
						<p class="text-muted-foreground text-xs">
							Markdown and pasted HTML are supported. Published copy is sanitised before clients see
							it.
						</p>
					</div>
				{:else}
					<div class="space-y-3">
						<p class="text-sm">
							{item.has_file
								? 'A PDF is attached. Publishing pins the current file, so an existing acceptance keeps pointing at the document that client saw.'
								: 'Attach the finished PDF before publishing.'}
						</p>
						{#if item.has_file}
							<div class="min-h-[70vh] overflow-hidden rounded-xl border">
								<DocumentViewer
									src="/terms/{item.uid}/file"
									contentType={item.file_content_type ?? 'application/pdf'}
									name={item.title}
									class="h-full min-h-[70vh] w-full"
								/>
							</div>
						{/if}
						{#if canManage}
							<FileDropzone
								name="file"
								bind:files={pdfFiles}
								accept="application/pdf"
								formats="PDF"
								title={item.has_file ? 'Replace attached PDF' : 'Attach PDF'}
								rich
								disabled={submitting !== null}
								onreject={(reason) => toast.error(reason)}
							/>
							<button
								bind:this={uploadSubmitter}
								type="submit"
								formaction="?/upload"
								formenctype="multipart/form-data"
								class="hidden"
								aria-hidden="true"
								tabindex={-1}
							>
								Upload PDF
							</button>
							{#if submitting === 'upload'}
								<p class="text-muted-foreground text-sm" aria-live="polite">Uploading PDF…</p>
							{/if}
						{/if}
					</div>
				{/if}
			</CardContent>
		</Card>
	</main>

	<aside class="flex w-full flex-col gap-4 xl:sticky xl:top-5">
		<Card glass>
			<CardHeader>
				<CardTitle>Details</CardTitle>
				<CardDescription
					>The name staff and clients use to recognise this agreement.</CardDescription
				>
			</CardHeader>
			<CardContent class="space-y-1.5">
				<Label for="title">Title</Label>
				<Input id="title" name="title" bind:value={title} disabled={!canManage} />
			</CardContent>
		</Card>

		<Card glass>
			<CardHeader>
				<CardTitle>When clients must agree</CardTitle>
				<CardDescription>
					Where the current published version interrupts the client.
				</CardDescription>
			</CardHeader>
			<CardContent class="space-y-3">
				<label class="flex items-start gap-3">
					<Checkbox bind:checked={requiredOnboarding} disabled={!canManage} class="mt-0.5" />
					<span>
						<span class="block text-sm font-medium">During onboarding</span>
						<span class="text-muted-foreground block text-xs">
							Asked in the onboarding checklist.
						</span>
					</span>
				</label>
				<label class="flex items-start gap-3">
					<Checkbox bind:checked={requiredLogin} disabled={!canManage} class="mt-0.5" />
					<span>
						<span class="block text-sm font-medium">At sign-in</span>
						<span class="text-muted-foreground block text-xs">
							Asked before the portal opens.
						</span>
					</span>
				</label>
				<p class="text-muted-foreground text-xs">{timingSummary}</p>
			</CardContent>
		</Card>

		<Card glass>
			<CardHeader>
				<CardTitle>Who these terms apply to</CardTitle>
				<CardDescription>Selected conditions are joined with “or”.</CardDescription>
			</CardHeader>
			<CardContent class="space-y-4">
				<Segmented
					size="sm"
					shape="square"
					label="Applicability"
					options={[
						{ value: 'all', label: 'All clients' },
						{ value: 'conditions', label: 'Selected' }
					]}
					value={scopeMode}
					disabled={!canManage}
					onValueChange={(value) => (scopeMode = value === 'conditions' ? 'conditions' : 'all')}
				/>

				{#if scopeMode === 'conditions'}
					<div class="space-y-2">
						<p class="text-sm font-medium">Product types</p>
						{#if data.products.length === 0}
							<p class="text-muted-foreground text-xs">
								This firm's product shelf is empty, so there is nothing to scope by yet.
							</p>
						{:else}
							<div class="grid grid-cols-2 gap-x-4 gap-y-2.5">
								{#each data.products as choice (choice.value)}
									<label class="flex items-center gap-2 text-sm">
										<Checkbox
											checked={products.includes(choice.value)}
											disabled={!canManage}
											onCheckedChange={(value) => toggleProduct(choice.value, value === true)}
										/>
										{choice.label}
										{#if !choice.onShelf}
											<span class="text-muted-foreground text-xs">(off shelf)</span>
										{/if}
									</label>
								{/each}
							</div>
						{/if}
					</div>
					<label class="flex items-start gap-3">
						<Checkbox bind:checked={hasTransfer} disabled={!canManage} class="mt-0.5" />
						<span>
							<span class="block text-sm font-medium">Applications with a transfer</span>
							<span class="text-muted-foreground block text-xs">
								Money or assets moving into an account.
							</span>
						</span>
					</label>
				{/if}

				<Alert variant={scopeIncomplete ? 'warning' : 'default'}>
					<InfoIcon class="size-4" />
					<AlertDescription>{applicabilitySummary}</AlertDescription>
				</Alert>
			</CardContent>
		</Card>

		{#if data.versions.length}
			<Card glass>
				<CardHeader>
					<CardTitle>Published versions</CardTitle>
					<CardDescription>
						Each acceptance stays tied to the version that client saw.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<ul class="divide-border divide-y text-sm">
						{#each data.versions as version (version.uid)}
							<li class="flex items-center justify-between py-2 first:pt-0 last:pb-0">
								<span class="font-medium">Version {version.version_number}</span>
								<span class="text-muted-foreground tabular-nums">
									{formatDate(version.published_at)}
								</span>
							</li>
						{/each}
					</ul>
				</CardContent>
			</Card>
		{/if}
	</aside>
</form>

<ConfirmDialog
	bind:open={archiveOpen}
	title="Archive these terms?"
	description="They stay on file for anyone who already accepted them. New journeys will not ask for them."
	confirmLabel="Archive"
	onconfirm={async () => {
		const res = await fetch('?/archive', { method: 'POST', body: new FormData() });
		await invalidateAll();
		if (!res.ok) toast.error('Could not archive.');
	}}
/>
