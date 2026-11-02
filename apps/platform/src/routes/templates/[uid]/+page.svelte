<script lang="ts">
	import { page } from '$app/state';
	import { Button, StatusBadge, FORM_STATUS_META } from '@graphene/ui';
	import { goto, invalidateAll } from '$app/navigation';
	import { enhance } from '$app/forms';
	import { deserialize } from '$app/forms';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import BackButton from '$lib/components/back-button.svelte';
	import LayoutTemplateIcon from '@lucide/svelte/icons/layout-template';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import FileOutputIcon from '@lucide/svelte/icons/file-output';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import DatabaseIcon from '@lucide/svelte/icons/database';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import CheckCircleIcon from '@lucide/svelte/icons/check-circle-2';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import ArchiveIcon from '@lucide/svelte/icons/archive';
	import ArchiveRestoreIcon from '@lucide/svelte/icons/archive-restore';
	import RefreshCwIcon from '@lucide/svelte/icons/refresh-cw';
	import { FormLinkDialog, DatasetLinkDialog } from '@graphene/ui/template-authoring';
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Badge,
		buttonVariants,
		Card,
		CardAction,
		CardContent,
		CardHeader,
		CardTitle,
		ConfirmDialog,
		DATASET_FORMAT_META,
		DOC_TYPE_META,
		DropdownMenu,
		EmptyState,
		humanise,
		Input,
		Label,
		PageHeader,
		PdfViewer,
		PreviewSpinner,
		SelectMenu,
		Sheet,
		TEMPLATE_STATUS_META,
		Textarea,
		toast
	} from '@graphene/ui';
	import { formatDate } from '@graphene/ui';

	const RENDERER_LABEL: Record<string, string> = { weasy: 'WeasyPrint', chromium: 'Chromium' };
	const DOC_TYPES = Object.entries(DOC_TYPE_META) as [string, { label: string }][];
	const docTypeItems = DOC_TYPES.map(([key, meta]) => ({ value: key, label: meta.label }));
	const rendererItems = [
		{ value: 'weasy', label: 'WeasyPrint' },
		{ value: 'chromium', label: 'Chromium' }
	];
	let detailsOpen = $state(false);
	let detailsError = $state<string | null>(null);
	let detailsSaving = $state(false);
	let editName = $state('');
	let editDtype = $state('');
	let editRenderer = $state('');
	let editDesc = $state('');

	function openDetails() {
		if (!template) return;
		detailsError = null;
		editName = template.name;
		editDtype = template.documentType;
		editRenderer = latestVersion?.renderer ?? 'weasy';
		editDesc = template.description ?? '';
		detailsOpen = true;
	}
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const id = $derived(page.params.uid ?? '');
	const template = $derived(data.template);
	const versions = $derived(data.versions);
	const forms = $derived(data.forms);
	// Real datasets catalogue (from GET /datasets); the link dialog resolves links by code.
	const datasets = $derived(data.datasets);
	const canManage = $derived(data.canManage);

	// On-screen preview: the TRUE PDF render of the latest saved version (same engine + pipeline as
	// generation, via the worker's render service), lazily fetched so the page paints first. The
	// native PDF viewer paginates, so overflow/page breaks show exactly as the generated document —
	// unlike the old scaled HTML thumbnail, which clipped everything past page one.
	let previewPdfUrl = $state<string | null>(null);
	let previewState = $state<'loading' | 'ready' | 'empty' | 'error'>('loading');
	// Bumped by the failure state's "Try again". The effect otherwise only re-runs when the template
	// changes, so without something for it to read the button would be a control that does nothing —
	// worse than no button, because it reads as "we tried and it is still broken".
	let previewAttempt = $state(0);
	$effect(() => {
		if (!id || !template) return;
		// The attempt number rides on the URL. That is what makes this effect DEPEND on it — so the
		// failure state's Try again re-runs it at all — and it gives each retry its own address, so
		// no cached failure can be handed back. Omitted on the first load to keep the ordinary
		// request clean; the endpoint ignores the param either way.
		const url = `/templates/${id}/preview.pdf${previewAttempt ? `?attempt=${previewAttempt}` : ''}`;
		let objectUrl: string | null = null;
		let cancelled = false;
		previewState = 'loading';
		previewPdfUrl = null;
		(async () => {
			try {
				const res = await fetch(url);
				if (cancelled) return;
				if (res.status === 422) return void (previewState = 'empty'); // no content yet
				if (!res.ok) return void (previewState = 'error');
				objectUrl = URL.createObjectURL(await res.blob());
				if (cancelled) return URL.revokeObjectURL(objectUrl);
				previewPdfUrl = objectUrl;
				previewState = 'ready';
			} catch {
				if (!cancelled) previewState = 'error';
			}
		})();
		return () => {
			cancelled = true;
			if (objectUrl) URL.revokeObjectURL(objectUrl);
		};
	});
	const dtm = $derived(template ? DOC_TYPE_META[template.documentType] : null);

	// API returns versions newest-first; the latest is what the editor edits / what we publish.
	const latestVersion = $derived(versions[0]);
	const isArchived = $derived(!!template?.archivedAt);
	// Publish only a live template's draft — an archived one is restored first.
	const canPublish = $derived(canManage && !isArchived && latestVersion?.status === 'draft');
	// Version history shows published milestones only.
	const milestones = $derived(versions.filter((v) => v.publishedAt));

	const headerMeta = $derived(
		[
			template?.currentVersionNumber ? `v${template.currentVersionNumber}` : null,
			template?.archivedAt ? `Archived ${formatDate(template.archivedAt)}` : null
		]
			.filter(Boolean)
			.join(' · ')
	);

	const datasetCodes = $derived(template?.linkedDatasets ?? []);
	const linkedDatasets = $derived(
		datasetCodes.length > 0 ? datasets.filter((d) => datasetCodes.includes(d.code)) : []
	);
	const effectiveFormCode = $derived(template?.inputFormCode ?? null);
	const linkedForm = $derived(forms.find((f) => f.code === effectiveFormCode) ?? null);

	let changeOpen = $state(false);
	let datasetsManageOpen = $state(false);
	let publishOpen = $state(false);
	let publishing = $state(false);
	let publishForm = $state<HTMLFormElement>();
	// Soft warning after publishing: variables the published version references that won't fill.
	let publishWarnings = $state<{ name: string; suggestion?: string | null }[]>([]);

	async function postAction(action: string, body: FormData, successMessage: string): Promise<void> {
		const res = await fetch(action, { method: 'POST', body });
		const result = deserialize(await res.text());
		if (result.type === 'success') {
			toast.success(successMessage);
			await invalidateAll();
		} else if (result.type === 'failure') {
			toast.error((result.data?.error as string) ?? 'That didn’t work.');
		}
	}

	function toggleDataset(code: string) {
		const adding = !datasetCodes.includes(code);
		const next = adding ? [...datasetCodes, code] : datasetCodes.filter((c) => c !== code);
		const body = new FormData();
		body.append('codes', JSON.stringify(next));
		void postAction('?/setDatasets', body, adding ? 'Dataset linked.' : 'Dataset unlinked.');
	}

	function saveInputForm(code: string | null) {
		const body = new FormData();
		body.append('code', code ?? '');
		void postAction('?/setInputForm', body, code ? 'Input form linked.' : 'Input form removed.');
	}

	// Lifecycle actions (mirror the templates list). Archive/restore stay on the page — the header
	// reflects the new state and offers the reverse. Duplicate opens the copy in the editor.
	let lifecycleBusy = $state(false);
	async function archiveTemplate() {
		lifecycleBusy = true;
		await postAction('?/archive', new FormData(), 'Template archived.');
		lifecycleBusy = false;
	}
	async function restoreTemplate() {
		lifecycleBusy = true;
		await postAction('?/unarchive', new FormData(), 'Template restored.');
		lifecycleBusy = false;
	}
	async function duplicateTemplate() {
		lifecycleBusy = true;
		try {
			const res = await fetch('?/duplicate', { method: 'POST', body: new FormData() });
			const result = deserialize(await res.text());
			if (result.type === 'success' && result.data?.uid) {
				await goto(`/templates/${result.data.uid as string}/edit`);
			} else if (result.type === 'failure') {
				toast.error((result.data?.error as string) ?? 'Could not duplicate the template.');
			}
		} catch {
			toast.error('Could not save the template. Please try again.');
		} finally {
			lifecycleBusy = false;
		}
	}
