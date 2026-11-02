<script lang="ts">
	import {
		Button,
		Card,
		Checkbox,
		Dialog,
		DocumentViewer,
		EmptyState,
		FileDropzone,
		humanise,
		LoadErrorBanner,
		StatusBadge,
		toast
	} from '@graphene/ui';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import FileSignatureIcon from '@lucide/svelte/icons/file-signature';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import FormFillDialog from '$lib/components/form-fill-dialog.svelte';
	import { formDataSources } from '$lib/forms';
	import { formatDate } from '$lib/format';
	import { deserialize } from '$app/forms';
	import { invalidate } from '$app/navigation';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const requests = $derived(data.requests);

	type ClientRequest = PageData['requests'][number];
	type RequestItem = ClientRequest['items'][number];

	const ACTION_ICON = {
		upload: UploadIcon,
		agree: FileSignatureIcon,
		acknowledge: CheckIcon,
		complete_form: ListChecksIcon
	} as const;

	const ACTION_LABEL: Record<RequestItem['action'], string> = {
		upload: 'Document to upload',
		agree: 'Agreement to read and accept',
		acknowledge: 'Something to confirm you have seen',
		complete_form: 'Form to complete'
	};

	const ACTION_CTA: Record<RequestItem['action'], string> = {
		upload: 'Upload',
		agree: 'Review & agree',
		acknowledge: 'Review & confirm',
		complete_form: 'Complete'
	};

	const isActionable = (r: ClientRequest, i: RequestItem): boolean =>
		r.status === 'pending' && i.status === 'pending';

	/** "Due 3 August" / "Overdue since 3 August" / '' — clients get dates, not day-counts. */
	function dueLabel(r: ClientRequest): { text: string; overdue: boolean } | null {
		if (!r.due_date) return null;
		const overdue = new Date(`${r.due_date}T23:59:59`) < new Date() && r.status === 'pending';
		return {
			text: overdue ? `Overdue since ${formatDate(r.due_date)}` : `Due ${formatDate(r.due_date)}`,
			overdue
		};
	}

	const outstanding = $derived(
		requests.reduce((n, r) => n + r.items.filter((i) => isActionable(r, i)).length, 0)
	);

	/** Post one item's fulfilment, then reload. Returns null on success, else a message the calling
	 * dialog shows in place (errors are scoped, not shared page state). */
	async function postFulfil(
		req: ClientRequest,
		item: RequestItem,
		file?: File
	): Promise<string | null> {
		const body = new FormData();
		body.append('req_uid', req.uid);
		body.append('item_uid', item.uid);
		if (file) body.append('file', file, file.name);
		const res = await fetch('/requests?/fulfilItem', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type === 'success') {
			await invalidate('app:requests');
			return null;
		}
		return result.type === 'failure'
			? String(result.data?.error ?? 'That didn’t work.')
			: 'That didn’t work. Please try again.';
	}

	// The request/item a dialog is acting on — carried together, since fulfilment needs both uids.
	type Target = { req: ClientRequest; item: RequestItem };

	// Upload flow.
	let uploadOpen = $state(false);
	let uploadTarget = $state<Target | null>(null);
	let uploadError = $state('');
	let files = $state<File[]>([]);
	let uploading = $state(false);
	$effect(() => {
		if (files.length) uploadError = '';
	});

	function openUpload(req: ClientRequest, item: RequestItem): void {
		uploadTarget = { req, item };
		files = [];
		uploadError = '';
		uploadOpen = true;
	}

	async function submitUpload(): Promise<void> {
		if (uploading || !uploadTarget) return;
		if (!files.length) {
			uploadError = 'Choose a file.';
			return;
		}
		uploading = true;
		try {
			const err = await postFulfil(uploadTarget.req, uploadTarget.item, files[0]);
			if (err) uploadError = err;
			else {
				uploadOpen = false;
				toast.success('Thanks. Your adviser has it.');
			}
		} finally {
			uploading = false;
		}
	}

	// Agree / acknowledge: the client must SEE the document before confirming.
	let agreeOpen = $state(false);
	let agreeTarget = $state<Target | null>(null);
	let agreeError = $state('');
	let agreeChecked = $state(false);
	let agreeing = $state(false);
	let agreeLoading = $state(false);
	let agreeDoc = $state<{
		url: string | null;
		name: string | null;
		content_type: string | null;
		content: string | null;
	} | null>(null);

	async function loadAgreeDoc(target: Target): Promise<void> {
		agreeLoading = true;
		try {
			const body = new FormData();
			body.append('req_uid', target.req.uid);
			body.append('item_uid', target.item.uid);
			const res = await fetch('/requests?/targetDoc', { method: 'POST', body });
			const result = deserialize(await res.text());
			if (result.type === 'success' && (result.data?.url || result.data?.content)) {
				agreeDoc = {
					url: (result.data.url as string) ?? null,
					name: (result.data.name as string) ?? null,
					content_type: (result.data.content_type as string) ?? null,
					content: (result.data.content as string) ?? null
				};
			} else {
				agreeError = 'Couldn’t load the document to review. Please try again.';
			}
		} finally {
			agreeLoading = false;
		}
	}

	async function openAgree(req: ClientRequest, item: RequestItem): Promise<void> {
		agreeTarget = { req, item };
		agreeChecked = false;
		agreeDoc = null;
		agreeError = '';
		agreeOpen = true;
		await loadAgreeDoc({ req, item });
	}

	const isPdf = $derived(agreeDoc?.content_type === 'application/pdf');

	async function submitAgree(): Promise<void> {
		if (!agreeTarget || !agreeChecked || agreeing) return;
		agreeing = true;
		try {
			const err = await postFulfil(agreeTarget.req, agreeTarget.item);
			if (err) agreeError = err;
			else {
				agreeOpen = false;
				toast.success('Thanks. That’s confirmed.');
			}
		} finally {
			agreeing = false;
		}
	}

	// Form flow — the shared in-app builder-form dialog.
	let formOpen = $state(false);
	let formTarget = $state<Target | null>(null);

	function openForm(req: ClientRequest, item: RequestItem): void {
		formTarget = { req, item };
		formOpen = true;
	}

	// Throwing keeps the dialog open (its submit() swallows the close on reject).
	async function saveForm(values: Record<string, unknown>): Promise<void> {
		if (!formTarget) return;
		const body = new FormData();
		body.append('req_uid', formTarget.req.uid);
		body.append('item_uid', formTarget.item.uid);
		body.append('answers', JSON.stringify(values));
		const res = await fetch('/requests?/saveForm', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type === 'success') {
			formTarget.item.formAnswers = values;
			toast.success('Progress saved');
			return;
		}
		throw new Error(
			result.type === 'failure'
				? String(result.data?.error ?? 'That didn’t work.')
				: 'That didn’t work. Please try again.'
		);
	}

	async function submitForm(values: Record<string, unknown>): Promise<void> {
		if (!formTarget) return;
		const body = new FormData();
		body.append('req_uid', formTarget.req.uid);
		body.append('item_uid', formTarget.item.uid);
		body.append('answers', JSON.stringify(values));
		const res = await fetch('/requests?/submitForm', { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type === 'success') {
			await invalidate('app:requests');
			toast.success('Thanks. Your answers are with your adviser.');
			return;
		}
		throw new Error(
			result.type === 'failure'
				? String(result.data?.error ?? 'That didn’t work.')
				: 'That didn’t work. Please try again.'
		);
	}

	function start(req: ClientRequest, item: RequestItem): void {
		if (item.action === 'upload') openUpload(req, item);
		else if (item.action === 'complete_form') openForm(req, item);
		else void openAgree(req, item);
	}
</script>

<svelte:head><title>Requests</title></svelte:head>

<div class="space-y-6">
	<div>
		<h1 class="text-3xl font-semibold tracking-tight">Requests</h1>
		<p class="text-muted-foreground mt-1">
			{#if outstanding > 0}
				{outstanding} thing{outstanding === 1 ? '' : 's'} your adviser has asked you for
			{:else}
				Everything your adviser has asked you for
			{/if}
		</p>
	</div>

	{#if data.loadError}
		<LoadErrorBanner
			message="We couldn’t load your requests. Please try again."
			onRetry={() => invalidate('app:requests')}
		/>
	{:else if requests.length === 0}
		<EmptyState
			icon={CircleCheckIcon}
			title="Nothing outstanding"
			description="Your adviser hasn't asked you for anything. If they need something, it will appear here."
		/>
	{/if}

	<div class="space-y-6">
		{#each requests as r (r.uid)}
			{@const due = dueLabel(r)}
			{@const done = r.status === 'fulfilled'}
			<Card class="gap-0 py-0">
				<div class="flex flex-wrap items-start justify-between gap-3 border-b px-5 py-4">
					<div class="min-w-0">
						<h2 class="truncate text-lg font-semibold leading-snug">
							{done ? 'Completed request' : 'Your adviser needs'}
						</h2>
						<p class="text-muted-foreground text-sm">Asked on {formatDate(r.created_at)}</p>
					</div>
					<div class="flex flex-wrap items-center gap-2">
						{#if due}
							<StatusBadge tone={due.overdue ? 'danger' : 'neutral'}>{due.text}</StatusBadge>
						{/if}
						{#if r.priority === 'urgent' || r.priority === 'high'}
							<StatusBadge tone="warning">{humanise(r.priority)}</StatusBadge>
						{/if}
						<StatusBadge tone={done ? 'success' : 'info'}>
							{done ? 'Complete' : 'Outstanding'}
						</StatusBadge>
					</div>
				</div>

				{#if r.message}
					<p class="text-muted-foreground border-b px-5 py-3 text-sm whitespace-pre-line">
						“{r.message}”
					</p>
				{/if}

				<ol class="divide-y">
					{#each r.items as item (item.uid)}
						{@const Icon = ACTION_ICON[item.action] ?? ClipboardListIcon}
						{@const actionable = isActionable(r, item)}
						{@const complete = item.status === 'fulfilled'}
						<li class="flex items-center gap-4 px-5 py-3.5">
							<div
								class="flex size-9 shrink-0 items-center justify-center rounded-lg {complete
									? 'bg-success/10 text-success'
									: actionable
										? 'bg-warning/10 text-warning'
										: 'bg-muted text-muted-foreground'}"
							>
								{#if complete}
									<CheckIcon class="size-4" />
								{:else}
									<Icon class="size-4" />
								{/if}
							</div>
							<div class="min-w-0 flex-1">
								<p class="truncate text-sm font-medium">{item.title}</p>
								<p class="text-muted-foreground truncate text-xs">{ACTION_LABEL[item.action]}</p>
							</div>
							{#if complete}
								<StatusBadge tone="success" class="shrink-0">Done</StatusBadge>
							{:else if item.status === 'cancelled'}
								<StatusBadge tone="neutral" class="shrink-0">No longer needed</StatusBadge>
							{:else if actionable && item.action === 'complete_form' && !item.formFields?.length}
								<!-- The form couldn't be resolved, so there is nothing honest to open. -->
								<StatusBadge tone="warning" class="shrink-0">Contact your adviser</StatusBadge>
							{:else if actionable}
								<Button size="sm" class="shrink-0" onclick={() => start(r, item)}>
									{ACTION_CTA[item.action]}
								</Button>
							{:else}
								<StatusBadge tone="neutral" class="shrink-0">Pending</StatusBadge>
							{/if}
						</li>
					{/each}
				</ol>
			</Card>
		{/each}
	</div>
</div>

<!-- Upload dialog -->
<Dialog.Root
	bind:open={uploadOpen}
	onOpenChange={(o) => {
		if (!o) {
			files = [];
			uploadTarget = null;
			uploadError = '';
		}
	}}
>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{uploadTarget?.item.title ?? 'Upload a document'}</Dialog.Title>
			<Dialog.Description>
				Upload what your adviser asked for. PDF, Office, text and common image files are accepted,
				up to 25 MB.
			</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-2 py-2">
			<FileDropzone
				bind:files
				rich
				title="Upload document"
				onreject={(reason) => (uploadError = reason)}
			/>
			{#if uploadError}
				<p class="text-destructive text-xs" role="alert">{uploadError}</p>
			{/if}
		</div>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (uploadOpen = false)}>Cancel</Button>
			<Button disabled={files.length === 0 || uploading} onclick={submitUpload}>
				{uploading ? 'Uploading…' : 'Upload'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<!-- Agree / acknowledge: read the actual document, THEN confirm. -->
<Dialog.Root
	bind:open={agreeOpen}
	onOpenChange={(o) => {
		if (!o) {
			agreeTarget = null;
			agreeError = '';
		}
	}}
>
	<Dialog.Content class="sm:max-w-2xl">
		<Dialog.Header>
			<Dialog.Title>{agreeTarget?.item.title ?? 'Review'}</Dialog.Title>
			<!-- No file name: the stored document's name is the firm's own filing (often the uploaded
			     file), and a client confirming it has no use for it. -->
			<Dialog.Description>Please read this in full first.</Dialog.Description>
		</Dialog.Header>

		<div class="py-2">
			{#if agreeLoading}
				<div
					class="text-muted-foreground flex h-72 items-center justify-center rounded border text-sm"
				>
					Loading document…
				</div>
			{:else if agreeDoc?.content}
				<!-- A terms declaration: firm-authored HTML (not user input); the API only returns
				     `document_type='terms'` documents here. -->
				<div
					class="prose prose-sm max-h-72 max-w-none overflow-y-auto rounded border p-4 sm:max-h-96"
				>
					<!-- eslint-disable-next-line svelte/no-at-html-tags -->
					{@html agreeDoc.content}
				</div>
			{:else if agreeDoc?.url && isPdf}
				<!-- The shared viewer (pdf.js) over our own origin, not an <iframe> to the browser's
				     plugin — see the API's `/target` for why a presigned URL previewed as an empty
				     frame. -->
				<DocumentViewer
					src={agreeDoc.url}
					contentType={agreeDoc.content_type ?? undefined}
					name={agreeTarget?.item.title ?? 'Document'}
					class="h-72 overflow-hidden rounded border sm:h-96"
					searchable={false}
					zoomable={false}
				/>
			{:else if agreeDoc?.url}
				<a
					href={agreeDoc.url}
					target="_blank"
					rel="noopener noreferrer"
					class="text-brand flex h-24 items-center justify-center gap-2 rounded border underline"
				>
					<FileTextIcon class="size-4" /> Open the document
				</a>
			{:else}
				<div class="flex h-24 flex-col items-center justify-center gap-2 rounded border">
					<p class="text-muted-foreground text-sm">Couldn’t load the document.</p>
					<Button
						variant="outline"
						size="sm"
						onclick={() => agreeTarget && loadAgreeDoc(agreeTarget)}
						disabled={agreeLoading}
					>
						{agreeLoading ? 'Loading…' : 'Try again'}
					</Button>
				</div>
			{/if}
		</div>

		<label class="flex items-start gap-2.5 py-1 text-sm">
			<Checkbox
				checked={agreeChecked}
				disabled={!agreeDoc}
				onCheckedChange={(v) => (agreeChecked = v === true)}
			/>
			<span>
				{agreeTarget?.item.action === 'acknowledge'
					? 'I confirm I have read this.'
					: 'I have read and agree to this document.'}
			</span>
		</label>
		{#if agreeError}
			<p class="text-destructive text-xs" role="alert">{agreeError}</p>
		{/if}
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (agreeOpen = false)}>Cancel</Button>
			<Button disabled={!agreeDoc || !agreeChecked || agreeing} onclick={submitAgree}>
				{agreeing
					? 'Confirming…'
					: agreeTarget?.item.action === 'acknowledge'
						? 'Confirm'
						: 'Agree'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<!-- Form flow — the shared in-app builder-form dialog. -->
<FormFillDialog
	bind:open={formOpen}
	formName={formTarget?.item.formName ?? undefined}
	fields={formTarget?.item.formFields ?? []}
	answers={formTarget?.item.formAnswers ?? {}}
	dataSources={formDataSources}
	requestTitle={formTarget?.item.title ?? undefined}
	note={formTarget?.req.message ?? null}
	onsubmit={submitForm}
	onsave={saveForm}
/>
