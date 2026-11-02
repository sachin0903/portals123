import { error, redirect } from '@sveltejs/kit';
import { authHeaders } from '$lib/api';
import { bookUrl } from '$lib/book/api';
import type { AccountDetailData } from '@graphene/ui';
import type { LayoutServerLoad } from './$types';

/**
 * One account of the target firm — the shell. Holdings and the ledger belong to the page, so this
 * layout is the account itself with `?tenant=` on the read. Without that param the API resolves
 * the OPERATIONS tenant's own empty book.
 */
export const load: LayoutServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const headers = authHeaders(locals.accessToken);

	const res = await fetch(bookUrl(`/crm/accounts/${params.accountUid}`, tenant.uid), {
		headers
	});
	if (res.status === 404) error(404, 'Account not found in this firm’s book.');
	if (!res.ok) error(res.status, 'Could not load the account.');
	const account: AccountDetailData = await res.json();
	return { account };
};
