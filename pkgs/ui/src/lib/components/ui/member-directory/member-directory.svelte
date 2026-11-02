<script lang="ts">
	import SearchXIcon from '@lucide/svelte/icons/search-x';
	import UsersIcon from '@lucide/svelte/icons/users';
	import type { Snippet } from 'svelte';
	import ListingToolbar from '../../book/listing/listing-toolbar.svelte';
	import type { FilterDef } from '../../book/listing/filter-panel.svelte';
	import * as Avatar from '../avatar/index.js';
	import { Badge } from '../badge/index.js';
	import { StatusBadge } from '../status-badge/index.js';
	import { statusTone } from '../../book/listing/status-tone.js';
	import { Button } from '../button/index.js';
	import { DataTable, type DataTableColumn, type DataTableSort } from '../data-table/index.js';
	import EmptyState from '../empty-state/empty-state.svelte';
	import { cn } from '../../../utils.js';
	import { humanise } from '../../../sentence-case.js';
	import { formatDate } from '../../../dates.js';
	import { initials } from '../../../initials.js';

	export type MemberDirectoryRow = {
		id: string;
		name: string;
		email: string;
		avatar?: string | null;
		href?: string;
		createdAt?: string | null;
		lastSeen?: string | null;
		status: 'active' | 'inactive' | 'invited';
		/** Whether they may hold a client book (`auth.users.is_adviser`). */
		isAdviser?: boolean;
		jobTitle?: string | null;
	};

	let {
		title,
		description,
		rows,
		searchPlaceholder = 'Search by name or email',
		emptyTitle = 'No members yet',
		emptyDescription,
		roles,
		actions,
		listingActions,
		filterDefs = [],
		filterValue = {},
		onfilters,
		query: initialQuery = '',
		total = null,
		onsearch,
		onclear,
		class: className
	}: {
		title: string;
		description?: string;
		rows: MemberDirectoryRow[];
		searchPlaceholder?: string;
		emptyTitle?: string;
		emptyDescription?: string;
		roles: Snippet<[MemberDirectoryRow]>;
		actions?: Snippet<[MemberDirectoryRow]>;
		/** The page-level act beside the search (Invite member / Create profile). */
		listingActions?: Snippet;
		filterDefs?: FilterDef[];
		filterValue?: Record<string, string[]>;
		onfilters?: (next: Record<string, string[]>) => void;
		/** The search the caller was asked for, so the box shows what the URL says. */
		query?: string;
		/** How many match ACROSS the firm, when the caller is windowing. `null` ⇒ `rows` is
		 *  everything there is, and the count of it is the honest number. */
		total?: number | null;
		/** Runs the search on the SERVER. Absent ⇒ the rows are the whole set and the search below
		 *  filters them, which is only true for a caller that fetched everything. */
		onsearch?: (q: string) => void;
		/** Clears search and filters in one navigation, avoiding two URL writes racing each other. */
		onclear?: () => void;
		class?: string;
	} = $props();

	// Seeded once, then owned by the input: callers navigate with `replaceState`, so no history
	// entry carries a different search for this to fall out of step with.
	// svelte-ignore state_referenced_locally
	let query = $state(initialQuery);
	const server = $derived(!!onsearch);
	const searching = $derived(query.trim() !== '');
	function clearSearch(): void {
		query = '';
		onsearch?.('');
	}
	const visible = $derived.by(() => {
		// In SERVER mode the rows ARE the answer. Filtering them again narrows a window that was
		// narrowed for us, which is how a search starts hiding rows the server just found.
		const needle = server ? '' : query.trim().toLocaleLowerCase();
		const filtered = needle
			? rows.filter((row) => `${row.name} ${row.email}`.toLocaleLowerCase().includes(needle))
			: [...rows];
		// NOT sorted here any more: the table owns the order (`memberSort`, seeded to the newest
		// first below). This used to pre-sort, and `DataTable` then re-sorted on top of it — so the
		// moment a reader clicked any heading the old dropdown silently stopped doing anything.
		return filtered;
	});

	// A column heading's click is a SEPARATE sort from the dropdown above (which stays — it is what
	// makes "Advisers first" and "Recently active" reachable on a touch device with no header to
	// click). Adviser and Status are DERIVED — the cell renders a badge, not the raw value — so they
	// state a `sortValue`; the rest read the row's own field directly. Roles carries no sort: the
	// row has no roles LIST of its own to count — `roles` is a caller-supplied snippet.
	const HEAD_CLASS = 'text-[10px] tracking-wide uppercase';
	const memberColumns: DataTableColumn<MemberDirectoryRow>[] = [
		{
			key: 'name',
			header: 'Name',
			sortable: true,
			sortDefault: true,
			headerClass: `w-[22%] pl-4 ${HEAD_CLASS}`,
			cellClass: 'pl-4'
		},
		{
			key: 'jobTitle',
			header: 'Job title',
			sortable: true,
			hideBelow: 'md',
			headerClass: `w-[12%] ${HEAD_CLASS}`
		},
		{
			key: 'isAdviser',
			header: 'Adviser',
			sortable: true,
			defaultDir: 'desc',
			headerClass: `w-[10%] ${HEAD_CLASS}`,
			sortValue: (r) => (r.isAdviser ? 1 : 0)
		},
		{
			key: 'createdAt',
			header: 'Member since',
			sortable: true,
			defaultDir: 'desc',
			hideBelow: 'lg',
			headerClass: `w-[12%] ${HEAD_CLASS}`
		},
		{
			key: 'lastSeen',
			header: 'Last active',
			sortable: true,
			defaultDir: 'desc',
			hideBelow: 'xl',
			headerClass: `w-[10%] ${HEAD_CLASS}`
		},
		{ key: 'roles', header: 'Roles', headerClass: 'w-[20%] text-[10px] tracking-wide' },
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: `w-[10%] ${HEAD_CLASS}`,
			sortValue: (r) => humanise(r.status)
		},
		{ key: 'actions', header: '', align: 'right', headerClass: 'w-10', cellClass: 'pr-2' }
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal recreated on
	// every update would stamp the reader's chosen column back to unsorted.
	// Newest first, which is what a directory opens on. It is the TABLE's initial sort now, not a
	// pre-sort applied to the rows: this component used to carry a "Recently added / Name A–Z / …"
	// dropdown AND hand the sorted result to a table that re-sorted it, so a header click silently
	// retired the dropdown. Every axis it offered is a column, so the heading is the one control.
	// NULL — the table's `sortDefault` column (Name). See `data-table/sort.ts`.
	let memberSort = $state<DataTableSort | null>(null);
