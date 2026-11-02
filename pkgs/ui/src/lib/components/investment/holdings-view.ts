import { classifyHolding } from '../book/clients/lake.js';
import { ALL, matches, search } from '../book/listing/filters.js';
import type { DataTableColumn } from '../ui/data-table/index.js';
import { allocationShare } from './allocation.js';
import type { Holding } from './types.js';

/**
 * The holdings table's view of a set of positions — its columns, its sort values, and the account
 * band's search and filters — as plain data, so the table and the holdings export read ONE
 * definition. The export repeating them is how its file came to disagree with the screen.
 */

/** The account holdings band's search and filters. An absent filter is no filter. */
export interface HoldingsFilter {
	q?: string;
	assetClass?: string;
	currency?: string;
}

/** The asset class a position is filtered under — the allocation ring's own rule, so a reader who
 *  clicks "Equities" gets the rows that made the Equities segment. */
export const holdingClass = (h: Pick<Holding, 'name'>) => classifyHolding({ asset_name: h.name });

/** Whether a position passes the band's search and filters. */
export function holdingMatches(h: Holding, filter: HoldingsFilter): boolean {
	return (
		search(filter.q ?? '', h.name, h.ticker, h.isin, h.sedol) &&
		matches(filter.assetClass || ALL, holdingClass(h)) &&
		matches(filter.currency || ALL, h.currency)
	);
}

/** The holdings table's columns, in order. Allocation exists only with a positive total to be a
 *  share OF; Account only where several accounts' positions share one table. */
export function holdingColumns({
	showWrapper = false,
	allocationBase = null
}: {
	showWrapper?: boolean;
	allocationBase?: number | null;
}) {
	const showAllocation = allocationBase != null && allocationBase > 0;
	return [
		{ key: 'name', header: 'Investment', sortable: true },
		...(showWrapper ? [{ key: 'wrapper' as const, header: 'Account', sortable: true }] : []),
		{ key: 'isin', header: 'ISIN', sortable: true, hideBelowWidth: '2xl' as const },
		{ key: 'sedol', header: 'SEDOL', sortable: true, hideBelowWidth: '3xl' as const },
		{
			key: 'units',
			header: 'Units',
			sortable: true,
			align: 'right' as const,
			defaultDir: 'desc' as const
		},
		{
			key: 'currency',
			header: 'Currency',
			sortable: true,
			sortValue: (h: Holding) => h.currency ?? ''
		},
		{
			key: 'price',
			header: 'Price',
			sortable: true,
			align: 'right' as const,
			defaultDir: 'desc' as const
		},
		{
			key: 'bookCost',
			header: 'Book cost',
			sortable: true,
			align: 'right' as const,
			defaultDir: 'desc' as const,
			hideBelowWidth: '4xl' as const
		},
		{
			key: 'value',
			header: 'Value',
			sortable: true,
			align: 'right' as const,
			defaultDir: 'desc' as const
		},
		...(showAllocation
			? [
					{
						key: 'allocation' as const,
						header: 'Allocation',
						sortable: true,
						align: 'right' as const,
						defaultDir: 'desc' as const,
						sortValue: (h: Holding) => allocationShare(h.value, allocationBase)
					}
				]
			: []),
		{
			key: 'gainPct',
			header: 'Unrealised gain',
			sortable: true,
			align: 'right' as const,
			defaultDir: 'desc' as const
		},
		{ key: 'lookthroughHref', header: '', align: 'right' as const }
	] satisfies DataTableColumn<Holding>[];
}
