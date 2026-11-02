<script lang="ts">
	// Thin route over the house listing furniture: a Card holding a ListingToolbar and the shared
	// DataTable. The topbar crumb already names the page, so there is no heading — the same canvas
	// as Clients and Accounts.
	import { applyAction, enhance } from '$app/forms';
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
		LoadErrorBanner,
		Sheet,
		StatusBadge,
		type DataTableColumn
	} from '@graphene/ui';
	import { ListingOverflow, ListingToolbar, type FilterDef } from '@graphene/ui/book/listing';
	import type { TermItem } from '$lib/terms';
	import type { PageData } from './$types';
	import FileSignatureIcon from '@lucide/svelte/icons/file-signature';

	let { data }: { data: PageData } = $props();
	let createOpen = $state(false);
	let title = $state('');
	let query = $state('');

	$effect(() => {
		query = data.q ?? '';
	});

	/** The listing is server-driven, so a requested filter becomes a URL. `keepFocus` so typing in
	 *  the search box is not interrupted by the reload it triggers. */
	function navigate(next: { q?: string; status?: string }) {
		const params = new URLSearchParams(page.url.searchParams);
		const q = (next.q !== undefined ? next.q : data.q) ?? '';
		const status = (next.status !== undefined ? next.status : data.status) ?? '';
		if (q.trim()) params.set('q', q.trim());
		else params.delete('q');
		if (status) params.set('status', status);
		else params.delete('status');
		const qs = params.toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	const statusFilter = $derived(data.status ?? '');
	const dirty = $derived(statusFilter !== '' || (data.q ?? '').trim() !== '');

	// Archived is a STATUS, not a view: a term that is archived has a lifecycle state like draft and
	// published, and splitting it into its own tab made the status filter mean "of the terms that are
	// not archived" — two controls describing one column.
	const filterDefs: FilterDef[] = [
		{
			key: 'status',
			label: 'Status',
			allLabel: 'All statuses',
			// The API takes one `status`, so the panel must not offer an "or".
			single: true,
			options: [
				{ value: 'draft', label: 'Draft' },
				{ value: 'published', label: 'Published' },
				{ value: 'archived', label: 'Archived' }
			]
		}
	];
	const barValue = $derived<Record<string, string[]>>(
		statusFilter ? { status: [statusFilter] } : {}
	);

	const statusLabel = (item: TermItem) =>
		FORM_STATUS_META[item.status as keyof typeof FORM_STATUS_META]?.label ?? item.status;

	const formatLabel = (kind: string) => (kind === 'pdf' ? 'PDF' : 'HTML');

	const publishedDate = (item: TermItem) =>
		formatDate(item.current_version_published_at, 'Not published');

	const columns: DataTableColumn<TermItem>[] = [
		{ key: 'title', header: 'Title', sortable: true, sortDefault: true, headerClass: 'w-[22%]' },
		{ key: 'code', header: 'Code', sortable: true, hideBelowWidth: 'xl', headerClass: 'w-[12%]' },
		{
			key: 'required_at',
			header: 'Required',
			sortable: true,
			hideBelowWidth: '2xl',
			sortValue: (item) => item.required_at.join(', '),
			headerClass: 'w-[16%]'
		},
		{
			key: 'current_version_number',
			header: 'Revisions',
			sortable: true,
			defaultDir: 'desc',
			align: 'center',
			sortValue: (item) => item.current_version_number ?? 0,
			headerClass: 'w-[10%]'
		},
		{
			key: 'current_version_published_at',
			header: 'Current published',
			sortable: true,
			defaultDir: 'desc',
			hideBelowWidth: 'lg',
			sortValue: (item) =>
				item.current_version_published_at ? new Date(item.current_version_published_at) : null,
			headerClass: 'w-[16%]'
		},
		{
			key: 'body_kind',
			header: 'Format',
			sortable: true,
			hideBelowWidth: 'md',
			sortValue: (item) => formatLabel(item.body_kind),
			headerClass: 'w-[10%]'
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			sortValue: statusLabel,
			headerClass: 'w-[14%]'
		}
	];
</script>

{#snippet createFirst()}
	<Button size="sm" onclick={() => (createOpen = true)}>New terms</Button>
{/snippet}

{#snippet listingActions()}
	<ListingOverflow>
		<DropdownMenu.Item onclick={() => (createOpen = true)}>New terms</DropdownMenu.Item>
	</ListingOverflow>
{/snippet}

{#snippet clearFilters()}
	<Button variant="outline" size="sm" onclick={() => navigate({ q: '', status: '' })}>
		Clear filters
	</Button>
{/snippet}

{#snippet noTerms()}
	<!-- Three states, not two: a search that matches nothing must not tell a firm with a full
	     library that it has published nothing. -->
	<EmptyState
		icon={FileSignatureIcon}
		title={dirty ? 'No terms match your filters' : 'No terms yet'}
		description={dirty
			? 'Try a different search or status, or clear the filters to see everything.'
			: 'Publish the declarations and PDFs clients must accept before an account opens.'}
		action={dirty ? clearFilters : data.canManage ? createFirst : undefined}
		compact
		chromeless
	/>
{/snippet}

<svelte:head><title>Terms · Platform</title></svelte:head>

<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#if !data.canManage && data.items.length === 0 && !data.loadError}
		<Alert>
			<AlertDescription>You do not have permission to view the terms library.</AlertDescription>
		</Alert>
	{/if}

	{#if data.loadError}
		<LoadErrorBanner onRetry={() => invalidateAll()} />
	{/if}

	{#if data.canManage || data.items.length > 0}
		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				<ListingToolbar
					class="shrink-0"
					bind:query
					placeholder="Search titles…"
					{dirty}
					onsearch={(term) => navigate({ q: term })}
					onclear={() => navigate({ q: '', status: '' })}
					onrefresh={() => invalidateAll()}
					actions={data.canManage ? listingActions : undefined}
					{filterDefs}
					filterValue={barValue}
					onfilters={(next) => navigate({ status: next.status?.[0] ?? '' })}
				/>
				<DataTable
					{columns}
					rows={data.items}
					getKey={(item) => item.uid}
					label="Terms"
					empty={noTerms}
					scroll
					fixed
					onRowClick={(item) => goto(`/terms/${item.uid}`)}
				>
					{#snippet cell(item, column)}
						{#if column.key === 'title'}
							<!-- A real anchor as well as the row handler: middle-click and "open in new tab" are
							     what a row-level click handler cannot provide. -->
							<a href="/terms/{item.uid}" class="font-medium hover:underline">{item.title}</a>
						{:else if column.key === 'code'}
							<span class="text-muted-foreground">{item.code}</span>
						{:else if column.key === 'required_at'}
							{#if item.required_at.length}
								<span class="inline-flex flex-wrap gap-1">
									{#each item.required_at as point (point)}
										<Badge variant="outline" class="font-normal">{humanise(point)}</Badge>
									{/each}
								</span>
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						{:else if column.key === 'current_version_number'}
							<span class="tabular-nums">{item.current_version_number ?? 0}</span>
						{:else if column.key === 'current_version_published_at'}
							<span
								class:text-muted-foreground={!item.current_version_published_at}
								class="tabular-nums"
							>
								{publishedDate(item)}
							</span>
						{:else if column.key === 'body_kind'}
							<Badge variant="outline" class="font-normal">{formatLabel(item.body_kind)}</Badge>
						{:else if column.key === 'status'}
							<StatusBadge
								tone={FORM_STATUS_META[item.status as keyof typeof FORM_STATUS_META]?.tone}
							>
								{statusLabel(item)}
								{#if item.current_version_number}
									· v{item.current_version_number}
								{/if}
							</StatusBadge>
						{/if}
					{/snippet}
				</DataTable>
			</CardContent>
		</Card>
	{/if}
</div>

<Sheet.Root bind:open={createOpen}>
	<Sheet.Content>
		<Sheet.Header>
			<Sheet.Title>New terms</Sheet.Title>
			<Sheet.Description>A draft you can write or attach a PDF to, then publish.</Sheet.Description>
		</Sheet.Header>
		<form
			method="POST"
			action="?/create"
			class="space-y-4"
			use:enhance={() => {
				// A custom callback REPLACES the default, so the redirect this action returns has to be
				// followed here. Only closing the sheet swallowed it: the term was created (201) and the
				// editor never opened, which reads as the button doing nothing.
				return async ({ result }) => {
					createOpen = false;
					if (result.type === 'redirect') await goto(result.location, { invalidateAll: true });
					else await applyAction(result);
				};
			}}
		>
			<div class="space-y-1.5">
				<Label for="title">Title</Label>
				<Input id="title" name="title" bind:value={title} required />
			</div>
			<Button type="submit">Create</Button>
		</form>
	</Sheet.Content>
</Sheet.Root>
