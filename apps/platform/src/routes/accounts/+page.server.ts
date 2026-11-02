import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, type Account } from '$lib/api';
import type { AccountFilterOption } from '@graphene/ui';
import type { CatalogModel, CatalogProduct } from '@graphene/ui/book/journeys';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { FILTER_KEYS, SORT } from './listing.js';
import type { PageServerLoad } from './$types';

const productTypeOptions = (products: CatalogProduct[]): AccountFilterOption[] => {
	const types = new Set(
		products
			.filter((product) => product.enabled !== false)
			.flatMap((product) => {
				const type = product.product_type?.trim();
				return type ? [type] : [];
			})
	);
	return [...types]
		.sort((a, b) => a.localeCompare(b, 'en-GB', { numeric: true, sensitivity: 'base' }))
		.map((value) => ({
			value,
			label: value.replace(/_/g, ' ').replace(/^./, (letter) => letter.toUpperCase())
		}));
};

const modelOptions = (models: CatalogModel[]): AccountFilterOption[] =>
	models
		.map((model) => ({ value: String(model.id), label: model.name }))
		.sort((a, b) =>
			a.label.localeCompare(b.label, 'en-GB', { numeric: true, sensitivity: 'base' })
		);

/** Everything the listing has to fetch, in one streamed object. */
type Book = {
	accounts: Account[];
	matchCount: number;
	facets: Record<string, string[]>;
	bounds: Record<string, number[]>;
	productTypeOptions: AccountFilterOption[];
	modelOptions: AccountFilterOption[];
};

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// Search, filters and the page live in the URL and are applied by the API. Client-side over
	// one page would be a lie about the rest of the book — "no account for Halloway" when the
	// truth is "not on this page".
	//
	// The query is resolved here and the rows are STREAMED: the page renders on the click with its
	// toolbar already showing the filters in the URL, and fills in when the API answers. `redirect`
	// stays out here — it must be thrown before the response starts, not from a streamed promise.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, SORT);
	return { query, book: loadBook(locals.accessToken, fetch, query) };
};

async function loadBook(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: ReturnType<typeof queryFromParams>
): Promise<Book> {
	// RLS scopes the rows to what this caller may see (the whole firm for a read-all adviser, their
	// own book otherwise); the filters narrow inside that, never beyond it.
	const headers = authHeaders(accessToken);
	const [res, productsRes, modelsRes] = await Promise.all([
		fetch(`${apiUrl()}/crm/accounts?${apiParams(query)}`, { headers }),
		fetch(`${apiUrl()}/catalog/products`, { headers }),
		fetch(`${apiUrl()}/catalog/models`, { headers })
	]);
	// An empty page rather than a 500: the listing renders its own empty state, and a failed
	// read here is not worth taking the whole route down for.
	const page: ListingPage<Account> = res.ok
		? await res.json()
		: { items: [], total: 0, facets: {} };
	const products: CatalogProduct[] = productsRes.ok ? await productsRes.json() : [];
	const models: CatalogModel[] = modelsRes.ok ? await modelsRes.json() : [];
	return {
		accounts: page.items,
		matchCount: page.total,
		facets: page.facets,
		bounds: page.bounds ?? {},
		productTypeOptions: productTypeOptions(Array.isArray(products) ? products : []),
		modelOptions: modelOptions(Array.isArray(models) ? models : [])
	};
}
