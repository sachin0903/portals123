import { redirect } from '@sveltejs/kit';
import type { Transaction } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, account } = await parent();
	const result = await targetJson<Transaction[]>(
		fetch,
		locals.accessToken,
		tenant.uid,
		'/crm/transactions',
		{
			account_id: account.id,
			limit: PAGE_SIZE,
			...Object.fromEntries(url.searchParams)
		}
	);
	return {
		transactions: result.ok ? result.data : [],
		transactionsError: !result.ok,
		loadError: !result.ok
	};
};
