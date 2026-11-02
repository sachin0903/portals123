<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		ConfirmDialog,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		DynamicForm,
		EmptyState,
		FORM_STATUS_META,
		formatFieldValue,
		formDataSources,
		type FormField,
		humanise,
		RecordId,
		Sheet,
		StatusBadge,
		Tabs,
		toast
	} from '@graphene/ui';
	import { deserialize, enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import CheckCircleIcon from '@lucide/svelte/icons/check-circle-2';
	import ArchiveIcon from '@lucide/svelte/icons/archive';
	import ArchiveRestoreIcon from '@lucide/svelte/icons/archive-restore';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import BackButton from '$lib/components/back-button.svelte';
	import InboxIcon from '@lucide/svelte/icons/inbox';
	import { formatDate } from '@graphene/ui';
	import type { FormResponseRow } from '$lib/forms';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const form = $derived(data.form);
	const fields = $derived(data.fields);
	const responses = $derived(data.responses);
	const canManage = $derived(data.canManage);

	// Label + formatted value for one answer, reusing the shared field formatter (currency, dates,
	// select labels, …) when the field is known; falls back to the raw key/value otherwise.
	function answerLabel(key: string): string {
		return fields.find((f) => f.fieldKey === key)?.label || key;
	}
	function answerValue(key: string, value: unknown): string {
		const field = fields.find((f) => f.fieldKey === key);
		const formatted = field ? formatFieldValue({ value, field, dataSources: formDataSources }) : '';
		if (formatted) return formatted;
		if (value == null || value === '') return '—';
		return typeof value === 'object' ? JSON.stringify(value) : String(value);
	}

	let tab = $state('preview');
	let values = $state<Record<string, unknown>>({});
	let submitted = $state<Record<string, unknown> | null>(null);
	let submitError = $state<string | null>(null);

	// Pending + confirm state for the header actions.
	let publishing = $state(false);
	let unarchiving = $state(false);
	let archiving = $state(false);
	let archiveOpen = $state(false);
	let archiveFormEl = $state<HTMLFormElement | null>(null);

	async function handleSubmit(v: Record<string, unknown>) {
		submitError = null;
		const body = new FormData();
		body.append('answers', JSON.stringify(v));
		try {
			const res = await fetch('?/submitResponse', {
				method: 'POST',
				body,
				headers: { 'x-sveltekit-action': 'true' }
			});
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				submitted = v;
				await invalidateAll(); // reflect the new submission in the Responses tab
			} else if (result.type === 'failure') {
				submitError = String(
					result.data?.error ?? 'Could not record the response. Please try again.'
				);
			} else {
				submitError = 'Could not record the response. Please try again.';
			}
		} catch {
			submitError = 'Could not record the response. Please try again.';
		}
	}

	function fieldTypeLabel(t: string): string {
		return t.replace(/([A-Z])/g, ' $1').replace(/^./, (c) => c.toUpperCase());
	}
	// Only Order sorts: `fields` comes in render order, and no other order exists elsewhere.
	let fieldsSort = $state<DataTableSort | null>(null);
	const fieldOrder = $derived(new Map(fields.map((f, i) => [f.uid, i + 1])));
	const fieldsColumns: DataTableColumn<FormField>[] = [
		{
			key: 'order',
			header: 'Order',
			sortable: true,
			sortDefault: true,
			align: 'center',
			headerClass: 'w-[10%]',
			sortValue: (f) => fieldOrder.get(f.uid) ?? f.sortOrder
		},
		{ key: 'field', header: 'Field', headerClass: 'w-[32%]' },
		{ key: 'section', header: 'Section', hideBelowWidth: 'md', headerClass: 'w-[16%]' },
		{ key: 'type', header: 'Type', headerClass: 'w-[16%]' },
		{ key: 'conditional', header: 'Conditional', headerClass: 'w-[14%]' },
		{ key: 'required', header: 'Required', headerClass: 'w-[12%]' }
	];

	let responsesSort = $state<DataTableSort | null>(null);
	let responseDetailOpen = $state(false);
	let responseDetail = $state<FormResponseRow | null>(null);
	function openResponse(r: FormResponseRow): void {
		responseDetail = r;
		responseDetailOpen = true;
	}
	const viaLabel = (r: FormResponseRow) => (r.via === 'request' ? 'From request' : 'Standalone');
	const desc = { sortable: true, defaultDir: 'desc' } as const;
	const responsesColumns: DataTableColumn<FormResponseRow>[] = [
		{ key: 'submittedBy', header: 'Submitted by', sortable: true, headerClass: 'w-[32%]' },
		{ ...desc, key: 'submittedAt', header: 'Submitted', headerClass: 'w-[22%]' },
		{
			key: 'via',
			header: 'Via',
			sortable: true,
			headerClass: 'w-[24%]',
			sortValue: viaLabel
		},
		{
			...desc,
			key: 'answers',
			header: 'Answers',
			align: 'center',
			headerClass: 'w-[22%]',
			sortValue: (r) => Object.keys(r.answers).length
		}
	];
