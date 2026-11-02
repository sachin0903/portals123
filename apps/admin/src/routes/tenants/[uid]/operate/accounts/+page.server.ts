import { redirect } from '@sveltejs/kit';
import type { Account, AccountFilterOption } from '@graphene/ui';
import type { CatalogModel, CatalogProduct } from '@graphene/ui/book/journeys';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

/** The platform's `/accounts` filters — the same listing, server-driven, through `?tenant=`. */
const FILTER_KEYS = ['status', 'adviser', 'type', 'service', 'model', 'value'] as const;

const byLabel = (a: AccountFilterOption, b: AccountFilterOption) =>
	a.label.localeCompare(b.label, 'en-GB', { numeric: true, sensitivity: 'base' });

const productTypeOptions = (products: CatalogProduct[]): AccountFilterOption[] =>
	[
		...new Set(
			products
				.filter((product) => product.enabled !== false)
				.flatMap((product) => {
					const type = product.product_type?.trim();
					return type ? [type] : [];
				})
		)
	]
		.map((value) => ({
			value,
			label: value.replace(/_/g, ' ').replace(/^./, (letter) => letter.toUpperCase())
		}))
		.sort(byLabel);

const modelOptions = (models: CatalogModel[]): AccountFilterOption[] =>
	models.map((model) => ({ value: String(model.id), label: model.name })).sort(byLabel);

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'name', dir: 'asc' });
	return { query, book: loadBook(fetch, locals.accessToken, tenant.uid, query) };
};

async function loadBook(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	query: ReturnType<typeof queryFromParams>
) {
	const [accounts, products, models] = await Promise.all([
		targetJson<ListingPage<Account>>(
			fetch,
			accessToken,
			tenantUid,
			'/crm/accounts',
			new URLSearchParams(apiParams(query))
		),
		targetJson<CatalogProduct[]>(fetch, accessToken, tenantUid, '/catalog/products'),
		targetJson<CatalogModel[]>(fetch, accessToken, tenantUid, '/catalog/models')
	]);
	const page = accounts.ok ? accounts.data : { items: [], total: 0, facets: {} };
	return {
		accounts: page.items,
		matchCount: page.total,
		facets: page.facets,
		bounds: page.bounds ?? {},
		productTypeOptions: productTypeOptions(
			products.ok && Array.isArray(products.data) ? products.data : []
		),
		modelOptions: modelOptions(models.ok && Array.isArray(models.data) ? models.data : []),
		loadError: !accounts.ok
	};
}