</script>

{#snippet clearSearchAction()}
	<Button variant="outline" size="sm" onclick={clearSearch}>Clear search</Button>
{/snippet}

<section class={cn('border-border bg-card overflow-hidden rounded-xl border', className)}>
	<div class="border-border border-b px-4 py-3">
		<div class="mb-3 min-w-0">
			<h2 class="text-sm font-semibold">{title}</h2>
			{#if description}<p class="text-muted-foreground mt-0.5 text-xs">{description}</p>{/if}
			{#if total !== null && total > visible.length}
				<p class="text-muted-foreground mt-0.5 text-xs tabular-nums">
					Showing {visible.length} of {total}
				</p>
			{/if}
		</div>
		<ListingToolbar
			bind:query
			placeholder={searchPlaceholder}
			{onsearch}
			dirty={query.trim() !== '' ||
				(!!onclear && Object.values(filterValue).some((values) => values.length > 0))}
			{onclear}
			{filterDefs}
			{filterValue}
			{onfilters}
			actions={listingActions}
		/>
	</div>

	{#if visible.length === 0}
		<div class="p-6">
			<!-- Two corrections. It tested `rows.length`, so a LOCAL-mode search that matched nobody
			     rendered an empty table body under a populated count instead of any empty state at
			     all — `visible` is what the table draws, so it is what decides. And a search miss
			     wore the caller's "No members yet", telling a firm with two hundred people that it
			     had none; the searched state is the component's to word, since the caller cannot
			     see the box. -->
			<EmptyState
				compact
				icon={searching ? SearchXIcon : UsersIcon}
				title={searching ? 'Nobody matches that search' : emptyTitle}
				description={searching
					? `No name or email matches “${query.trim()}”.`
					: (emptyDescription ?? 'People appear here once they are invited.')}
				action={searching ? clearSearchAction : undefined}
			/>
		</div>
	{:else}
		<div class="overflow-x-auto">
			<DataTable
				columns={memberColumns}
				rows={visible}
				bind:sort={memberSort}
				getKey={(row) => row.id}
				label={title}
				fixed
				class="min-w-[880px]"
				empty="No members yet."
			>
				{#snippet cell(row, column)}
					{#if column.key === 'name'}
						<div class="flex min-w-0 items-center gap-2.5">
							<Avatar.Root class="size-8">
								{#if row.avatar}<Avatar.Image src={row.avatar} alt="" />{/if}
								<Avatar.Fallback class="bg-muted text-[10px] font-semibold">
									{initials(row.name)}
								</Avatar.Fallback>
							</Avatar.Root>
							<div class="min-w-0">
								{#if row.href}
									<a class="block truncate text-xs font-semibold hover:underline" href={row.href}>
										{row.name}
									</a>
								{:else}
									<p class="truncate text-xs font-semibold">{row.name}</p>
								{/if}
								<p class="text-muted-foreground truncate text-[10px]">{row.email}</p>
							</div>
						</div>
					{:else if column.key === 'jobTitle'}
						<span class="text-muted-foreground text-xs">{row.jobTitle?.trim() || '—'}</span>
					{:else if column.key === 'isAdviser'}
						{#if row.isAdviser}
							<Badge variant="secondary" class="text-[10px]">Adviser</Badge>
						{:else}
							<span class="text-muted-foreground text-xs">—</span>
						{/if}
					{:else if column.key === 'createdAt'}
						<span class="text-muted-foreground text-xs">{formatDate(row.createdAt)}</span>
					{:else if column.key === 'lastSeen'}
						<span class="text-muted-foreground text-xs">{formatDate(row.lastSeen)}</span>
					{:else if column.key === 'roles'}
						{@render roles(row)}
					{:else if column.key === 'status'}
						<StatusBadge tone={statusTone(row.status)}>{humanise(row.status)}</StatusBadge>
					{:else if column.key === 'actions'}
						{#if actions}{@render actions(row)}{/if}
					{/if}
				{/snippet}
			</DataTable>
		</div>
		<div class="border-border text-muted-foreground border-t px-4 py-2.5 text-[11px]">
			Showing {visible.length} of {rows.length}
			{rows.length === 1 ? 'member' : 'members'}
		</div>
	{/if}
</section>
