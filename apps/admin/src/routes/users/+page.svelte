<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		Button,
		Card,
		CardContent,
		DataTable,
		type DataTableColumn,
		EmptyState,
		LoadErrorBanner,
		PageHeader,
		RecordId,
		SectionHeading,
		StatusBadge
	} from '@graphene/ui';
	import {
		ListingToolbar,
		PaginationStrip,
		rangeLabelFromOffset,
		type FilterDef
	} from '@graphene/ui/book/listing';
	import UsersIcon from '@lucide/svelte/icons/users';
	import type { PageData } from './$types';
	import type { UserRow } from './+page.server';

	let { data }: { data: PageData } = $props();

	const pageCount = $derived(Math.max(1, Math.ceil(data.total / data.pageSize)));
	const currentPage = $derived(Math.floor(data.offset / data.pageSize) + 1);
	function pageHref(pageNumber: number): string {
		const params = new URLSearchParams(page.url.searchParams);
		const offset = (pageNumber - 1) * data.pageSize;
		if (offset > 0) params.set('offset', String(offset));
		else params.delete('offset');
		const qs = params.toString();
		return qs ? `?${qs}` : page.url.pathname;
	}

	/**
	 * Every narrowing lives in the URL and is applied by the API — the directory pages, and the
	 * browser never holds the whole estate, so a control that filtered the rows on screen would
	 * describe this page while appearing to describe the platform.
	 *
	 * One writer for all of them, so changing the status cannot silently drop the search term.
	 * A change to either resets to the first page: page 4 of a re-narrowed list is a set of rows
	 * nobody asked for, and is usually empty.
	 */
	function navigate(extra: { q?: string; status?: string; offset?: number }) {
		const next = { q: data.q, status: data.status, offset: 0, ...extra };
		const params = new URLSearchParams();
		if (next.q.trim()) params.set('q', next.q.trim());
		if (next.status) params.set('status', next.status);
		if (next.offset > 0) params.set('offset', String(next.offset));
		goto(`/users${params.size ? `?${params}` : ''}`, { keepFocus: true });
	}

	const filterDefs = $derived<FilterDef[]>([
		{
			key: 'status',
			label: 'Status',
			options: [
				{ value: 'active', label: 'Active' },
				{ value: 'disabled', label: 'Disabled' }
			],
			single: true,
			allLabel: 'All'
		}
	]);
	const filterValue = $derived<Record<string, string[]>>(
		data.status ? { status: [data.status] } : {}
	);
	function applyFilters(next: Record<string, string[]>) {
		navigate({ status: next.status?.[0] ?? '' });
	}
	const dirty = $derived(!!data.status);

	// `data.users` is one server-paginated PAGE (`?limit=&offset=` above) — client logins have no
	// bound, so the API pages them and the browser never holds the whole directory. Sorting this
	// page client-side would silently reorder it and lie about the estate's real order (the same
	// reason the Activity feed and the platforms console result grid stay `sortDisabled`), so the
	// headers keep their place in a shared table without offering to sort.
	const userColumns: DataTableColumn<UserRow>[] = [
		{ key: 'name', header: 'Person' },
		{ key: 'tenant_name', header: 'Firm' },
		{ key: 'memberships', header: 'Memberships', align: 'right' },
		{ key: 'status', header: 'Status' }
	];
</script>

{#snippet clearUserFilters()}
	<Button variant="outline" size="sm" href="/users">Clear filters</Button>
{/snippet}

<PageHeader
	eyebrow="Internal operations"
	title="Users"
	description="Every login across the platform. The same address has a separate login for each firm."
/>

{#if data.apiError}
	<LoadErrorBanner message={data.apiError} class="mb-5" onRetry={() => invalidateAll()} />
{/if}

<div class="space-y-5">
	<ListingToolbar
		query={data.q}
		placeholder="Search name, email or firm"
		{dirty}
		onclear={() => navigate({ status: '', q: '' })}
		onsearch={(term) => navigate({ q: term })}
		{filterDefs}
		{filterValue}
		onfilters={applyFilters}
	/>

	<section>
		<SectionHeading
			title={data.q || data.status ? 'Matching users' : 'All users'}
			count={data.total}
		/>
		<Card glass>
			<CardContent>
				{#if data.users.length === 0}
					<!-- The narrowed branch told the reader to clear a filter and gave them nothing to
						     clear it with. -->
					<EmptyState
						compact
						icon={UsersIcon}
						title={data.q || data.status ? 'No people match' : 'No users yet'}
						description={data.q || data.status
							? 'Try part of an email address or the firm’s name, or clear the filters to see everyone.'
							: 'People appear here once a firm invites them.'}
						action={data.q || data.status ? clearUserFilters : undefined}
					/>
				{:else}
					<DataTable
						columns={userColumns}
						rows={data.users}
						sortDisabled
						getKey={(u) => u.user_id}
						label="Users"
					>
						{#snippet cell(u, column)}
							{#if column.key === 'name'}
								<a href="/users/{u.user_uid}" class="font-medium hover:underline">
									{u.name ?? u.email}
								</a>
								<div class="text-muted-foreground text-xs">{u.email}</div>
							{:else if column.key === 'tenant_name'}
								{#if u.tenant_id}
									<a href="/tenants/{u.tenant_uid}" class="hover:underline">{u.tenant_name}</a>
									<RecordId value={u.tenant_alias} />
								{:else}
									<span class="text-muted-foreground text-sm">—</span>
								{/if}
							{:else if column.key === 'memberships'}
								<span class="tabular-nums">{u.memberships}</span>
							{:else if column.key === 'status'}
								<StatusBadge tone={u.enabled ? 'success' : 'neutral'}>
									{u.enabled ? 'Active' : 'Disabled'}
								</StatusBadge>
							{/if}
						{/snippet}
					</DataTable>

					{#if data.total > 0}
						<PaginationStrip
							label={rangeLabelFromOffset(data.offset, data.users.length, data.total)}
							page={currentPage}
							{pageCount}
							href={pageHref}
						/>
					{/if}
				{/if}
			</CardContent>
		</Card>
	</section>
</div>
