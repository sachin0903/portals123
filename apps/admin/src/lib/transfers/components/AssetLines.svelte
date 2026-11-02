<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Button,
		DataTable,
		type DataTableColumn,
		type DataTableSort,
		RecordId,
		formatGBP
	} from '@graphene/ui';
	import type { TransfersStore } from '$lib/transfers/store.svelte';
	import type { Asset } from '$lib/transfers/types';

	let { wb }: { wb: TransfersStore } = $props();

	const assets = $derived(wb.selected?.assets ?? []);
	const missing = $derived(assets.filter((a) => a.book == null).length);

	// Every column key matches the field it reads, so the default row-value sort already gives
	// the right order (money on the number, `book` on `null | number` — `null` sorts last, which
	// is exactly "unset" belongs).
	const columns: DataTableColumn<Asset>[] = [
		{ key: 'name', header: 'Asset', sortable: true, sortDefault: true },
		{
			key: 'qty',
			header: 'Qty',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		},
		{
			key: 'price',
			header: 'Price',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		},
		{
			key: 'value',
			header: 'Value',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		},
		{
			key: 'book',
			header: 'Book cost',
			align: 'right',
			cellClass: 'tabular-nums',
			sortable: true,
			defaultDir: 'desc'
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal passed in
	// the template is recreated on every update, stamping the reader's chosen column back to the
	// default — a sort that silently undoes itself.
	let sort = $state<DataTableSort | null>(null);
</script>

<div class="space-y-2">
	{#if missing > 0}
		<Alert class="border-warning-line bg-warning-subtle">
			<AlertDescription class="text-warning-strong flex items-center justify-between gap-2">
				<span>{missing} line(s) missing a book cost — required before CREST settles.</span>
				<Button size="sm" variant="outline" onclick={() => wb.runOp('seccl_bookcost')}
					>Set book cost</Button
				>
			</AlertDescription>
		</Alert>
	{/if}
	<div class="border-border/70 overflow-x-auto rounded-lg border">
		<DataTable
			{columns}
			rows={assets}
			bind:sort
			getKey={(a) => a.isin}
			label="Asset lines"
			empty="No asset lines."
		>
			{#snippet cell(a, column)}
				{#if column.key === 'name'}
					<span class="text-xs font-medium">{a.name}</span>
					<RecordId value={a.isin} class="text-[10px]" />
				{:else if column.key === 'qty'}
					{a.qty.toLocaleString('en-GB')}
				{:else if column.key === 'price'}
					{formatGBP(a.price)}
				{:else if column.key === 'value'}
					{formatGBP(a.value)}
				{:else if column.key === 'book'}
					{#if a.book == null}<span class="text-warning-strong">unset</span>{:else}{formatGBP(
							a.book
						)}{/if}
				{/if}
			{/snippet}
		</DataTable>
	</div>
</div>
