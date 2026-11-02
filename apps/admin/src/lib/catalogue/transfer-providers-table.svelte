<script module lang="ts">
	import type { FilterDef } from '@graphene/ui/book/listing';

	export function transferProviderFilterDefs(facets: {
		products: string[];
		rails: string[];
	}): FilterDef[] {
		return [
			{
				key: 'product',
				label: 'Product',
				options: facets.products.map((p) => ({ value: p, label: p })),
				allLabel: 'All products'
			},
			{
				key: 'rail',
				label: 'Rail',
				options: facets.rails.map((r) => ({ value: r, label: r })),
				allLabel: 'All providers'
			}
		];
	}
</script>

<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page as pageStore } from '$app/state';
	import {
		Badge,
		Card,
		CardContent,
		DataTable,
		SectionHeading,
		Pagination,
		Switch,
		toast,
		type DataTableColumn,
		type DataTableSort
	} from '@graphene/ui';
	import { ListingToolbar } from '@graphene/ui/book/listing';
	import type { Provider } from './types';

	let {
		providers,
		total,
		page,
		pageSize,
		sort,
		dir,
		q,
		product,
		rail,
		facets,
		canManage,
		form
	}: {
		providers: Provider[];
		total: number;
		page: number;
		pageSize: number;
		sort: string;
		dir: 'asc' | 'desc';
		q: string;
		product: string[];
		rail: string[];
		facets: { products: string[]; rails: string[] };
		canManage: boolean;
		form: { error?: string; saved?: string } | null;
	} = $props();

	const filterDefs = $derived(transferProviderFilterDefs(facets));
	const barValue = $derived<Record<string, string[]>>({
		...(product.length ? { product } : {}),
		...(rail.length ? { rail } : {})
	});
	function setFilters(next: Record<string, string[]>): void {
		navigate({ product: next.product ?? [], rail: next.rail ?? [], page: '1' });
	}

	// 929 ceding schemes on a single Seccl connection, so this listing pages and sorts on the
	// server. Everything the table shows about ordering and position therefore describes the whole
	// register, not the 25 rows in the DOM.
	const pageCount = $derived(Math.max(1, Math.ceil(total / pageSize)));
	const tableSort = $derived<DataTableSort | null>({ key: sort, dir });
	const firstOnPage = $derived(total === 0 ? 0 : (page - 1) * pageSize + 1);
	const lastOnPage = $derived(Math.min(page * pageSize, total));

	/** Only a status the platform actively contradicts counts as unavailable; absent is unknown. */
	const active = (p: Provider): boolean => !p.status || p.status.toLowerCase() === 'active';

	const columns: DataTableColumn<Provider>[] = [
		{
			key: 'name',
			header: 'Provider',
			sortable: true,
			headerClass: 'w-[28%]',
			cellClass: 'whitespace-normal'
		},
		{
			// Table cells are `whitespace-nowrap` by default (the primitive), which is right for a
			// money or date column and wrong for this one: a provider on two rails carries up to six
			// segments, and unwrappable content sets the column's minimum width, so the table
			// overflowed and scrolled with the container half empty. Wrapping here lets the badges
			// stack instead of pushing the table wider than its card.
			key: 'rails',
			header: 'Providers',
			sortable: true,
			defaultDir: 'desc',
			sortValue: (provider) => provider.rails?.length ?? 0,
			headerClass: 'w-[38%]',
			cellClass: 'whitespace-normal'
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			hideBelow: 'md',
			headerClass: 'w-[20%]'
		},
		{
			key: 'enabled',
			header: 'Enabled',
			align: 'center',
			sortable: true,
			headerClass: 'w-[14%] text-center',
			cellClass: 'text-center'
		}
	];

	/** Sort and page both live in the URL, so a sorted page is linkable and survives a reload. A
	 *  list value becomes one repeated `?key=` param per entry; an empty list clears the key. */
	function navigate(next: Record<string, string | string[]>): void {
		const params = new URLSearchParams(pageStore.url.searchParams);
		for (const [key, value] of Object.entries(next)) {
			params.delete(key);
			if (Array.isArray(value)) {
				for (const one of value) if (one) params.append(key, one);
			} else {
				params.set(key, value);
			}
		}
		void goto(`?${params}`, { keepFocus: true, noScroll: true });
	}

	// Bits' onCheckedChange passes the new value and no event, so the row's form is found by id.
	function submitRow(id: number): void {
		const el = document.getElementById(`tp-${id}`);
		if (el instanceof HTMLFormElement) el.requestSubmit();
	}

	function onSort(next: DataTableSort | null): void {
		// Back to page 1: the row that was 3rd on page 12 is not 3rd on page 12 of a new ordering,
		// so keeping the offset would land the operator somewhere arbitrary.
		navigate({ sort: next?.key ?? 'name', dir: next?.dir ?? 'asc', page: '1' });
	}

	$effect(() => {
		if (form?.saved) toast.success('Provider updated');
		else if (form?.error) toast.error(form.error);
	});
