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
		Dialog,
		humanise,
		Input,
		Label,
		LoadErrorBanner,
		PageHeader,
		SelectMenu,
		StatusBadge,
		Textarea,
		toast
	} from '@graphene/ui';
	import { untrack } from 'svelte';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import FileSignatureIcon from '@lucide/svelte/icons/file-signature';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import UserIcon from '@lucide/svelte/icons/user';
	import { formatDate } from '@graphene/ui';
	import {
		REQUEST_ACTION_LABELS,
		REQUEST_STATUS_META,
		isRequestOverdue,
		requestDueLabel,
		requestProgress,
		type ApiDocument,
		type ApiRequest,
		type ApiRequestItem,
		type RequestAction
	} from '@graphene/ui/book/documents';
	import type { SubmitFunction } from '@sveltejs/kit';

	let {
		record,
		canManage,
		canSend,
		form
	}: {
		record: { request: ApiRequest | null; documents: ApiDocument[]; loadError: boolean };
		canManage: boolean;
		canSend: boolean;
		form: { error?: string } | null;
	} = $props();

	const request = $derived(record.request);

	const ACTION_ICON: Record<RequestAction, typeof FileTextIcon> = {
		upload: UploadIcon,
		agree: FileSignatureIcon,
		acknowledge: CheckIcon,
		complete_form: ListChecksIcon
	};

	const documentItems = $derived(
		record.documents
			.filter((d) => d.status === 'generated')
			.map((d) => ({ value: d.uid, label: d.name ?? 'Untitled document' }))
	);

	const baseColumns: DataTableColumn<ApiRequestItem>[] = [
		{ key: 'title', header: 'Item', sortable: true, headerClass: 'w-[40%]' },
		{ key: 'linked', header: 'Linked document', hideBelowWidth: 'md', headerClass: 'w-[30%]' },
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[15%]',
			sortValue: (i) => REQUEST_STATUS_META[i.status].label
		}
	];
	// Re-pointing is documents:send (release), not documents:write.
	const columns = $derived<DataTableColumn<ApiRequestItem>[]>(
		canSend
			? [
					...baseColumns,
					{ key: 'actions', header: '', align: 'right', headerClass: 'w-[15%] min-w-56' }
				]
			: baseColumns
	);

	/* ── Lifecycle actions ───────────────────────────────────────────────────── */
	let cancelOpen = $state(false);
	let cancelFormEl: HTMLFormElement | null = null;

	// Due date: a small inline form rather than a dialog — one field, applied immediately. Seeded
	// once from the load (untracked), so a reload after saving never fights what's in the input.
	let dueDate = $state(untrack(() => record.request?.due_date ?? ''));
	let savingDue = $state(false);
	// Shared by the Save and Remove-deadline forms (both hit ?/dueDate).
	const saveDue: SubmitFunction = () => {
		savingDue = true;
		return async ({ result, update }) => {
			savingDue = false;
			if (result.type === 'success') {
				toast.success('Deadline updated.');
				await invalidateAll();
			} else {
				toast.error(
					result.type === 'failure'
						? String(result.data?.error ?? 'Something went wrong.')
						: 'Something went wrong.'
				);
				await update();
			}
		};
	};

	// Reassign: swap the document an agree/acknowledge item points at. Compliance-sensitive — the
	// API keeps the prior consent as a superseded fact, and the reason lands on the audit trail.
	let reassignOpen = $state(false);
	let reassignItem = $state<ApiRequestItem | null>(null);
	let reassignTarget = $state('');
	let reassigning = $state(false);
	function openReassign(item: ApiRequestItem): void {
		reassignItem = item;
		reassignTarget = '';
		reassignOpen = true;
	}
</script>

<svelte:head><title>Request · Platform</title></svelte:head>

