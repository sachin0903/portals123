import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import type { Transaction } from '@graphene/ui';
import { apiUrl, authHeaders } from '$lib/api';
import { FILTER_KEYS, SORT } from './listing';
import type { PageServerLoad } from './$types';

/**
 * The ledger for this account. Loaded here (not on the record layout) so changing filters
 * does not re-fetch holdings, instructions or fee packages.
 */
export const load: PageServerLoad = async ({ parent, locals, fetch, url }) => {
	const { account } = await parent();
	const transactionQuery = queryFromParams(url.searchParams, FILTER_KEYS, SORT);
	return {
		transactionQuery,
		transactionFilter: url.searchParams.get('isin') ?? url.searchParams.get('asset'),
		band: loadBand(locals.accessToken, fetch, account.id, transactionQuery, url)
	};
};

async function loadBand(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	accountId: number,
	transactionQuery: ReturnType<typeof queryFromParams>,
	url: URL
) {
	const headers = authHeaders(accessToken);
	const transactionParams = new URLSearchParams(apiParams(transactionQuery));
	transactionParams.set('account_id', String(accountId));
	transactionParams.set('paged', 'true');
	const selectedIsin = url.searchParams.get('isin');
	const selectedAsset = url.searchParams.get('asset');
	if (selectedIsin) transactionParams.set('isin', selectedIsin);
	else if (selectedAsset) transactionParams.set('q', selectedAsset);

	const txRes = await fetch(`${apiUrl()}/crm/transactions?${transactionParams}`, { headers });
	const transactionPayload: ListingPage<Transaction> | Transaction[] = txRes.ok
		? await txRes.json()
		: [];
	const transactions = Array.isArray(transactionPayload)
		? transactionPayload
		: transactionPayload.items;
	const transactionTotal = Array.isArray(transactionPayload)
		? transactionPayload.length
		: transactionPayload.total;
	// Facet options are computed server-side over the MATCH SET, so the type filter offers the types
	// this account actually has rather than every type in the product.
	const transactionFacets = Array.isArray(transactionPayload)
		? {}
		: (transactionPayload.facets ?? {});

	return {
		transactions,
		transactionQuery,
		transactionTotal,
		transactionFacets,
		transactionFilter: selectedIsin ?? selectedAsset,
		loadError: !txRes.ok
	};
}
