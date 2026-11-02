<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import {
		Button,
		buttonVariants,
		Card,
		CardContent,
		DataTable,
		type DataTableColumn,
		EmptyState,
		Input,
		LoadErrorBanner,
		Popover,
		Select
	} from '@graphene/ui';
	import HardDriveIcon from '@lucide/svelte/icons/hard-drive';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import FileIcon from '@lucide/svelte/icons/file';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import SearchIcon from '@lucide/svelte/icons/search';
	import ListFilterIcon from '@lucide/svelte/icons/list-filter';
	import CalendarIcon from '@lucide/svelte/icons/calendar';
	import type { PageData } from './$types';
	import type { StorageFolder, StorageObject } from './+page.server';

	// One row is either a folder or an object — the table lists both, and a column's meaning
	// differs by which (a folder's "Size" is a recursive rollup, an object's is its own byte
	// count), so the cell snippet below switches on `kind` before it switches on the column.
	type BrowseRow =
		{ kind: 'folder'; folder: StorageFolder } | { kind: 'object'; object: StorageObject };

	let { data }: { data: PageData } = $props();

	const base = $derived(`/tenants/${data.tenantUid}/settings/storage`);

	// Breadcrumb: each cumulative prefix segment, so you can jump up to any level (root = the bucket).
	// Segments resolve to a friendly name (tenant/document) via the API's `prefix_labels`, falling
	// back to the raw segment.
	const crumbs = $derived.by(() => {
		let acc = '';
		const labels = data.page?.prefix_labels ?? {};
		return data.prefix
			.split('/')
			.filter(Boolean)
			.map((p) => {
				acc += `${p}/`;
				return { label: labels[p] ?? p, prefix: acc };
			});
	});

	function fmtSize(n: number): string {
		if (n < 1024) return `${n} B`;
		if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
		return `${(n / 1024 / 1024).toFixed(1)} MB`;
	}
	function fmtDate(iso: string): string {
		const d = new Date(iso);
		return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('en-GB');
	}
	// Folder prefix's last segment ("documents/x/" -> "x"); object key relative to the current prefix.
	const leaf = (prefix: string): string => prefix.replace(/\/$/, '').split('/').pop() ?? prefix;
	const fileName = (key: string): string => key.slice(data.prefix.length) || key;
	const fileExt = (name: string): string => {
		const i = name.lastIndexOf('.');
		return i > 0 ? name.slice(i + 1).toLowerCase() : '';
	};
	const viewHref = (key: string, download = false): string =>
		`${base}/view?key=${encodeURIComponent(key)}${download ? '&download=1' : ''}`;

	// --- Filters (client-side, scoped to the current page of results) ---------------------------
	// S3 lists by prefix + continuation token and can't filter by name or date, so these narrow the
	// rows already fetched; page navigation below moves between the underlying S3 pages.
	let search = $state('');
	let typeFilter = $state('all'); // 'all' | 'folder' | 'ext:<ext>'
	let fromDate = $state('');
	let toDate = $state('');

	// Type options come from the API's rolled-up `object_types` (every extension found under this
	// prefix, including inside folders) — so the filter offers real types even at a folder-only level.
	const typeOptions = $derived.by(() => {
		const opts = [{ value: 'all', label: 'All types' }];
		if (data.page && data.page.folders.length) opts.push({ value: 'folder', label: 'Folders' });
		for (const e of data.page?.object_types ?? [])
			opts.push({ value: `ext:${e}`, label: e.toUpperCase() });
		return opts;
	});
	const typeLabel = $derived(typeOptions.find((o) => o.value === typeFilter)?.label ?? 'All types');

	const dateActive = $derived(!!(fromDate || toDate));
	const dateLabel = $derived(
		fromDate && toDate
			? `${fromDate} → ${toDate}`
			: fromDate
				? `From ${fromDate}`
				: toDate
					? `Until ${toDate}`
					: 'Date'
	);
	const hasFilters = $derived(search.trim() !== '' || typeFilter !== 'all' || dateActive);

	const query = $derived(search.trim().toLowerCase());
	// Folders now carry a recursive rollup, so every filter applies to them too: name search, an
	// extension filter (folder contains that type → keep it, so you can drill in), and the date range
	// (against the folder's latest-modified).
	const visibleFolders = $derived.by(() => {
		if (!data.page) return [];
		const from = fromDate ? new Date(`${fromDate}T00:00:00`) : null;
		const to = toDate ? new Date(`${toDate}T23:59:59.999`) : null;
		const wantExt = typeFilter.startsWith('ext:') ? typeFilter.slice(4) : null;
		return data.page.folders.filter((f) => {
			if (query && !`${f.label ?? ''} ${leaf(f.prefix)}`.toLowerCase().includes(query))
				return false;
			if (wantExt && !f.exts.includes(wantExt)) return false;
			if (from || to) {
				if (!f.last_modified) return false;
				const t = new Date(f.last_modified);
				if (from && t < from) return false;
				if (to && t > to) return false;
			}
			return true;
		});
	});
	const visibleObjects = $derived.by(() => {
		if (!data.page || typeFilter === 'folder') return [];
		const from = fromDate ? new Date(`${fromDate}T00:00:00`) : null;
		const to = toDate ? new Date(`${toDate}T23:59:59.999`) : null;
		const wantExt = typeFilter.startsWith('ext:') ? typeFilter.slice(4) : null;
		return data.page.objects.filter((o) => {
			const name = fileName(o.key);
			if (query && !name.toLowerCase().includes(query)) return false;
			if (wantExt && fileExt(name) !== wantExt) return false;
			if (from || to) {
				const t = new Date(o.last_modified);
				if (from && t < from) return false;
				if (to && t > to) return false;
			}
			return true;
		});
	});
	const filteredEmpty = $derived(visibleFolders.length === 0 && visibleObjects.length === 0);

	// Folders sort before objects, matching the hand-rolled table this replaced.
	const browseRows = $derived<BrowseRow[]>([
		...visibleFolders.map((folder) => ({ kind: 'folder' as const, folder })),
		...visibleObjects.map((object) => ({ kind: 'object' as const, object }))
	]);
	const browseKey = (r: BrowseRow) =>
		r.kind === 'folder' ? `folder:${r.folder.prefix}` : `object:${r.object.key}`;

	// One S3 page at a time (the continuation-token paging below), so a client-side sort would
	// silently reorder this page and lie about the bucket's real order — the same reason the
	// Activity feed and the platforms console result grid stay `sortDisabled`.
	const browseColumns: DataTableColumn<BrowseRow>[] = [
		{ key: 'name', header: 'Name' },
		{ key: 'size', header: 'Size', align: 'right', cellClass: 'text-sm tabular-nums' },
		{ key: 'modified', header: 'Modified', cellClass: 'text-muted-foreground text-sm' },
		{ key: 'actions', header: '', align: 'right' }
	];

	function clearFilters() {
		search = '';
		typeFilter = 'all';
		fromDate = '';
		toDate = '';
	}

	// --- Page navigation ------------------------------------------------------------------------
	// Keep the stack of tokens that led to the current page so Back can retrace it (forward-only
	// tokens). Reset whenever the folder changes. Component state persists across in-route goto().
	let history = $state<string[]>([]);
	const pageNo = $derived(history.length + 1);

	function urlFor(prefix: string, token: string): string {
		const p = new URLSearchParams();
		if (prefix) p.set('prefix', prefix);
		if (token) p.set('token', token);
		const qs = p.toString();
		return qs ? `${base}?${qs}` : base;
	}
	function browse(prefix: string) {
		history = [];
		clearFilters();
		goto(urlFor(prefix, ''));
	}
	function nextPage() {
		const token = data.page?.next_token;
		if (!token) return;
		history = [...history, data.token];
		clearFilters();
		goto(urlFor(data.prefix, token));
	}
	function prevPage() {
		if (history.length === 0) return;
		const prev = history[history.length - 1] ?? '';
		history = history.slice(0, -1);
		clearFilters();
		goto(urlFor(data.prefix, prev));
	}