<div class="mx-auto w-full max-w-3xl">
	<PageHeader
		title={request?.contact_name ?? 'Request'}
		description="What you asked this client for, and where each line stands."
		back={{ label: 'Requests', href: '/documents/requests' }}
	>
		{#snippet actions()}
			<!-- The title IS the client's name, and an h1 cannot be a link — so the way to their record
			     is an action rather than the heading. Shown whatever the request's state: a cancelled
			     or completed request is still about a person the reader may want to open. -->
			{#if request?.contact_uid}
				<Button variant="ghost" size="sm" href={`/clients/${request.contact_uid}`}>
					<UserIcon class="mr-1 size-4" />
					Open client
				</Button>
			{/if}
			{#if request && request.status === 'pending' && canManage}
				<form
					method="POST"
					action="?/cancel"
					use:enhance={() => {
						return async ({ result, update }) => {
							if (result.type === 'success') {
								toast.success('Request cancelled.');
								await invalidateAll();
							} else {
								toast.error(
									result.type === 'failure'
										? String(result.data?.error ?? 'Something went wrong.')
										: 'Something went wrong.'
								);
								await update();
							}
						};
					}}
				>
					<Button
						type="submit"
						variant="outline"
						class="text-destructive"
						onclick={(e: MouseEvent) => {
							e.preventDefault();
							cancelFormEl = (e.currentTarget as HTMLButtonElement).form;
							cancelOpen = true;
						}}
					>
						Cancel request
					</Button>
				</form>
			{/if}
		{/snippet}
	</PageHeader>

	{#if record.loadError}
		<LoadErrorBanner
			message="Some request details couldn’t be loaded. Try again."
			onRetry={() => invalidateAll()}
			class="mb-4"
		/>
	{/if}

	{#if form?.error}
		<Alert variant="destructive" class="mb-4">
			<AlertDescription>{form.error}</AlertDescription>
		</Alert>
	{/if}

	{#if request}
		{@const prog = requestProgress(request)}
		{@const meta = REQUEST_STATUS_META[request.status]}
		{@const overdue = isRequestOverdue(request)}
		<div class="space-y-5">
			<!-- Header facts -->
			<Card glass>
				<CardContent class="space-y-3 text-xs">
					<div class="flex flex-wrap items-center gap-2">
						<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
						<Badge variant="outline">{humanise(request.priority)}</Badge>
						{#if prog.total > 0}
							<span class="text-muted-foreground text-xs tabular-nums">
								{prog.done}/{prog.total} done
							</span>
						{/if}
						{#if request.due_date && request.status === 'pending'}
							<span
								class="text-xs {overdue ? 'text-destructive font-medium' : 'text-muted-foreground'}"
							>
								{requestDueLabel(request)}
							</span>
						{/if}
					</div>
					<div class="flex justify-between gap-3">
						<span class="text-muted-foreground">Raised</span>
						<span>{formatDate(request.created_at)}</span>
					</div>
					<div class="flex justify-between gap-3">
						<span class="text-muted-foreground">Due</span>
						<span>{request.due_date ? formatDate(request.due_date) : 'No deadline'}</span>
					</div>
					{#if request.message}
						<div>
							<p class="text-muted-foreground">Covering note</p>
							<p class="mt-1 whitespace-pre-line">{request.message}</p>
						</div>
					{/if}

					{#if request.status === 'pending' && canManage}
						<!-- Only pending requests are editable (the API 409s otherwise). Clearing posts its own
						     empty value rather than blanking the bound input first, so what is submitted is
						     never a frame behind what was clicked. -->
						<div class="flex flex-wrap items-end gap-2 border-t pt-3">
							<form
								method="POST"
								action="?/dueDate"
								class="flex flex-wrap items-end gap-2"
								use:enhance={saveDue}
							>
								<div class="space-y-1.5">
									<Label for="due">Change the deadline</Label>
									<Input id="due" name="due_date" type="date" bind:value={dueDate} class="w-44" />
								</div>
								<Button type="submit" variant="outline" disabled={savingDue}>
									{savingDue ? 'Saving…' : 'Save'}
								</Button>
							</form>
							{#if request.due_date}
								<form method="POST" action="?/dueDate" use:enhance={saveDue}>
									<input type="hidden" name="due_date" value="" />
									<Button
										type="submit"
										variant="ghost"
										class="text-muted-foreground"
										disabled={savingDue}
									>
										Remove deadline
									</Button>
								</form>
							{/if}
						</div>
					{/if}
				</CardContent>
			</Card>

			<!-- Line items — each one fulfilled independently by the client. -->
			<Card glass class="gap-0 py-0">
				<CardHeader class="border-b px-5 py-4">
					<CardTitle class="text-base">What you asked for</CardTitle>
				</CardHeader>
				<CardContent class="p-0">
					<DataTable
						{columns}
						rows={request.items}
						getKey={(item) => item.uid}
						label="What you asked for"
						empty="This request has no items."
						fixed
					>
						{#snippet cell(item, column)}
							{@const Icon = ACTION_ICON[item.action] ?? ClipboardListIcon}
							{@const itemMeta = REQUEST_STATUS_META[item.status]}
							{#if column.key === 'title'}
								<div class="flex min-w-0 items-center gap-3">
									<div
										class="flex size-9 shrink-0 items-center justify-center rounded-lg {item.status ===
										'fulfilled'
											? 'bg-success/10 text-success'
											: item.status === 'cancelled'
												? 'bg-muted text-muted-foreground'
												: 'bg-warning/10 text-warning'}"
									>
										<Icon class="size-4" />
									</div>
									<div class="min-w-0 flex-1">
										<p class="truncate text-xs font-medium">{item.title}</p>
										<p class="text-muted-foreground text-xs">
											{REQUEST_ACTION_LABELS[item.action]}
										</p>
									</div>
								</div>
							{:else if column.key === 'linked'}
								{#if item.target_document_uid || item.fulfilled_document_uid}
									<div class="space-y-1 text-xs">
										{#if item.target_document_uid}
											<p>
												<span class="text-muted-foreground">Acting on </span>
												<a href={`/documents/${item.target_document_uid}`} class="hover:underline">
													the attached document
												</a>
											</p>
										{/if}
										{#if item.fulfilled_document_uid}
											<p>
												<span class="text-muted-foreground">Satisfied by </span>
												<a
													href={`/documents/${item.fulfilled_document_uid}`}
													class="hover:underline"
												>
													the document they provided
												</a>
											</p>
										{/if}
									</div>
								{:else}
									<span class="text-muted-foreground text-xs">—</span>
								{/if}
							{:else if column.key === 'status'}
								<StatusBadge tone={itemMeta.tone}>{itemMeta.label}</StatusBadge>
							{:else if column.key === 'actions'}
								{#if (item.action === 'agree' || item.action === 'acknowledge') && item.status !== 'cancelled'}
									<Button variant="ghost" size="sm" onclick={() => openReassign(item)}>
										Attach a different document
									</Button>
								{/if}
							{/if}
						{/snippet}
					</DataTable>
				</CardContent>
			</Card>

			<p class="text-muted-foreground text-xs">
				Tell the client separately if you cancel or reschedule this request. They will see the
				change when they next open their portal.
			</p>
		</div>
	{/if}
</div>

<ConfirmDialog
	bind:open={cancelOpen}
	title="Cancel this request?"
	description="Every line the client hasn't completed disappears from their portal. Lines they have already done stay on the record."
	confirmLabel="Cancel request"
	cancelLabel="Keep it"
	destructive
	onconfirm={() => cancelFormEl?.requestSubmit()}
/>

<!-- Reassign an agree/acknowledge target. Re-opens a fulfilled line and supersedes prior consent. -->
<Dialog.Root bind:open={reassignOpen}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Attach a different document</Dialog.Title>
			<Dialog.Description>
				{reassignItem?.status === 'fulfilled'
					? 'This line has already been agreed. Swapping the document re-opens it for the client, and their earlier consent is kept on the trail as superseded.'
					: 'The client will be asked to act on the document you choose here instead.'}
			</Dialog.Description>
		</Dialog.Header>
		<form
			method="POST"
			action="?/reassign"
			class="space-y-4"
			use:enhance={() => {
				reassigning = true;
				return async ({ result, update }) => {
					reassigning = false;
					if (result.type === 'success') {
						reassignOpen = false;
						toast.success('Document reattached. The client has been asked again.');
						await invalidateAll();
					} else {
						toast.error(
							result.type === 'failure'
								? String(result.data?.error ?? 'Something went wrong.')
								: 'Something went wrong.'
						);
						await update({ reset: false });
					}
				};
			}}
		>
			<input type="hidden" name="item_uid" value={reassignItem?.uid ?? ''} />
			<div class="space-y-1.5">
				<Label for="reassign-target">Document</Label>
				<SelectMenu
					id="reassign-target"
					name="target_document_uid"
					required
					placeholder={documentItems.length ? 'Choose a document…' : 'No documents available'}
					items={documentItems}
					bind:value={reassignTarget}
				/>
			</div>
			<div class="space-y-1.5">
				<Label for="reassign-reason">
					Reason <span class="text-muted-foreground">(recorded on the audit trail)</span>
				</Label>
				<Textarea
					id="reassign-reason"
					name="reason"
					rows={2}
					placeholder="e.g. The wrong illustration was attached."
				/>
			</div>
			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => (reassignOpen = false)}
					>Cancel</Button
				>
				<Button type="submit" disabled={reassigning || !reassignTarget}>
					{reassigning ? 'Saving…' : 'Reattach'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
