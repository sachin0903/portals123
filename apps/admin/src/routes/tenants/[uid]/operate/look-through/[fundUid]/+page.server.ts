import { error, redirect } from '@sveltejs/kit';
import type { FundLookthroughData } from '@graphene/ui/book/lookthrough';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, url, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const result = await targetJson<FundLookthroughData>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/crm/look-through/${encodeURIComponent(params.fundUid)}`,
		{
			as_of: url.searchParams.get('as_of'),
			account: url.searchParams.get('account')
		}
	);
	if (!result.ok) {
		if (result.status === 404) error(404, 'Fund look-through not found.');
		error(result.status || 500, 'Could not load fund look-through.');
	}
	return { lookthrough: result.data };
};