</script>

{#if !template || !dtm}
	<EmptyState
		icon={LayoutTemplateIcon}
		title="Template not found"
		description={`The template ${id} doesn't exist.`}
	>
		{#snippet action()}
			<Button variant="outline" onclick={() => goto('/templates')}>
				<ArrowLeftIcon class="mr-1 size-4" />
				Back to templates
			</Button>
		{/snippet}
	</EmptyState>
{:else}
	<div>
		<!-- Back -->
		<BackButton fallback="/templates" fallbackLabel="templates" class="mb-4" />

		<!-- Header -->
		<PageHeader title={template.name} description={headerMeta || undefined}>
			{#snippet actions()}
				<Badge class="{dtm.bgColor} {dtm.textColor}">{dtm.label}</Badge>
				{#if template.currentVersionStatus}
					{@const stm = TEMPLATE_STATUS_META[template.currentVersionStatus]}
					<StatusBadge tone={stm.tone}>{stm.label}</StatusBadge>
				{/if}
				<Button variant="outline" size="sm" onclick={() => goto(`/templates/${id}/edit`)}>
					<PencilIcon class="mr-1.5 size-4" />
					{canManage ? 'Edit' : 'View'}
				</Button>
				{#if canPublish && latestVersion}
					<form
						method="POST"
						action="?/publish"
						bind:this={publishForm}
						use:enhance={() => {
							publishWarnings = [];
							publishing = true;
							return async ({ result }) => {
								publishing = false;
								if (result.type === 'success') {
									publishWarnings =
										(result.data?.unknownVariables as {
											name: string;
											suggestion?: string | null;
										}[]) ?? [];
									toast.success('Version published.');
									await invalidateAll();
								} else if (result.type === 'failure')
									toast.error((result.data?.error as string) ?? 'Could not publish.');
							};
						}}
					>
						<input type="hidden" name="vid" value={latestVersion.uid} />
						<Button
							type="button"
							size="sm"
							variant="outline"
							disabled={publishing}
							onclick={() => (publishOpen = true)}
						>
							<CheckCircleIcon class="mr-1.5 size-4" />
							{publishing ? 'Publishing…' : 'Publish'}
						</Button>
					</form>
				{/if}
				{#if template.currentVersionStatus === 'published' && !isArchived}
					<Button size="sm" onclick={() => goto(`/documents/create?template=${template.uid}`)}>
						<FileOutputIcon class="mr-1.5 size-4" />
						Generate
					</Button>
				{/if}
				{#if isArchived && canManage}
					<Button size="sm" disabled={lifecycleBusy} onclick={restoreTemplate}>
						<ArchiveRestoreIcon class="mr-1.5 size-4" />
						Restore
					</Button>
				{/if}
				{#if canManage}
					<DropdownMenu.Root>
						<DropdownMenu.Trigger
							class={buttonVariants({ variant: 'outline', size: 'icon' })}
							aria-label="More actions"
							disabled={lifecycleBusy}
						>
							<EllipsisIcon class="size-4" />
						</DropdownMenu.Trigger>
						<DropdownMenu.Content align="end" class="w-44">
							<DropdownMenu.Item onclick={duplicateTemplate}>
								<CopyIcon class="mr-2 size-4" />
								Duplicate
							</DropdownMenu.Item>
							{#if !isArchived}
								<DropdownMenu.Separator />
								<DropdownMenu.Item class="text-destructive" onclick={archiveTemplate}>
									<ArchiveIcon class="mr-2 size-4" />
									Archive
								</DropdownMenu.Item>
							{/if}
						</DropdownMenu.Content>
					</DropdownMenu.Root>
				{/if}
			{/snippet}
		</PageHeader>

		<!-- Management (left) + preview/history (right sidebar) -->
		<div class="grid gap-4 lg:grid-cols-[1fr_320px] lg:items-start">
			<div class="space-y-4">
				<!-- Details — horizontal metadata card -->
				<Card glass>
					<CardHeader>
						<CardTitle class="text-sm">Details</CardTitle>
						{#if canManage}
							<CardAction>
								<Button variant="ghost" size="sm" class="h-7 px-2 text-xs" onclick={openDetails}>
									<PencilIcon class="mr-1 size-3.5" /> Edit
								</Button>
							</CardAction>
						{/if}
					</CardHeader>
					<CardContent>
						<dl class="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-3 lg:grid-cols-4">
							<div>
								<dt class="text-muted-foreground text-xs">Code</dt>
								<dd class="mt-1">
									<code class="bg-muted rounded px-1.5 py-0.5 font-mono text-xs"
										>{template.code}</code
									>
								</dd>
							</div>
							<div>
								<dt class="text-muted-foreground text-xs">Document type</dt>
								<dd class="mt-1 text-xs">{dtm.label}</dd>
							</div>
							<div>
								<dt class="text-muted-foreground text-xs">Input form</dt>
								<dd class="mt-1 truncate text-xs">
									{linkedForm ? linkedForm.name : 'None'}
								</dd>
							</div>
							<div>
								<dt class="text-muted-foreground text-xs">Renderer</dt>
								<dd class="mt-1 text-xs">
									{RENDERER_LABEL[latestVersion?.renderer ?? 'weasy'] ?? 'WeasyPrint'}
								</dd>
							</div>
							<div>
								<dt class="text-muted-foreground text-xs">Created by</dt>
								<dd class="mt-1 text-xs">{template.createdBy}</dd>
							</div>
							<div>
								<dt class="text-muted-foreground text-xs">Created</dt>
								<dd class="mt-1 text-xs">{formatDate(template.createdAt)}</dd>
							</div>
						</dl>
					</CardContent>
				</Card>

				{#if template.description}
					<Card glass>
						<CardHeader>
							<CardTitle class="text-sm">Description</CardTitle>
						</CardHeader>
						<CardContent>
							<p class="text-muted-foreground text-sm leading-relaxed">
								{template.description}
							</p>
						</CardContent>
					</Card>
				{/if}

				<!-- Input form + Datasets, side by side -->
				<div class="grid gap-4 sm:grid-cols-2">
					<!-- Input form -->
					<Card glass>
						<CardHeader>
							<CardTitle class="flex items-center gap-2 text-sm">
								<ListChecksIcon class="text-muted-foreground size-4" />
								Input form
							</CardTitle>
							{#if linkedForm}
								<CardAction>
									<Button
										variant="ghost"
										size="sm"
										class="h-7 px-2 text-xs"
										onclick={() => (changeOpen = true)}
									>
										Change
									</Button>
								</CardAction>
							{/if}
						</CardHeader>
						<CardContent>
							{#if linkedForm}
								{@const fm = FORM_STATUS_META[linkedForm.status]}
								<div class="rounded-lg border p-3">
									<div class="flex items-center gap-2">
										<div
											class="bg-brand/10 flex size-8 shrink-0 items-center justify-center rounded-lg"
										>
											<ListChecksIcon class="text-brand size-4" />
										</div>
										<p class="min-w-0 flex-1 truncate text-sm">{linkedForm.name}</p>
										<StatusBadge tone={fm.tone}>{fm.label}</StatusBadge>
									</div>
									<p class="text-muted-foreground mt-1.5 text-xs">
										<span>{humanise(linkedForm.entityType)}</span> · {linkedForm.fieldCount}
										fields · filled at generate time
									</p>
									<Button
										variant="outline"
										size="sm"
										class="mt-2.5 w-full"
										href={`/forms/${linkedForm.uid}?from=${encodeURIComponent(`/templates/${id}`)}&fromLabel=${encodeURIComponent(template.name)}`}
									>
										<EyeIcon class="mr-1 size-3.5" /> Preview form
									</Button>
								</div>
							{:else}
								<EmptyState compact title="No input form linked.">
									{#snippet action()}
										<Button size="sm" onclick={() => (changeOpen = true)}>
											<ListChecksIcon class="mr-1.5 size-4" /> Choose a form
										</Button>
									{/snippet}
								</EmptyState>
							{/if}
						</CardContent>
					</Card>

					<!-- Datasets -->
					<Card glass>
						<CardHeader>
							<CardTitle class="flex items-center gap-2 text-sm">
								<DatabaseIcon class="text-muted-foreground size-4" />
								Datasets
							</CardTitle>
							<CardAction>
								<Button
									variant="ghost"
									size="sm"
									class="h-7 px-2 text-xs"
									onclick={() => (datasetsManageOpen = true)}
								>
									Manage
								</Button>
							</CardAction>
						</CardHeader>
						<CardContent>
							{#if linkedDatasets.length === 0}
								<EmptyState compact title="No datasets linked.">
									{#snippet action()}
										<Button size="sm" onclick={() => (datasetsManageOpen = true)}>
											<DatabaseIcon class="mr-1.5 size-4" /> Link datasets
										</Button>
									{/snippet}
								</EmptyState>
							{:else}
								<div class="space-y-2">
									{#each linkedDatasets as ds (ds.uid)}
										{@const sm = DATASET_FORMAT_META[ds.format]}
										<div class="flex items-center gap-2 rounded-lg border px-3 py-2">
											<DatabaseIcon class="size-4 shrink-0 {sm.textColor}" />
											<div class="min-w-0 flex-1">
												<p class="truncate text-sm">{ds.name}</p>
												<p class="text-muted-foreground truncate font-mono text-xs">{ds.code}</p>
											</div>
											<Badge class="{sm.bgColor} {sm.textColor}">{sm.label}</Badge>
										</div>
									{/each}
								</div>
							{/if}
						</CardContent>
					</Card>
				</div>
			</div>

			<!-- Sidebar: editor entry + version history -->
			<div class="space-y-3">
				{#if publishWarnings.length}
					<Alert variant="warning">
						<TriangleAlertIcon class="size-4" />
						<AlertTitle>
							Published with {publishWarnings.length}
							{publishWarnings.length === 1 ? 'variable' : 'variables'} won't fill
						</AlertTitle>
						<AlertDescription>
							<p>
								These tokens aren't in the template's catalogue (likely hand-typed or mistyped).
								Open the editor to fix them from the variable picker.
							</p>
							<ul class="mt-2 flex flex-wrap gap-1.5">
								{#each publishWarnings as v}
									<li class="inline-flex items-center gap-1">
										<code class="bg-warning/15 rounded px-1 font-mono text-xs"
											>{`{{ ${v.name} }}`}</code
										>
										{#if v.suggestion}<span class="text-muted-foreground text-xs"
												>→ {v.suggestion}?</span
											>{/if}
									</li>
								{/each}
							</ul>
						</AlertDescription>
					</Alert>
				{/if}
				<!-- Preview: the true PDF render of the latest saved version (same engine as generation),
				     lazily loaded so the page paints first. The native viewer paginates — overflow shows
				     exactly as the generated document. -->
				<Card glass>
					<CardHeader>
						<CardTitle class="text-sm">Preview</CardTitle>
					</CardHeader>
					<CardContent>
						{#if previewState === 'empty'}
							<EmptyState
								compact
								icon={LayoutTemplateIcon}
								title="Nothing to preview yet"
								description="Add content in the editor and save to see it here."
							>
								{#snippet action()}
									<Button size="sm" onclick={() => goto(`/templates/${id}/edit`)}>
										<PencilIcon class="mr-1.5 size-4" /> Open in editor
									</Button>
								{/snippet}
							</EmptyState>
						{:else if previewState === 'ready' && previewPdfUrl}
							<!-- Self-rendered (pdf.js): clean stacked sheets, no native PDF-viewer chrome. A
							     fixed-height scroll region (not a one-page A4 box) so multi-page docs scroll. -->
							<!-- No find bar, no zoom pill. This is a 26rem THUMBNAIL of the current version, not
							     a reading surface: the controls cover the page they sit on, and the answer to
							     "I need to look closer" here is to open the document, not to zoom a preview.
							     The documents viewer keeps both — it IS the reading surface — which is why
							     these are per-mount props rather than a change to the component. -->
							<PdfViewer
								src={previewPdfUrl}
								searchable={false}
								zoomable={false}
								class="h-[26rem] rounded-md border"
							/>
						{:else if previewState === 'error'}
							<!-- A failure over someone's own work answers three things and names no internal
							     moving part: their template is safe, what to try now, and who to tell if it
							     persists. An adviser cannot act on the state of our renderer, so it is the
							     administrator who is named — the wording the generate flow uses when a render
							     does not come back. -->
							<EmptyState
								compact
								icon={TriangleAlertIcon}
								title="Preview unavailable"
								description="Couldn’t create the preview. Your template is unchanged. Try again, or ask your administrator for help."
								class="flex h-[26rem] flex-col items-center justify-center"
							>
								{#snippet action()}
									<Button size="sm" variant="outline" onclick={() => previewAttempt++}>
										<RefreshCwIcon class="mr-1.5 size-4" /> Try again
									</Button>
								{/snippet}
							</EmptyState>
						{:else}
							<div
								class="relative flex h-[26rem] items-center justify-center rounded-md border p-3 text-center"
							>
								<PreviewSpinner />
							</div>
						{/if}
						{#if previewState !== 'empty'}
							<Button
								size="sm"
								variant="outline"
								class="mt-2.5 w-full"
								onclick={() => goto(`/templates/${id}/edit`)}
							>
								<PencilIcon class="mr-1.5 size-4" /> Open in editor
							</Button>
						{/if}
					</CardContent>
				</Card>

				<!-- Version history — compact list; scrolls when long -->
				<Card glass>
					<CardHeader>
						<CardTitle class="flex items-center gap-2 text-sm">
							<HistoryIcon class="text-muted-foreground size-4 shrink-0" />
							Version history
						</CardTitle>
					</CardHeader>
					<CardContent>
						{#if milestones.length === 0}
							<EmptyState
								compact
								title="No published versions yet"
								description="Publishing a version records it here as a milestone."
							/>
						{:else}
							<div class="max-h-[28rem] space-y-2.5 overflow-y-auto">
								{#each milestones as v (v.uid)}
									{@const isCurrent = v.versionNumber === template.currentVersionNumber}
									<div class="flex items-start gap-2.5 rounded-lg border p-2.5">
										<span class="w-8 shrink-0 font-mono text-sm font-semibold"
											>v{v.versionNumber}</span
										>
										<div class="min-w-0 flex-1">
											<div class="flex flex-wrap items-center gap-1.5">
												<StatusBadge tone={TEMPLATE_STATUS_META[v.status].tone}>
													{TEMPLATE_STATUS_META[v.status].label}
												</StatusBadge>
												{#if isCurrent}
													<span class="text-primary text-[11px] font-semibold">Current</span>
												{/if}
											</div>
											<p class="text-muted-foreground mt-1 text-xs">
												Published {formatDate(v.publishedAt ?? v.createdAt)}{v.publishedBy
													? ` · ${v.publishedBy}`
													: ''}
											</p>
										</div>
									</div>
								{/each}
							</div>
						{/if}
					</CardContent>
				</Card>
			</div>
		</div>

		<FormLinkDialog
			bind:open={changeOpen}
			{forms}
			documentType={template.documentType}
			value={effectiveFormCode}
			onsave={saveInputForm}
		/>

		<DatasetLinkDialog
			bind:open={datasetsManageOpen}
			{datasets}
			linked={datasetCodes}
			ontoggle={toggleDataset}
		/>

		<ConfirmDialog
			bind:open={publishOpen}
			title={`Publish version ${latestVersion?.versionNumber ?? ''}?`}
			description="Published versions are immutable."
			confirmLabel="Publish"
			onconfirm={() => publishForm?.requestSubmit()}
		/>

		<Sheet.Root bind:open={detailsOpen}>
			<Sheet.Content class="flex flex-col gap-0">
				<Sheet.Header class="border-b px-6 pb-4 pt-6">
					<Sheet.Title>Edit details</Sheet.Title>
				</Sheet.Header>
				<form
					method="POST"
					action="?/updateDetails"
					class="flex min-h-0 flex-1 flex-col"
					use:enhance={() => {
						detailsError = null;
						detailsSaving = true;
						return async ({ result }) => {
							detailsSaving = false;
							if (result.type === 'success') {
								detailsOpen = false;
								toast.success('Details saved.');
								await invalidateAll();
							} else if (result.type === 'failure') {
								detailsError = (result.data?.error as string) ?? 'Could not save the details.';
							}
						};
					}}
				>
					<div class="flex-1 space-y-4 overflow-y-auto px-6 py-5">
						{#if detailsError}
							<Alert variant="destructive">
								<AlertDescription>{detailsError}</AlertDescription>
							</Alert>
						{/if}
						<div class="space-y-1.5">
							<Label for="tpl-name">Name</Label>
							<Input id="tpl-name" name="name" bind:value={editName} />
						</div>
						<div class="space-y-1.5">
							<Label for="tpl-dtype">Document type</Label>
							<SelectMenu
								id="tpl-dtype"
								name="document_type"
								items={docTypeItems}
								bind:value={editDtype}
							/>
						</div>
						<div class="space-y-1.5">
							<Label for="tpl-renderer">Renderer</Label>
							<SelectMenu
								id="tpl-renderer"
								name="renderer"
								items={rendererItems}
								bind:value={editRenderer}
							/>
							<p class="text-muted-foreground text-xs">
								PDF format. Applies to the latest version.
							</p>
						</div>
						<div class="space-y-1.5">
							<Label for="tpl-desc">
								Description <span class="text-muted-foreground font-normal">(optional)</span>
							</Label>
							<Textarea id="tpl-desc" name="description" rows={3} bind:value={editDesc} />
						</div>
					</div>
					<div class="flex items-center justify-between border-t px-6 py-4">
						<Button type="button" variant="ghost" onclick={() => (detailsOpen = false)}
							>Cancel</Button
						>
						<Button type="submit" disabled={detailsSaving}>
							{detailsSaving ? 'Saving…' : 'Save details'}
						</Button>
					</div>
				</form>
			</Sheet.Content>
		</Sheet.Root>
	</div>
{/if}
