import { error, redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { PageServerLoad } from './$types';

/**
 * One entity's rows for one firm.
 *
 * Search, sort and paging all live in the URL and are all resolved SERVER-side. Sorting here
 * rather than in the table is not a preference: `rows` is one page of a server-paginated set,
 * so a client-side sort would order the rows on screen and present that as the order of the
 * whole entity — the trap `@graphene/ui`'s DataTable documents as `sortDisabled`.
 */
export const load: PageServerLoad = async ({ locals, params, url, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');

	const { provider, entities } = await parent();
	const entity = entities.find((e) => e.key === params.entity);
	if (!entity) error(404, 'Unknown entity');

	const q = url.searchParams.get('q') ?? '';
	const sort = url.searchParams.get('sort') ?? '';
	const direction = url.searchParams.get('dir') === 'asc' ? 'asc' : 'desc';
	const page = Math.max(1, Number(url.searchParams.get('page') ?? 1) || 1);

	// An entity with no source has nothing to fetch; the page renders the reason instead. Asking
	// anyway would turn a known state into a 503 that looks like an outage.
	if (entity.availability !== 'ok') {
		return {
			entity,
			columns: [] as string[],
			items: [] as Record<string, unknown>[],
			total: 0,
			missingColumns: [] as string[],
			filters: { q, sort, direction, page },
			pageSize: PAGE_SIZE,
			loadError: null
		};
	}

	const search = new URLSearchParams({
		limit: String(PAGE_SIZE),
		offset: String((page - 1) * PAGE_SIZE),
		direction
	});
	if (q.trim()) search.set('q', q.trim());
	if (sort) search.set('sort', sort);

	const res = await fetch(
		`${apiUrl()}/admin/platforms/${provider}/firms/${params.uid}/entities/${params.entity}?${search}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!res.ok) {
		return {
			entity,
			columns: [] as string[],
			items: [] as Record<string, unknown>[],
			total: 0,
			missingColumns: [] as string[],
			filters: { q, sort, direction, page },
			pageSize: PAGE_SIZE,
			loadError: await detail(res)
		};
	}
	const body = (await res.json()) as {
		columns: string[];
		items: Record<string, unknown>[];
		total: number;
		missing_columns: string[];
	};

	return {
		entity,
		columns: body.columns,
		items: body.items,
		total: body.total,
		missingColumns: body.missing_columns ?? [],
		filters: { q, sort, direction, page },
		pageSize: PAGE_SIZE,
		loadError: null
	};
};