</script>

<!-- The heading is the Settings layout's; this panel is its Storage section. -->
<p class="text-muted-foreground mb-4 text-sm">Objects in the firm's storage bucket.</p>

{#if data.apiError}
	<LoadErrorBanner message={data.apiError} class="mb-5" onRetry={() => invalidateAll()} />
{/if}

{#if data.page}
	{@const rawEmpty = data.page.folders.length === 0 && data.page.objects.length === 0}
	<div class="space-y-4">
		{#if !data.page.per_tenant}
			<div
				class="border-warning/40 bg-warning/10 text-foreground rounded-lg border px-3 py-2 text-sm"
			>
				This firm has no dedicated storage yet. Shared storage
				<code class="font-mono text-xs">{data.page.bucket}</code> contains files from every firm. Do not
				treat this view as firm-specific.
			</div>
		{/if}

		{#if !rawEmpty}
			<div class="flex flex-wrap items-center gap-2">
				<div class="relative w-full max-w-xs">
					<SearchIcon
						class="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2"
					/>
					<Input
						bind:value={search}
						placeholder="Search this folder"
						class="bg-background h-8 pl-8"
					/>
				</div>
				<Select.Root
					type="single"
					value={typeFilter}
					onValueChange={(v) => (typeFilter = v ?? 'all')}
				>
					<Select.Trigger class="bg-background h-8 gap-1.5 text-sm" aria-label="Filter by type">
						<ListFilterIcon class="size-3.5" />
						{typeLabel}
					</Select.Trigger>
					<Select.Content>
						{#each typeOptions as o (o.value)}
							<Select.Item value={o.value}>{o.label}</Select.Item>
						{/each}
					</Select.Content>
				</Select.Root>
				<Popover.Root>
					<Popover.Trigger class={buttonVariants({ variant: 'outline', size: 'sm' })}>
						<CalendarIcon class="size-3.5" />
						{dateLabel}
					</Popover.Trigger>
					<Popover.Content class="w-64" align="end">
						<div class="space-y-3">
							<div class="space-y-1">
								<span class="text-muted-foreground text-xs">Modified from</span>
								<Input type="date" bind:value={fromDate} class="h-8" aria-label="From date" />
							</div>
							<div class="space-y-1">
								<span class="text-muted-foreground text-xs">Modified to</span>
								<Input type="date" bind:value={toDate} class="h-8" aria-label="To date" />
							</div>
							<div class="flex justify-end">
								<Button
									size="sm"
									variant="ghost"
									disabled={!dateActive}
									onclick={() => {
										fromDate = '';
										toDate = '';
									}}
								>
									Clear
								</Button>
							</div>
						</div>
					</Popover.Content>
				</Popover.Root>
				{#if hasFilters}
					<Button size="sm" variant="ghost" class="text-muted-foreground" onclick={clearFilters}>
						Clear all
					</Button>
				{/if}
			</div>
		{/if}

		<Card glass>
			<CardContent>
				<nav class="text-muted-foreground mb-4 flex flex-wrap items-center gap-1 text-sm">
					<button
						type="button"
						class="hover:text-foreground inline-flex items-center gap-1"
						onclick={() => browse('')}
					>
						<HardDriveIcon class="size-3.5" />
						{data.page.bucket}
					</button>
					{#each crumbs as c (c.prefix)}
						<ChevronRightIcon class="size-3.5 shrink-0" />
						<button type="button" class="hover:text-foreground" onclick={() => browse(c.prefix)}
							>{c.label}</button
						>
					{/each}
				</nav>
				{#if rawEmpty}
					<EmptyState
						compact
						icon={FolderIcon}
						title="Empty"
						description="Nothing stored under this path."
					/>
				{:else if filteredEmpty}
					<EmptyState
						compact
						icon={SearchIcon}
						title="No matches"
						description="No items on this page match your filters."
					>
						{#snippet action()}
							<Button variant="link" size="sm" onclick={clearFilters}>Clear filters</Button>
						{/snippet}
					</EmptyState>
				{:else}
					<DataTable
						columns={browseColumns}
						rows={browseRows}
						sortDisabled
						getKey={browseKey}
						label="Files"
						empty="No items on this page match your filters."
					>
						{#snippet cell(r, column)}
							{#if r.kind === 'folder'}
								{@const f = r.folder}
								{#if column.key === 'name'}
									<button
										type="button"
										class="inline-flex items-center gap-2 font-medium hover:underline"
										onclick={() => browse(f.prefix)}
									>
										<FolderIcon class="text-muted-foreground size-4 shrink-0" />
										<span
											class="truncate"
											title={f.label ? `${f.label} (${leaf(f.prefix)})` : leaf(f.prefix)}
										>
											{#if f.label}
												{f.label}
												<span class="text-muted-foreground font-normal">({leaf(f.prefix)})</span>
											{:else}
												{leaf(f.prefix)}/
											{/if}
										</span>
										{#if f.count}
											<span class="text-muted-foreground text-xs font-normal"
												>({f.count}{f.truncated ? '+' : ''})</span
											>
										{/if}
									</button>
								{:else if column.key === 'size'}
									<span class={f.size ? '' : 'text-muted-foreground'}>
										{f.truncated ? '≥ ' : ''}{fmtSize(f.size)}
									</span>
								{:else if column.key === 'modified'}
									{f.last_modified ? fmtDate(f.last_modified) : '—'}
								{/if}
							{:else}
								{@const o = r.object}
								{#if column.key === 'name'}
									<a
										href={viewHref(o.key)}
										target="_blank"
										rel="noopener"
										class="flex min-w-0 items-center gap-2 font-medium hover:underline"
										title="View {fileName(o.key)}"
									>
										<FileIcon class="text-muted-foreground size-4 shrink-0" />
										<span class="truncate">{fileName(o.key)}</span>
									</a>
								{:else if column.key === 'size'}
									{fmtSize(o.size)}
								{:else if column.key === 'modified'}
									{fmtDate(o.last_modified)}
								{:else if column.key === 'actions'}
									<a
										href={viewHref(o.key, true)}
										class="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
									>
										<DownloadIcon class="size-3.5" /> Download
									</a>
								{/if}
							{/if}
						{/snippet}
					</DataTable>
				{/if}

				{#if data.page.next_token || history.length > 0}
					<div class="mt-3 flex items-center justify-end gap-2">
						<span class="text-muted-foreground text-xs">Page {pageNo}</span>
						<Button
							variant="outline"
							size="icon"
							class="size-8"
							onclick={prevPage}
							disabled={history.length === 0}
							aria-label="Previous page"
						>
							<ChevronLeftIcon class="size-4" />
						</Button>
						<Button
							variant="outline"
							size="icon"
							class="size-8"
							onclick={nextPage}
							disabled={!data.page.next_token}
							aria-label="Next page"
						>
							<ChevronRightIcon class="size-4" />
						</Button>
					</div>
				{/if}
			</CardContent>
		</Card>
	</div>
{/if}
