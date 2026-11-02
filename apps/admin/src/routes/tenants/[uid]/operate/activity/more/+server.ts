import { actionLogFiltersFromSearchParams, actionLogQuery } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { fetchActionsPage } from '$lib/audit/actions-page';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	const query = actionLogQuery(actionLogFiltersFromSearchParams(url.searchParams), {
		limit: PAGE_SIZE,
		before: url.searchParams.get('before'),
		after: url.searchParams.get('after')
	});
	query.set('tenant', params.uid);
	return fetchActionsPage(fetch, '/admin/audit/actions', query, locals);
};