</script>

<svelte:head><title>{form ? form.name : 'Form'} · Platform</title></svelte:head>

{#if !form}
	<div class="mx-auto max-w-3xl">
		<p class="text-muted-foreground text-sm">This form could not be found.</p>
		<Button variant="outline" class="mt-4" href="/forms">
			<ArrowLeftIcon class="mr-1.5 size-4" /> Back to forms
		</Button>
	</div>
{:else}
	{@const meta = FORM_STATUS_META[form.status]}
	<div class="space-y-6">
		<div>
			<BackButton fallback="/forms" fallbackLabel="forms" />
			<div class="mt-2 flex items-start justify-between gap-4">
				<div>
					<div class="flex items-center gap-2">
						<h1 class="text-2xl font-semibold">{form.name}</h1>
						<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
					</div>
					<p class="text-muted-foreground mt-1 font-mono text-sm">{form.code}</p>
					{#if form.description}<p class="text-muted-foreground mt-2 text-sm">
							{form.description}
						</p>{/if}
				</div>
				<div class="flex shrink-0 items-center gap-2">
					{#if form.status === 'draft' && canManage}
						<form
							method="POST"
							action="?/publish"
							use:enhance={() => {
								publishing = true;
								return async ({ result }) => {
									publishing = false;
									if (result.type === 'success') {
										toast.success('Form published.');
										await invalidateAll();
									} else {
										toast.error(
											result.type === 'failure'
												? String(result.data?.error ?? 'Could not publish the form.')
												: 'Could not publish the form.'
										);
									}
								};
							}}
						>
							<Button type="submit" variant="outline" disabled={publishing}>
								<CheckCircleIcon class="mr-1.5 size-4" />
								{publishing ? 'Publishing…' : 'Publish'}
							</Button>
						</form>
					{/if}
					{#if canManage && form.status !== 'archived'}
						<Button href={`/forms/${form.uid}/edit`}>
							<PencilIcon class="mr-1.5 size-4" /> Edit
						</Button>
					{/if}
					{#if canManage && form.status === 'archived'}
						<form
							method="POST"
							action="?/unarchive"
							use:enhance={() => {
								unarchiving = true;
								return async ({ result }) => {
									unarchiving = false;
									if (result.type === 'success') {
										toast.success('Form restored.');
										await invalidateAll();
									} else {
										toast.error(
											result.type === 'failure'
												? String(result.data?.error ?? 'Could not restore the form.')
												: 'Could not restore the form.'
										);
									}
								};
							}}
						>
							<Button type="submit" variant="outline" disabled={unarchiving}>
								<ArchiveRestoreIcon class="mr-1.5 size-4" />
								{unarchiving ? 'Restoring…' : 'Un-archive'}
							</Button>
						</form>
					{/if}
					{#if canManage && form.status !== 'archived'}
						<form
							method="POST"
							action="?/archive"
							bind:this={archiveFormEl}
							use:enhance={() => {
								archiving = true;
								return async ({ result }) => {
									archiving = false;
									if (result.type === 'success') {
										toast.success('Form archived.');
										await goto('/forms');
									} else {
										toast.error(
											result.type === 'failure'
												? String(result.data?.error ?? 'Could not archive the form.')
												: 'Could not archive the form.'
										);
									}
								};
							}}
						>
							<Button
								type="button"
								variant="ghost"
								class="text-muted-foreground hover:text-destructive"
								disabled={archiving}
								onclick={() => (archiveOpen = true)}
							>
								<ArchiveIcon class="mr-1.5 size-4" />
								{archiving ? 'Archiving…' : 'Archive'}
							</Button>
						</form>
					{/if}
				</div>
			</div>
			<div class="mt-3 flex flex-wrap items-center gap-2">
				<Badge variant="outline">Applies to {humanise(form.entityType)}</Badge>
				<Badge variant="outline">{fields.length} fields</Badge>
			</div>
		</div>

		<Tabs.Root bind:value={tab}>
			<Tabs.List>
				<Tabs.Trigger value="preview">Preview</Tabs.Trigger>
				<Tabs.Trigger value="fields">Fields ({fields.length})</Tabs.Trigger>
				<!-- Responses are CLIENT data: the tab needs forms:submit or documents:read (the
				     six-splits re-key) — a definition-only viewer doesn't get an empty tab that
				     pretends nothing came back. -->
				{#if data.canReadResponses}
					<Tabs.Trigger value="responses">Responses ({responses.length})</Tabs.Trigger>
				{/if}
			</Tabs.List>

			<Tabs.Content value="preview" class="space-y-4 pt-4">
				{#if fields.length === 0}
					<EmptyState
						title="This form has no fields yet"
						description="Add fields in the builder to preview the form."
					>
						{#snippet action()}
							<Button variant="outline" size="sm" href={`/forms/${form.uid}/edit`}>
								<PencilIcon class="mr-1.5 size-4" /> Add fields
							</Button>
						{/snippet}
					</EmptyState>
				{:else}
					<!-- Google-Forms-style header: the form's title (+ description) leads the preview,
					     exactly as someone filling it in would see it. -->
					<Card glass class="border-t-brand overflow-hidden border-t-4">
						<CardContent>
							<h2 class="text-xl font-semibold">{form.name}</h2>
							{#if form.description}
								<p class="text-muted-foreground mt-1 text-sm">{form.description}</p>
							{/if}
						</CardContent>
					</Card>
					{#if submitted}
						{@const answerCount = Object.keys(submitted).length}
						<Alert variant="success">
							<CheckCircleIcon class="size-4" />
							<AlertDescription>
								Test submission recorded with {answerCount}
								{answerCount === 1 ? 'answer' : 'answers'} captured.
							</AlertDescription>
						</Alert>
					{/if}
					{#if submitError}
						<Alert variant="destructive">
							<AlertDescription>{submitError}</AlertDescription>
						</Alert>
					{/if}
					<!-- Recording a test response is forms:submit (the response plane, split out of
					     forms:write) — without it the preview stays a preview: no submit button, and no
					     handler behind a stray Enter key. -->
					<DynamicForm
						{fields}
						bind:values
						dataSources={formDataSources}
						submitLabel="Submit response"
						showSubmit={data.canSubmit}
						onsubmit={data.canSubmit ? handleSubmit : undefined}
					/>
				{/if}
			</Tabs.Content>

			<Tabs.Content value="fields" class="pt-4">
				{#if fields.length === 0}
					<EmptyState compact title="No fields yet" />
				{:else}
					<Card glass class="gap-2">
						<CardHeader><CardTitle>Fields</CardTitle></CardHeader>
						<CardContent class="p-0">
							<DataTable
								columns={fieldsColumns}
								rows={fields}
								bind:sort={fieldsSort}
								fixed
								getKey={(field) => field.uid}
								label="Fields"
							>
								{#snippet cell(field, column)}
									{#if column.key === 'order'}
										<span class="text-muted-foreground text-xs tabular-nums">
											{fieldOrder.get(field.uid)}
										</span>
									{:else if column.key === 'field'}
										<div class="min-w-0">
											<p class="truncate text-xs font-medium">{field.label}</p>
											<RecordId value={field.fieldKey} label="Key" class="mt-0.5" />
										</div>
									{:else if column.key === 'section'}
										<span class="text-muted-foreground text-xs">{field.section || '—'}</span>
									{:else if column.key === 'type'}
										<Badge variant="outline" class="font-normal"
											>{fieldTypeLabel(field.fieldType)}</Badge
										>
									{:else if column.key === 'conditional'}
										{#if field.conditions}
											<Badge variant="outline" class="font-normal">Conditional</Badge>
										{:else}
											<span class="text-muted-foreground text-xs">—</span>
										{/if}
									{:else if column.key === 'required'}
										<StatusBadge tone={field.required ? 'success' : 'neutral'}>
											{field.required ? 'Required' : 'Optional'}
										</StatusBadge>
									{/if}
								{/snippet}
							</DataTable>
						</CardContent>
					</Card>
				{/if}
			</Tabs.Content>

			<Tabs.Content value="responses" class="space-y-3 pt-4">
				{#if !data.canReadResponses}
					<!-- Unreachable via the (hidden) trigger; belt and braces for a stale tab state. -->
					<EmptyState
						icon={InboxIcon}
						title="Responses need another permission"
						description="Ask a firm administrator for access to client responses."
					/>
				{:else if responses.length === 0}
					<EmptyState
						icon={InboxIcon}
						title="No responses yet"
						description="Submissions appear here when a client completes a request for this form, or when it's filled from the preview above."
					/>
				{:else}
					<Card glass class="gap-2">
						<CardHeader><CardTitle>Responses</CardTitle></CardHeader>
						<CardContent class="p-0">
							<DataTable
								columns={responsesColumns}
								rows={responses}
								bind:sort={responsesSort}
								fixed
								getKey={(r) => r.uid}
								label="Responses"
								onRowClick={openResponse}
							>
								{#snippet cell(r, column)}
									{#if column.key === 'submittedBy'}
										<span class="truncate text-xs font-medium">{r.submittedBy ?? 'Unknown'}</span>
									{:else if column.key === 'submittedAt'}
										<span class="text-muted-foreground text-xs">{formatDate(r.submittedAt)}</span>
									{:else if column.key === 'via'}
										<Badge variant="outline" class="font-normal">{viaLabel(r)}</Badge>
									{:else if column.key === 'answers'}
										<span class="text-muted-foreground text-xs tabular-nums">
											{Object.keys(r.answers).length}
										</span>
									{/if}
								{/snippet}
							</DataTable>
						</CardContent>
					</Card>
				{/if}
			</Tabs.Content>
		</Tabs.Root>
	</div>

	<ConfirmDialog
		bind:open={archiveOpen}
		title="Archive this form?"
		description={`“${form.name}” will move to the archived view. You can restore it later.`}
		confirmLabel="Archive"
		destructive
		onconfirm={() => archiveFormEl?.requestSubmit()}
	/>
	<Sheet.Root bind:open={responseDetailOpen}>
		<Sheet.Content class="flex flex-col gap-0">
			{#if responseDetail}
				<Sheet.Header class="border-b px-6 pt-6 pb-4">
					<Sheet.Title>{responseDetail.submittedBy ?? 'Unknown'}</Sheet.Title>
					<div class="mt-1 flex items-center gap-2">
						<span class="text-muted-foreground text-xs">
							{formatDate(responseDetail.submittedAt)}
						</span>
						<Badge variant="outline" class="shrink-0">{viaLabel(responseDetail)}</Badge>
					</div>
				</Sheet.Header>
				<div class="min-h-0 flex-1 overflow-y-auto px-6 py-5">
					<dl class="divide-y text-xs">
						{#each Object.entries(responseDetail.answers) as [key, value] (key)}
							<div class="grid grid-cols-[minmax(0,12rem)_1fr] gap-3 py-2">
								<dt class="text-muted-foreground truncate text-xs">{answerLabel(key)}</dt>
								<dd class="min-w-0 break-words">{answerValue(key, value)}</dd>
							</div>
						{/each}
					</dl>
				</div>
			{/if}
		</Sheet.Content>
	</Sheet.Root>
{/if}
