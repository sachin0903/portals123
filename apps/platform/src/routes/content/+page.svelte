<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		Alert,
		AlertDescription,
		Badge,
		Button,
		Card,
		CardContent,
		DataTable,
		DropdownMenu,
		EmptyState,
		FORM_STATUS_META,
		formatDate,
		humanise,
		Input,
		Label,
		ListingSkeleton,
		LoadErrorBanner,
		Segmented,
		SelectMenu,
		Sheet,
		StatusBadge,
		Streamed,
		type DataTableColumn
	} from '@graphene/ui';
	import { ListingOverflow, ListingToolbar, type FilterDef } from '@graphene/ui/book/listing';
	import type { ContentItem, ContentKind } from '$lib/content';
	import { contentForView } from './content-view';
	import type { PageData } from './$types';
	import FileIcon from '@lucide/svelte/icons/file';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import NewspaperIcon from '@lucide/svelte/icons/newspaper';

	let { data }: { data: PageData } = $props();
	let createOpen = $state(false);
	let title = $state('');
	let kind = $state<ContentKind>('article');
	let query = $state('');
	// Holds only the folders the reader shut, so a new folder opens expanded.
	let collapsed = $state<Set<string>>(new Set());

	$effect(() => {
		query = data.q ?? '';
	});

	function navigate(next: { q?: string; kind?: string[]; status?: string[]; view?: string }) {
		const params = new URLSearchParams(page.url.searchParams);
		const q = (next.q !== undefined ? next.q : data.q) ?? '';
		const kindFilter = next.kind !== undefined ? next.kind : (data.kind ?? []);
		const status = next.status !== undefined ? next.status : (data.status ?? []);
		const view = next.view !== undefined ? next.view : data.view;
		if (q.trim()) params.set('q', q.trim());
		else params.delete('q');
		params.delete('kind');
		for (const k of kindFilter) if (k) params.append('kind', k);
		if (view === 'archived') params.set('view', 'archived');
		else params.delete('view');
		params.delete('status');
		if (view !== 'archived') for (const s of status) if (s) params.append('status', s);
		const qs = params.toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	const kindFilter = $derived(data.kind ?? []);
	const statusFilter = $derived(data.status ?? []);
	const contentView = $derived(data.view === 'archived' ? 'archived' : 'current');
	// `visibleItems` is derived from the streamed rows, so it lives inside the `Streamed` snippet
	// below — there are no rows yet while `data.feed` is in flight.
	const dirty = $derived(
		kindFilter.length > 0 || statusFilter.length > 0 || (data.q ?? '').trim() !== ''
	);

	// Status is only offered on the Current tab — Archived is its own view, not a status.
	const filterDefs = $derived<FilterDef[]>([
		{
			key: 'kind',
			label: 'Kind',
			allLabel: 'All kinds',
			options: [
				{ value: 'article', label: 'Article' },
				{ value: 'news', label: 'News' },
				{ value: 'guide', label: 'Guide' }
			]
		},
		...(contentView === 'current'
			? [
					{
						key: 'status',
						label: 'Status',
						allLabel: 'All statuses',
						options: [
							{ value: 'draft', label: 'Draft' },
							{ value: 'published', label: 'Published' }
						]
					} as FilterDef
				]
			: [])
	]);
	const barValue = $derived<Record<string, string[]>>({
		...(kindFilter.length ? { kind: kindFilter } : {}),
		...(contentView === 'current' && statusFilter.length ? { status: statusFilter } : {})
	});

	// One folder band per category; uncategorised items sort last.
	const categoryGroup = (item: ContentItem) => [
		{
			key: item.category_uid ?? '__uncategorised__',
			label: item.category_name ?? 'Uncategorised',
			last: !item.category_uid
		}
	];

	const columns: DataTableColumn<ContentItem>[] = [
		{ key: 'title', header: 'Name', sortable: true, sortDefault: true, headerClass: 'w-[50%]' },
		{
			key: 'audience',
			header: 'Audience',
			sortable: true,
			hideBelowWidth: 'md',
			headerClass: 'w-[18%]',
			sortValue: (item) => humanise(item.audience)
		},
		{ key: 'status', header: 'Status', sortable: true, headerClass: 'w-[16%]' },
		{
			key: 'updated',
			header: 'Modified',
			sortable: true,
			hideBelowWidth: 'lg',
			defaultDir: 'desc',
			headerClass: 'w-[16%]',
			cellClass: 'text-muted-foreground text-xs',
			sortValue: (item) => item.updated_at
		}
	];
</script>

{#snippet clearContentFilters()}
	<Button variant="outline" size="sm" onclick={() => navigate({ q: '', kind: [], status: [] })}>
		Clear filters
	</Button>
{/snippet}

{#snippet createFirstContent()}
	<Button size="sm" onclick={() => (createOpen = true)}>New content</Button>
{/snippet}

<svelte:head><title>Content · Platform</title></svelte:head>

{#snippet listingActions()}
	<ListingOverflow>
		<DropdownMenu.Item onclick={() => (createOpen = true)}>New</DropdownMenu.Item>
	</ListingOverflow>
{/snippet}

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<!-- The library is STREAMED. The toolbar's query is already resolved, so it paints while the
	     rows are in flight; `keep` holds it across a filter navigation or a load re-run so the
	     search box does not flash back to the skeleton. A caller without `content:read` gets a
	     resolved feed, and this renders the alert straight away. -->
	{#if data.feed instanceof Promise || data.canManage}
		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				<ListingToolbar
					class="shrink-0"
					bind:query
					placeholder="Search titles…"
					{dirty}
					onsearch={(term) => navigate({ q: term })}
					onclear={() => navigate({ q: '', kind: [], status: [] })}
					onrefresh={() => invalidateAll()}
					actions={data.canManage ? listingActions : undefined}
					{filterDefs}
					filterValue={barValue}
					onfilters={(next) => navigate({ kind: next.kind ?? [], status: next.status ?? [] })}
				>
					{#snippet filters()}
						<Segmented
							options={[
								{ value: 'current', label: 'Current' },
								{ value: 'archived', label: 'Archived' }
							]}
							value={contentView}
							onValueChange={(v) => navigate({ view: v, status: [] })}
						/>
					{/snippet}
				</ListingToolbar>
				<Streamed data={data.feed} onretry={() => invalidateAll()} keep>
					{#snippet pending()}
						<!-- The real toolbar is already above. ListingSkeleton also draws a search and a
						     card; flatten those so only its rows show under the toolbar that is live. -->
						<ListingSkeleton
							fill={false}
							header={false}
							filters={0}
							pager={false}
							columns={4}
							class="[&_[data-slot=card]]:contents [&_[data-slot=card-content]]:contents [&_[data-slot=card-content]>div:first-child]:hidden"
						/>
					{/snippet}
					{#snippet children(feed)}
						{@const visibleItems = contentForView(feed.items, contentView)}
						{#if feed.loadError}
							<LoadErrorBanner onRetry={() => invalidateAll()} />
						{:else if visibleItems.length === 0}
							<div class="py-8">
								<!-- Filtered, archived and empty are different states; create needs `canManage`.
								     A failed read is the banner above, not this "no content" state. -->
								<EmptyState
									compact
									icon={FolderIcon}
									title={dirty
										? 'No content matches your filters'
										: contentView === 'archived'
											? 'No archived content'
											: 'No content yet'}
									description={dirty
										? 'Try a different search, kind or status, or clear the filters to see everything.'
										: contentView === 'archived'
											? 'Content you archive is kept here, out of the library but not deleted.'
											: 'Articles, news items and PDF guides your firm publishes to clients appear here.'}
									action={dirty
										? clearContentFilters
										: contentView === 'archived' || !data.canManage
											? undefined
											: createFirstContent}
								/>
							</div>
						{:else}
							<DataTable
								{columns}
								rows={visibleItems}
								getKey={(item) => item.uid}
								groupBy={categoryGroup}
								bind:collapsed
								onRowClick={(item) => goto(`/content/${item.uid}/edit`)}
								label="Content files"
								empty="No content matches your filters."
								scroll
								fixed
							>
								{#snippet cell(item, column)}
									{#if column.key === 'title'}
										<a
											href="/content/{item.uid}/edit"
											class="flex min-w-0 items-center gap-2 font-medium hover:underline"
										>
											{#if item.kind === 'guide'}
												<FileTextIcon class="text-destructive size-4 shrink-0" />
											{:else if item.kind === 'news'}
												<NewspaperIcon class="text-muted-foreground size-4 shrink-0" />
											{:else}
												<FileIcon class="text-muted-foreground size-4 shrink-0" />
											{/if}
											<span class="truncate">{item.title}</span>
											<span class="text-muted-foreground text-xs font-normal">
												{item.kind === 'guide' ? 'PDF' : item.kind === 'news' ? 'News' : 'Article'}
											</span>
										</a>
									{:else if column.key === 'audience'}
										<Badge variant="outline" class="font-normal">{humanise(item.audience)}</Badge>
									{:else if column.key === 'status'}
										<StatusBadge tone={FORM_STATUS_META[item.status].tone}>
											{FORM_STATUS_META[item.status].label}
										</StatusBadge>
									{:else if column.key === 'updated'}
										<span>{formatDate(item.updated_at)}</span>
									{/if}
								{/snippet}
							</DataTable>
						{/if}
					{/snippet}
				</Streamed>
			</CardContent>
		</Card>
	{:else if !data.feed.loadError}
		<Alert>
			<AlertDescription>You do not have permission to view the content library.</AlertDescription>
		</Alert>
	{/if}
</div>

<!-- Gated like the toolbar, so no route into `createOpen` reaches a form the API refuses. -->
{#if data.canManage}
	<Sheet.Root bind:open={createOpen}>
		<Sheet.Content>
			<Sheet.Header>
				<Sheet.Title>New content</Sheet.Title>
				<Sheet.Description>
					Choose an editable portal article or upload a finished PDF.
				</Sheet.Description>
			</Sheet.Header>
			<form method="POST" action="?/create" use:enhance class="mt-4 flex flex-col gap-4">
				<div class="flex flex-col gap-1.5">
					<Label>Content type</Label>
					<SelectMenu
						value={kind}
						onValueChange={(v) => {
							if (v === 'news' || v === 'article' || v === 'guide') kind = v;
						}}
						items={[
							{ value: 'article', label: 'Article' },
							{ value: 'news', label: 'News' },
							{ value: 'guide', label: 'PDF guide' }
						]}
					/>
					<input type="hidden" name="kind" value={kind} />
					<p class="text-muted-foreground text-xs">
						{kind === 'guide'
							? 'Creates a PDF-only item. You will upload the document on the next screen.'
							: 'Creates an item you write or import from Word in the article editor.'}
					</p>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="title">Title</Label>
					<Input id="title" name="title" bind:value={title} required />
				</div>
				<Button type="submit">{kind === 'guide' ? 'Create PDF guide' : 'Create'}</Button>
			</form>
		</Sheet.Content>
	</Sheet.Root>
{/if}
