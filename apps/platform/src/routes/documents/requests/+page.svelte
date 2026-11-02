<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		DropdownMenu,
		EmptyState,
		humanise,
		ListingSkeleton,
		LoadErrorBanner,
		Streamed,
		Segmented,
		StatusBadge,
		TruncatedText
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import { RequestDialog } from '@graphene/ui/book/documents';
	import { ListingOverflow } from '@graphene/ui/book/listing';
	import ListingFrame from '$lib/components/listing-frame.svelte';
	import {
		REQUEST_ACTION_LABELS,
		REQUEST_STATUS_META,
		byRequestUrgency,
		isRequestOverdue,
		requestDueLabel,
		requestProgress,
		type ApiRequest,
		type RequestAction
	} from '@graphene/ui/book/documents';
	import type { ApiDocument, ClientOption } from '@graphene/ui/book/documents';
	import type { PageData } from './$types';

	type FormOption = { uid: string; name: string; status: string };

	let {
		data,
		form
	}: { data: PageData; form: { error?: string; created?: boolean; uid?: string } | null } =
		$props();

	// The tab is the URL's, not local state: the SERVER decides which requests to send, so the
	// tab has to be part of the question asked. It also means a tab survives a reload and can be
	// linked — which local state never could.
	const tab = $derived(data.tab);

	// Already narrowed to this tab by the server. Only the urgency ORDER is applied here, because
	// it is a presentation rule (`byRequestUrgency` reads the same fields the badge does) and
	// re-filtering rows the server already selected is how a window starts hiding its own contents.
	const rowsOf = (library: { requests: ApiRequest[] }) =>
		tab === 'pending' ? library.requests.toSorted(byRequestUrgency) : library.requests;

	/** Put the tab and the search in the URL and let the loader answer them. `keepFocus` is why
	 *  typing survives the navigation it triggers; `replaceState` keeps a queue of keystrokes out
	 *  of the back button. */
	function navigate(next: { tab?: string; q?: string }): void {
		const url = new URL(page.url);
		if (next.tab !== undefined) url.searchParams.set('tab', next.tab);
		if (next.q !== undefined) {
			if (next.q) url.searchParams.set('q', next.q);
			else url.searchParams.delete('q');
		}
		goto(url, { keepFocus: true, noScroll: true, replaceState: true });
	}

	const clientLabel = (r: ApiRequest): string => r.contact_name ?? `Client #${r.contact_id}`;

	/** "Upload a passport · Agree to the client agreement" — every line, not just the first, so a
	 * four-item request never reads as one. */
	const itemSummary = (r: ApiRequest): string =>
		r.items
			.filter((i) => i.status !== 'cancelled')
			.map((i) => `${REQUEST_ACTION_LABELS[i.action as RequestAction] ?? i.action}: ${i.title}`)
			.join(' · ');

	// Opens on Priority (urgent first); ties keep the chase order `rowsOf` gave them.
	let sort = $state<DataTableSort | null>(null);

	const PRIORITY_RANK: Record<string, number> = { urgent: 0, high: 1, normal: 2, low: 3 };

	const tableLabel = $derived(
		`${tab === 'pending' ? 'Outstanding' : tab === 'fulfilled' ? 'Completed' : 'Cancelled'} requests`
	);

	const columns: DataTableColumn<ApiRequest>[] = [
		{
			key: 'client',
			header: 'Client',
			sortable: true,
			headerClass: 'w-[40%]',
			sortValue: clientLabel
		},
		{
			key: 'priority',
			header: 'Priority',
			sortable: true,
			sortDefault: true,
			hideBelowWidth: 'md',
			headerClass: 'w-[12%]',
			sortValue: (r) => PRIORITY_RANK[r.priority] ?? 2
		},
		{
			key: 'progress',
			header: 'Progress',
			sortable: true,
			align: 'center',
			defaultDir: 'desc',
			hideBelowWidth: 'lg',
			headerClass: 'w-[12%]',
			sortValue: (r) => {
				const p = requestProgress(r);
				return p.total > 0 ? p.done / p.total : -1;
			}
		},
		{
			key: 'due_date',
			header: 'Due',
			sortable: true,
			hideBelowWidth: 'md',
			headerClass: 'w-[16%]'
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[12%]',
			sortValue: (r) => REQUEST_STATUS_META[r.status].label
		}
	];

	/* ── No `?client=` preselect any more ─────────────────────────────────────
	   The document record's "Raise a request" was its only producer and that button is gone with the
	   record's Requests tab. It is deleted rather than left dormant because it was BROKEN: the link
	   carried `?client=<uid>` (a nanoid — a URL never carries a database key) while this page parsed
	   it with `Number()`, so `preselectedClient` was always null and the dialog never opened locked.
	   The one surface that still raises a request against a fixed client — the client record's
	   Documents tab — mounts the builder in place with `forClient`, which posts `contact_id` in a
	   hidden input and never travels through a URL. */

	/* ── New request ─────────────────────────────────────────────────────────── */
	// The builder itself is the shared RequestDialog; this page only owns whether it's open and, on
	// success, jumps to the new request's detail. Clients / documents / forms load when the dialog
	// opens, so the queue's first paint is only the paged list.
	let dialogOpen = $state(false);
	let clients = $state<ClientOption[]>([]);
	let documents = $state<ApiDocument[]>([]);
	let forms = $state<FormOption[]>([]);
	let pickersLoaded = $state(false);

	async function loadPickers(): Promise<void> {
		if (pickersLoaded || !data.canSend) return;
		pickersLoaded = true;
		const res = await fetch('/documents/requests/pickers');
		if (!res.ok) return;
		const body = (await res.json()) as {
			clients?: ClientOption[];
			documents?: ApiDocument[];
			forms?: FormOption[];
		};
		clients = body.clients ?? [];
		documents = body.documents ?? [];
		forms = body.forms ?? [];
	}

	$effect(() => {
		if (dialogOpen) void loadPickers();
	});