</script>

<div class="space-y-5">
	<div>
		<SectionHeading title="Transfer providers" count={total.toLocaleString()} />
		<p class="text-muted-foreground text-sm">
			Ceding schemes a transfer can be addressed to, and the rails each is reachable on. Synced from
			the custodian and switched off until someone turns one on.
		</p>
	</div>

	<Card glass>
		<!-- Toolbar INSIDE the panel, above the table, the same frame the platform portal's clients
		     and accounts listings use. The COMPONENT, not a copy of its structure: an earlier note
		     here said it could only filter client-side, which was wrong — `onsearch` is its
		     server-driven mode, and it carries the debounce, the Enter-skips-the-timer behaviour
		     and the Clear button this panel had reimplemented. -->
		<CardContent class="space-y-4">
			<!-- Every control applies itself: the filter state lives in the URL, so navigating IS
			     applying, and an Apply button would only add a step between the two. Each change
			     resets to page 1 — page 12 of an unfiltered register is usually past the end of a
			     filtered one. -->
			<ListingToolbar
				query={q}
				placeholder="Search providers, Origo or ATG reference"
				dirty={Boolean(q || product.length || rail.length)}
				onsearch={(term) => navigate({ q: term, page: '1' })}
				onclear={() => goto('?')}
				{filterDefs}
				filterValue={barValue}
				onfilters={setFilters}
			/>

			<DataTable
				{columns}
				rows={providers}
				label="Transfer providers"
				sort={tableSort}
				{onSort}
				getKey={(p) => p.id}
				fixed
				empty="No transfer providers are available yet. Refresh the platform data to check again."
			>
				{#snippet cell(provider, column)}
					{#if column.key === 'name'}
						<span class="font-medium">{provider.name}</span>
					{:else if column.key === 'rails'}
						<!-- One group per rail: the rail name is the darker leading segment, the products it
					     carries follow it. A provider reachable on Origo AND ATG carries different
					     products on each, so the products cannot be shown detached from the rail. -->
						<div class="flex flex-wrap items-center gap-2">
							{#each provider.rails ?? [] as rail (rail.rail + rail.reference)}
								<span class="inline-flex items-center overflow-hidden rounded border">
									<span
										class="bg-muted text-foreground border-r px-1.5 py-0.5 text-xs font-medium"
										title={rail.reference ?? undefined}
									>
										{rail.rail}
									</span>
									{#each rail.products ?? [] as product (product)}
										<span
											class="text-muted-foreground border-r px-1.5 py-0.5 text-xs last:border-r-0"
										>
											{product}
										</span>
									{/each}
								</span>
							{:else}
								<span class="text-muted-foreground text-xs">No rails published</span>
							{/each}
						</div>
					{:else if column.key === 'status'}
						<Badge variant={active(provider) ? 'secondary' : 'destructive'} class="font-normal">
							{provider.status ?? 'unknown'}
						</Badge>
					{:else if column.key === 'enabled'}
						<form
							id="tp-{provider.id}"
							method="POST"
							action="?/provider"
							use:enhance
							class="flex justify-center"
						>
							<input type="hidden" name="kind" value="transfer" />
							<input type="hidden" name="uid" value={provider.uid} />
							<input type="hidden" name="name" value={provider.name} />
							<input type="hidden" name="enabled" value={provider.enabled ? '' : 'on'} />
							<!-- A suspended provider cannot be enabled: a transfer addressed to one fails at
						     the custodian, after a client has been told it is under way. The rule is
						     enforced in catalogue.upsert_provider; this only stops the click. An already-
						     enabled provider that has since been suspended stays toggleable so it can be
						     switched OFF. -->
							<Switch
								checked={provider.enabled}
								disabled={!canManage || (!active(provider) && !provider.enabled)}
								title={!active(provider) && !provider.enabled
									? `${provider.status ?? 'Not active'} at the custodian. It cannot be enabled.`
									: undefined}
								aria-label="Enable {provider.name}"
								onCheckedChange={() => submitRow(provider.id)}
							/>
						</form>
					{/if}
				{/snippet}
			</DataTable>

			{#if total > 0}
				<div class="flex flex-wrap items-center justify-between gap-3">
					<!-- The count is the register's, not the page's: it is the only thing on screen that
				     says how much is behind this view. -->
					<p class="text-muted-foreground text-xs tabular-nums">
						{firstOnPage.toLocaleString('en-GB')}–{lastOnPage.toLocaleString('en-GB')}
						of {total.toLocaleString('en-GB')}
					</p>
					{#if pageCount > 1}
						<!-- Link mode, not bindable: the rows come from the server loader, so a page control
					     that only moved local state would change the highlight and not the data. -->
						<Pagination
							{page}
							{pageCount}
							href={(n) => {
								const params = new URLSearchParams(pageStore.url.searchParams);
								params.set('page', String(n));
								return `?${params}`;
							}}
						/>
					{/if}
				</div>
			{/if}
		</CardContent>
	</Card>
</div>
