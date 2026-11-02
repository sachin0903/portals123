<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import {
		Alert,
		AlertDescription,
		Button,
		Card,
		CardContent,
		Checkbox,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		Dialog,
		EmptyState,
		Input,
		Label,
		PageHeader,
		RecordId,
		SectionHeading,
		StatusBadge,
		toast
	} from '@graphene/ui';
	import { ALL, ListingToolbar, matches, type FilterDef } from '@graphene/ui/book/listing';
	import BuildingIcon from '@lucide/svelte/icons/building-2';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import type { PageData, ActionData } from './$types';
	import type { TenantSummary } from '$lib/api';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	let open = $state(false);
	// The search is the URL's, so the API answers it. Filtering `data.tenants` here would search
	// the window the loader happened to fetch and report it as the answer for the whole estate.
	// Seeded once, then owned by the input. Safe because the search navigates with `replaceState`,
	// so no history entry carries a different `q` for this to fall out of step with — the only way
	// to arrive with another one is a fresh navigation, which remounts this.
	// svelte-ignore state_referenced_locally
	let query = $state(data.query);
	let creating = $state(false);

	function onSearch(value: string): void {
		query = value;
		const url = new URL(page.url);
		if (query.trim()) url.searchParams.set('q', query.trim());
		else url.searchParams.delete('q');
		goto(url, { keepFocus: true, noScroll: true, replaceState: true });
	}

	const isReady = (t: TenantSummary) => t.has_environment && t.has_keys && t.has_orgs;

	// Two client-side selects over the page the loader already fetched (up to PAGE_SIZE=200 firms,
	// see +page.server.ts) — NOT the same reach as `q` above. `q` asks the API and can find a firm
	// anywhere in the estate; these narrow only the batch already in hand, so a firm outside that
	// window never shows up here no matter what's picked. Fine at today's estate size; would need
	// to become API filters (like `q`) the day a firm falls off the fetched page.
	let statusFilter = $state(ALL);
	let setupFilter = $state(ALL);
	// Two values, so a segmented row rather than a dropdown: both choices are visible without a
	// click and the control reads as the switch it is. Still THREE segments — `ALL` has to stay
	// reachable, and a boolean toggle would drop one of the three states.
	const statusOptions = [
		{ value: ALL, label: 'All' },
		{ value: 'active', label: 'Active' },
		{ value: 'disabled', label: 'Disabled' }
	];
	const setupOptions = [
		{ value: ALL, label: 'All' },
		{ value: 'ready', label: 'Ready' },
		{ value: 'setup_needed', label: 'Setup needed' }
	];
	const filterDefs: FilterDef[] = [
		{ key: 'status', label: 'Status', options: statusOptions.slice(1), allLabel: 'All statuses' },
		{ key: 'setup', label: 'Setup', options: setupOptions.slice(1), allLabel: 'All setup states' }
	];
	const filterValue = $derived<Record<string, string[]>>({
		...(statusFilter !== ALL ? { status: [statusFilter] } : {}),
		...(setupFilter !== ALL ? { setup: [setupFilter] } : {})
	});
	function applyFilters(next: Record<string, string[]>): void {
		statusFilter = next.status?.[0] ?? ALL;
		setupFilter = next.setup?.[0] ?? ALL;
	}
	function clearListing(): void {
		query = '';
		statusFilter = ALL;
		setupFilter = ALL;
		const url = new URL(page.url);
		url.searchParams.delete('q');
		goto(url, { keepFocus: true, noScroll: true, replaceState: true });
	}

	// Every heading here is clickable. `Setup` and `Status` are DERIVED — the row holds `enabled`
	// and the readiness facts, not the words on the badge — so each says what to order by rather
	// than falling through to a field of that name, which does not exist.
	const firmColumns: DataTableColumn<TenantSummary>[] = [
		{ key: 'name', header: 'Firm', sortable: true, sortDefault: true },
		{ key: 'user_count', header: 'Users', sortable: true, align: 'right', defaultDir: 'desc' },
		{
			key: 'setup',
			header: 'Setup',
			sortable: true,
			sortValue: (t) => (isReady(t) ? 1 : 0)
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			sortValue: (t) => (t.enabled ? 1 : 0)
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let firmSort = $state<DataTableSort | null>(null);

	const filtered = $derived(
		data.tenants.filter(
			(t) =>
				matches(statusFilter, t.enabled ? 'active' : 'disabled') &&
				matches(setupFilter, isReady(t) ? 'ready' : 'setup_needed')
		)
	);
</script>

<PageHeader
	eyebrow="Internal operations"
	title="Tenants"
	description="Adviser firms on the platform."
/>

{#if data.apiError}
	<Alert variant="destructive" class="mb-5">
		<AlertDescription>{data.apiError}</AlertDescription>
	</Alert>
{/if}

{#if data.tenants.length === 0}
	{#if data.canManage}
		<EmptyState
			icon={BuildingIcon}
			title="No firms yet"
			description="Adviser firms you create appear here."
		>
			{#snippet action()}
				<Button onclick={() => (open = true)}>
					<PlusIcon class="size-4" /> Create the first firm
				</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<!-- This is the branch for an operator who CANNOT create a firm, and it told them that
		     firms they create appear here — describing an act as theirs on the one screen that
		     exists because it is not. -->
		<EmptyState
			icon={BuildingIcon}
			title="No firms yet"
			description="Adviser firms appear here once operations creates them."
		/>
	{/if}
{:else}
	<div class="space-y-5">
		<section>
			<SectionHeading title="Firms" count={data.total} />
			<Card glass>
				<CardContent class="space-y-4">
					<ListingToolbar
						bind:query
						onsearch={onSearch}
						placeholder="Search firms…"
						dirty={query.trim() !== '' || Object.keys(filterValue).length > 0}
						onclear={clearListing}
						{filterDefs}
						{filterValue}
						onfilters={applyFilters}
					>
						{#snippet actions()}
							{#if data.canManage}
								<Button onclick={() => (open = true)}>
									<PlusIcon class="size-4" /> New tenant
								</Button>
							{/if}
						{/snippet}
					</ListingToolbar>
					<DataTable
						columns={firmColumns}
						rows={filtered}
						bind:sort={firmSort}
						getKey={(t) => t.id}
						label="Firms"
						empty={query.trim()
							? `No firms match “${query.trim()}”.`
							: 'No firms match your filters.'}
					>
						{#snippet cell(t, column)}
							{#if column.key === 'name'}
								<a href="/tenants/{t.uid}" class="font-medium hover:underline">{t.name}</a>
								<RecordId value={t.alias} />
							{:else if column.key === 'user_count'}
								<span class="tabular-nums">{t.user_count}</span>
							{:else if column.key === 'setup'}
								<StatusBadge tone={isReady(t) ? 'success' : 'warning'}>
									{isReady(t) ? 'Ready' : 'Setup needed'}
								</StatusBadge>
							{:else if column.key === 'status'}
								<StatusBadge tone={t.enabled ? 'success' : 'danger'}>
									{t.enabled ? 'Active' : 'Disabled'}
								</StatusBadge>
							{/if}
						{/snippet}
					</DataTable>
				</CardContent>
			</Card>
		</section>
	</div>
{/if}

<!-- New firm dialog -->
<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-md">
		<Dialog.Header>
			<Dialog.Title>New firm</Dialog.Title>
			<Dialog.Description>
				Creates the tenant record. You'll set up its WorkOS environment next.
			</Dialog.Description>
		</Dialog.Header>
		<form
			method="POST"
			action="?/createTenant"
			use:enhance={() => {
				creating = true;
				return async ({ result, update }) => {
					creating = false;
					// Guarded on the UID, which is what the navigation needs — it was guarded on `id`,
					// so a response without a uid passed the check and then built `/tenants/undefined`.
					if (result.type === 'success' && result.data?.uid) {
						open = false;
						toast.success('Firm created');
						await goto(`/tenants/${result.data.uid}`);
					} else {
						await update();
					}
				};
			}}
			class="space-y-4"
		>
			<div class="space-y-2">
				<Label for="name">Name</Label>
				<Input id="name" name="name" placeholder="Acme Advisers" required />
			</div>
			<div class="space-y-2">
				<Label for="alias">Alias</Label>
				<Input id="alias" name="alias" placeholder="acme-advisers" pattern="[a-z0-9-]+" required />
				<p class="text-muted-foreground text-xs">Use lowercase letters, digits and hyphens.</p>
			</div>
			<div class="flex items-start gap-2.5 rounded-md border p-3">
				<Checkbox id="from_model" name="from_model" checked class="mt-0.5" />
				<Label for="from_model" class="block text-sm font-normal">
					Base on the model tenant
					<span class="text-muted-foreground block text-xs">
						Clone the model's roles as a starting point. Uncheck for a blank firm with no roles.
					</span>
				</Label>
			</div>
			{#if form && 'error' in form}
				<Alert variant="destructive">
					<AlertDescription>{form.error}</AlertDescription>
				</Alert>
			{/if}
			<Dialog.Footer>
				<Button type="button" variant="ghost" onclick={() => (open = false)}>Cancel</Button>
				<Button type="submit" disabled={creating}>
					{creating ? 'Creating…' : 'Create firm'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
