<script lang="ts">
	import { enhance } from '$app/forms';
	import {
		Button,
		Card,
		CardContent,
		DataTable,
		Input,
		Label,
		PageHeader,
		SelectMenu,
		type DataTableColumn,
		withToast
	} from '@graphene/ui';
	import { REQUEST_PRIORITIES, REQUEST_STATUS_META } from '@graphene/ui/book/documents';
	import {
		ListingToolbar,
		humanise,
		matches,
		search,
		type FilterDef
	} from '@graphene/ui/book/listing';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	type Row = PageData['requests'][number];
	/** Severity order, matching the platform requests listing. */
	const PRIORITY_RANK: Record<string, number> = { urgent: 0, high: 1, normal: 2, low: 3 };
	const columns: DataTableColumn<Row>[] = [
		{ key: 'contact_name', header: 'Client', sortable: true, sortDefault: true },
		{ key: 'status', header: 'Status', sortable: true },
		{
			key: 'priority',
			header: 'Priority',
			sortable: true,
			sortValue: (r) => PRIORITY_RANK[r.priority] ?? 2
		},
		// No `defaultDir`: a due date opens soonest first.
		{ key: 'due_date', header: 'Due', sortable: true }
	];
	const clientItems = $derived(
		data.clients.map((client) => ({
			value: String(client.contact_id),
			label: client.name ?? client.email ?? `Client ${client.contact_id}`
		}))
	);

	// CLIENT-SIDE, unlike the deleted-documents listing beside this one: `+page.server.ts` already
	// fetches every request for this firm in one call (no `limit`), so there is no page in hand to
	// lie about — filtering the array we already hold in full is telling the truth, not a shortcut.
	let query = $state('');
	// MULTI: "High and Urgent" — everything that isn't routine — is a real question here.
	let statusFilter = $state<string[]>([]);
	let priorityFilter = $state<string[]>([]);

	// The full three/four-value vocabularies, not `optionsFrom(data.requests, …)` — these are closed
	// enums (`documents.requests.status`, `.priority`), so a firm with no cancelled requests today
	// should still be able to filter FOR "cancelled" rather than have the option vanish because
	// nothing currently matches it.
	const STATUS_OPTIONS = (Object.keys(REQUEST_STATUS_META) as Row['status'][]).map((value) => ({
		value,
		label: REQUEST_STATUS_META[value].label
	}));
	const PRIORITY_OPTIONS = REQUEST_PRIORITIES.map((value) => ({ value, label: humanise(value) }));

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'status', label: 'Status', options: STATUS_OPTIONS },
		{ key: 'priority', label: 'Priority', options: PRIORITY_OPTIONS }
	]);
	const filterValue = $derived<Record<string, string[]>>({
		...(statusFilter.length ? { status: statusFilter } : {}),
		...(priorityFilter.length ? { priority: priorityFilter } : {})
	});
	function onRequestFilters(next: Record<string, string[]>) {
		statusFilter = next.status ?? [];
		priorityFilter = next.priority ?? [];
	}

	const dirty = $derived(statusFilter.length > 0 || priorityFilter.length > 0);
	function clear() {
		statusFilter = [];
		priorityFilter = [];
	}

	const rows = $derived(
		data.requests.filter(
			(r) =>
				matches(statusFilter, r.status) &&
				matches(priorityFilter, r.priority) &&
				search(query, r.contact_name, r.message)
		)
	);
</script>

<svelte:head><title>Document requests · {data.tenant.name}</title></svelte:head>
<div class="space-y-5">
	<PageHeader
		title="Document requests"
		description="What this firm has asked its clients for, and what they have sent back."
		back={{ href: `/tenants/${data.tenant.uid}/operate/documents`, label: 'Documents' }}
	/>
	{#if data.writing}
		<Card glass>
			<CardContent>
				<form
					method="POST"
					action="?/create"
					use:enhance={withToast('Request sent')}
					class="grid gap-3 md:grid-cols-2"
				>
					<div class="space-y-1.5">
						<Label for="contact_id">Client</Label>
						<SelectMenu id="contact_id" name="contact_id" items={clientItems} />
					</div>
					<div class="space-y-1.5">
						<Label for="title">What is needed?</Label>
						<Input id="title" name="title" placeholder="Proof of address" />
					</div>
					<div class="space-y-1.5">
						<Label for="due_date">Due date</Label>
						<Input id="due_date" name="due_date" type="date" />
					</div>
					<div class="space-y-1.5">
						<Label for="priority">Priority</Label>
						<SelectMenu
							id="priority"
							name="priority"
							value="normal"
							items={PRIORITY_OPTIONS.filter((o) => o.value !== 'low')}
						/>
					</div>
					<Button type="submit" class="md:col-span-2 md:w-fit">Send request</Button>
				</form>
			</CardContent>
		</Card>
	{/if}
	<ListingToolbar
		bind:query
		placeholder="Search by client or message"
		{dirty}
		onclear={clear}
		{filterDefs}
		{filterValue}
		onfilters={onRequestFilters}
	/>
	<DataTable
		{rows}
		{columns}
		getKey={(row) => row.uid}
		label="Document requests"
		empty={dirty || query ? 'No requests match your filters.' : 'No document requests.'}
	>
		{#snippet cell(row, column)}
			{#if column.key === 'contact_name'}
				<a
					class="font-medium underline-offset-4 hover:underline"
					href={`/tenants/${data.tenant.uid}/operate/documents/requests/${row.uid}`}
				>
					{row.contact_name ?? `Client ${row.contact_id}`}
				</a>
			{:else if column.key === 'due_date'}
				{row.due_date ? new Date(row.due_date).toLocaleDateString('en-GB') : '—'}
			{:else}
				{String(row[column.key as keyof Row] ?? '—')}
			{/if}
		{/snippet}
	</DataTable>
</div>
