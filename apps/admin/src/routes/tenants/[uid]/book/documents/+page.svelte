<script lang="ts">
	// The firm's DELETED documents — read-only, and the only surface that lists them now that the
	// platform library is dropping its Live/Archived switch. Nothing is hard deleted, so these rows
	// exist; they simply had no reader left.
	//
	// App-local rather than a mount of the shared DocumentsLibrary: that component is the firm's
	// working library (search, filters, bulk bar, the send/archive affordances) and is mid-refactor
	// for the same relabelling. What operations needs is one flat evidence table, so this is the
	// house DataTable directly.
	//
	// "Deleted" everywhere on screen; `archived` stays the API's word and never appears (see
	// $lib/book/deleted-documents).
	//
	// SERVER-DRIVEN: `data.query` is this listing's question and `data.documents` is one page of the
	// answer, both resolved in `+page.server.ts` against `GET /documents?status=archived`. Search and
	// the three filters live in the URL (`book/listing/query.ts`) rather than in component state, so
	// a filtered link survives a reload and the back button actually widens the list again.
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		CELL_NAME,
		DataTable,
		TruncatedText,
		type DataTableColumn,
		type DataTableSort
	} from '@graphene/ui';
	import {
		type FilterDef,
		barValueFrom,
		withBarValue,
		ListingToolbar,
		Pagination,
		isFiltered,
		optionsFromFacet,
		pageHref,
		paramsFromQuery,
		type ListingQuery
	} from '@graphene/ui/book/listing';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { BookArchivedDocument } from '$lib/book/api';
	import { documentName, typeLabel } from '$lib/book/deleted-documents';
	import { formatBytes, formatDate } from '@graphene/ui';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// Client-side still — see the loader's comment. The endpoint has nothing to hand a `sort`/`dir`
	// to, so this orders only the 25 rows on the current page rather than the whole deleted set.
	let sort = $state<DataTableSort | null>({ key: 'created_at', dir: 'desc' });

	/** Turns a changed query into a navigation, the same way every other server-driven listing does
	 *  (`transactions/+page.svelte`). `replaceState` keeps a run of filter clicks from piling up
	 *  history entries; `keepFocus`/`noScroll` so typing in the search box or picking a filter does
	 *  not yank focus or jump the scroll position. */
	function navigate(next: ListingQuery) {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	const dirty = $derived(isFiltered(data.query));
	function clear() {
		navigate({ ...data.query, q: '', filters: {}, offset: 0 });
	}
	/** Real links for the pager — shareable, middle-clickable, back-button friendly. The current
	 *  params are passed through so an unrelated one survives being paged past. */
	const hrefForPage = (n: number) => pageHref(data.query, n, page.url.searchParams);

	// Options come from the server's facets, computed over the whole match set (not the 25 rows on
	// screen) — see `optionsFromFacet`. `type` gets the library's own slug->label map; `client`
	// carries a uid (two clients can share a name), so its labels come from `facetLabels`, which the
	// endpoint computes precisely because a row on screen can only ever name the client already
	// filtered to.
	const typeOptions = $derived(optionsFromFacet(data.facets, 'type', typeLabel));
	const authorOptions = $derived(optionsFromFacet(data.facets, 'author'));
	const BAR_KEYS = ['type', 'author', 'client'] as const;

	const clientOptions = $derived(
		optionsFromFacet(data.facets, 'client', (uid) => data.facetLabels?.client?.[uid] ?? uid)
	);

	const filterDefs = $derived<FilterDef[]>([
		{ key: 'type', label: 'Type', options: typeOptions },
		{
			key: 'author',
			label: 'Created by',
			options: authorOptions,
			searchPlaceholder: 'Search staff…'
		},
		{
			key: 'client',
			label: 'Filed under',
			options: clientOptions,
			searchPlaceholder: 'Search clients…'
		}
	]);

	// `sortValue` is given on every column whose cell renders something other than the raw field —
	// a formatted size sorts numerically, a de-slugged type sorts on its label — otherwise the
	// visible order silently disagrees with the column header.
	const columns: DataTableColumn<BookArchivedDocument>[] = [
		{
			key: 'name',
			header: 'Document',
			sortable: true,
			sortValue: (r) => documentName(r).toLowerCase(),
			headerClass: 'w-[26%]'
		},
		{
			key: 'document_type',
			header: 'Type',
			sortable: true,
			sortValue: (r) => typeLabel(r.document_type),
			hideBelowWidth: 'md',
			headerClass: 'w-[14%]'
		},
		{
			key: 'client',
			header: 'Filed under',
			sortable: true,
			sortValue: (r) => r.client ?? '',
			headerClass: 'w-[18%]'
		},
		{
			key: 'created_by',
			header: 'Created by',
			sortable: true,
			sortValue: (r) => r.created_by ?? '',
			hideBelowWidth: 'xl',
			headerClass: 'w-[16%]'
		},
		{
			key: 'size_bytes',
			header: 'Size',
			sortable: true,
			align: 'right',
			defaultDir: 'desc',
			sortValue: (r) => r.size_bytes ?? null,
			hideBelowWidth: 'lg',
			headerClass: 'w-[12%]'
		},
		{
			key: 'created_at',
			header: 'Created',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (r) => r.created_at,
			headerClass: 'w-[14%]'
		}
	];
</script>

<svelte:head><title>Deleted documents · {data.tenant.name} · Operations</title></svelte:head>

<Card>
	<CardHeader>
		<CardTitle>Deleted documents</CardTitle>
		<p class="text-muted-foreground text-sm">
			Documents {data.tenant.name} has deleted. Nothing is ever destroyed — a deleted document keeps its
			file, its history and the status it will return to if it is restored. This view is read-only.
		</p>
	</CardHeader>
	<CardContent>
		{#if data.loadError}
			<!-- A failed read must never render as "nothing deleted": an operator would conclude the
			     firm's documents are intact, which is the one wrong answer this page can give. -->
			<Alert class="border-destructive/50 bg-destructive/5">
				<TriangleAlertIcon class="size-4" />
				<AlertTitle>Couldn't load this firm's deleted documents</AlertTitle>
				<AlertDescription>
					The list below is not empty because nothing was deleted — it is empty because the read
					failed. Reload to try again.
				</AlertDescription>
			</Alert>
		{:else}
			<ListingToolbar
				class="mb-4"
				query={data.query.q}
				placeholder="Search deleted documents by name"
				{dirty}
				onclear={clear}
				onsearch={(next) => navigate({ ...data.query, q: next, offset: 0 })}
				{filterDefs}
				filterValue={barValueFrom(data.query, BAR_KEYS)}
				onfilters={(next) => navigate(withBarValue(data.query, BAR_KEYS, next))}
			></ListingToolbar>

			<DataTable
				{columns}
				rows={data.documents}
				bind:sort
				getKey={(r) => r.uid}
				label="Deleted documents"
				fixed
				empty={dirty
					? 'No deleted documents match your filters.'
					: 'This firm has deleted no documents.'}
			>
				{#snippet cell(row, column)}
					{#if column.key === 'name'}
						<span class="block truncate font-medium">{documentName(row)}</span>
						<span class="text-muted-foreground text-xs">{row.source}</span>
					{:else if column.key === 'document_type'}
						{typeLabel(row.document_type)}
					{:else if column.key === 'client'}
						<!-- Bare interpolations before this: no element at all, so nothing to bound. A person's
						     name is the one string here whose length we do not control. -->
						<TruncatedText text={row.client ?? '—'} class={CELL_NAME} />
					{:else if column.key === 'created_by'}
						<TruncatedText text={row.created_by ?? '—'} class={CELL_NAME} />
					{:else if column.key === 'size_bytes'}
						{formatBytes(row.size_bytes)}
					{:else if column.key === 'created_at'}
						{formatDate(row.created_at)}
					{/if}
				{/snippet}
			</DataTable>

			<Pagination
				query={data.query}
				total={data.total}
				shown={data.documents.length}
				href={hrefForPage}
			/>
		{/if}
	</CardContent>
</Card>
