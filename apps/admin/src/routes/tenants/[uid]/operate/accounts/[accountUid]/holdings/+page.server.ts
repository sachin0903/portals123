import { redirect } from '@sveltejs/kit';
import { mapOperateHoldings } from '$lib/operate-holdings';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

type RawHolding = Record<string, unknown>;

/** This account's positions. Loaded here so opening Holdings does not also pay for instructions. */
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, account } = await parent();
	const holdingResult = await targetJson<RawHolding[]>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/crm/holdings',
		{ account_id: account.id }
	);
	return {
		holdings: mapOperateHoldings(holdingResult.ok ? holdingResult.data : [], account, tenant.uid),
		loadError: !holdingResult.ok
	};
};