</script>

{#snippet newRequest()}
	<Button onclick={() => (dialogOpen = true)}><PlusIcon class="size-4" /> New request</Button>
{/snippet}

<svelte:head><title>Requests · Platform</title></svelte:head>

<Streamed data={data.library} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ListingSkeleton filters={0} actions={data.canSend} pager={false} />
	{/snippet}
	{#snippet children(library)}
		{@const rows = rowsOf(library)}
		{@const overdueCount = library.overdue}
		<ListingFrame>
			{#snippet banner()}
				{#if form?.error}
					<Alert variant="destructive">
						<AlertDescription>{form.error}</AlertDescription>
					</Alert>
				{/if}
				{#if library.loadError}
					<LoadErrorBanner
						message="Requests couldn’t be loaded. Try again."
						onRetry={() => invalidateAll()}
					/>
				{/if}
			{/snippet}

			<div class="flex shrink-0 flex-wrap items-center justify-between gap-3">
				<Segmented
					options={[
						{ value: 'pending', label: `Outstanding (${library.counts.pending})` },
						{ value: 'fulfilled', label: `Complete (${library.counts.fulfilled})` },
						{ value: 'cancelled', label: `Cancelled (${library.counts.cancelled})` }
					]}
					value={tab}
					onValueChange={(v) => navigate({ tab: v })}
				/>
				<div class="ml-auto flex items-center gap-2">
					{#if overdueCount > 0}
						<Badge variant="destructive">{overdueCount} overdue</Badge>
					{/if}
					{#if data.canSend}
						<ListingOverflow>
							<DropdownMenu.Item onclick={() => (dialogOpen = true)}>New request</DropdownMenu.Item>
						</ListingOverflow>
					{/if}
				</div>
			</div>

			{#if rows.length === 0}
				<EmptyState
					icon={ClipboardListIcon}
					title={tab === 'pending'
						? 'Nothing outstanding'
						: tab === 'fulfilled'
							? 'Nothing completed yet'
							: 'Nothing cancelled'}
					description={tab === 'pending' && data.canSend
						? 'Ask a client for a document, agreement or form. They will see it in their portal.'
						: undefined}
					action={tab === 'pending' && data.canSend ? newRequest : undefined}
					compact
					chromeless
				/>
			{:else}
				<DataTable {columns} {rows} bind:sort scroll fixed getKey={(r) => r.uid} label={tableLabel}>
					{#snippet cell(r, column)}
						{@const overdue = isRequestOverdue(r)}
						{#if column.key === 'client'}
							<div class="flex min-w-0 items-center gap-3">
								<div
									class="flex size-8 shrink-0 items-center justify-center rounded-lg {r.status ===
									'fulfilled'
										? 'bg-success/10 text-success'
										: overdue
											? 'bg-destructive/10 text-destructive'
											: 'bg-muted text-muted-foreground'}"
								>
									<ClipboardListIcon class="size-4" />
								</div>
								<div class="min-w-0 flex-1">
									<TruncatedText
										text={clientLabel(r)}
										href={`/documents/requests/${r.uid}`}
										class="text-xs font-medium"
									/>
									<p class="text-muted-foreground truncate text-xs">
										{itemSummary(r) || 'No items'}
									</p>
								</div>
							</div>
						{:else if column.key === 'priority'}
							{#if r.priority === 'urgent' || r.priority === 'high'}
								<Badge variant="destructive">{humanise(r.priority)}</Badge>
							{/if}
						{:else if column.key === 'progress'}
							{@const prog = requestProgress(r)}
							{#if prog.total > 0}
								<span class="text-muted-foreground text-xs tabular-nums">
									{prog.done}/{prog.total} done
								</span>
							{/if}
						{:else if column.key === 'due_date'}
							{#if r.due_date && r.status === 'pending'}
								<span
									class="text-xs {overdue
										? 'text-destructive font-medium'
										: 'text-muted-foreground'}"
								>
									{requestDueLabel(r)}
								</span>
							{/if}
						{:else if column.key === 'status'}
							{@const meta = REQUEST_STATUS_META[r.status]}
							<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
						{/if}
					{/snippet}
				</DataTable>
			{/if}
		</ListingFrame>
	{/snippet}
</Streamed>

<!-- Ask a client for something — the builder is shared with the client-record Documents tab. On
     success the list revalidates and we jump to the new request's detail. -->
<!-- No `forClient` here: the hub always shows the picker. Locking the target to one client is the
     client RECORD's job, and its Documents tab mounts this same builder with `forClient` set. (This
     briefly posted to `?/createForClient` when a client was locked — an action that does not exist,
     so the feature answered 404 while every test passed, because they invoke `actions.create`
     directly and never go through this attribute. ONE action, from every host.) -->
<RequestDialog
	{enhance}
	invalidate={invalidateAll}
	bind:open={dialogOpen}
	action="?/create"
	{clients}
	{documents}
	{forms}
	onCreated={(uid) => (uid ? goto(`/documents/requests/${uid}`) : undefined)}
/>
