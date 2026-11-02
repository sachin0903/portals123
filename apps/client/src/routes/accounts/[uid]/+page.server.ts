import { loginPath } from '@graphene/auth';
import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, type Account, type Holding, type Transaction } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, fetch, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	const headers = authHeaders(locals.accessToken);

	// The URL names the account by its `uid`; `platform.accounts.id` is the database's own key and
	// never appears in one. RLS gates it either way: a 404 means absent or not one of this client's
	// accounts, and the resolve runs inside the same session, so the two are indistinguishable.
	const accRes = await fetch(`${apiUrl()}/crm/accounts/${params.uid}`, { headers });
	if (accRes.status === 404) error(404, 'Account not found');
	if (!accRes.ok) error(accRes.status, 'Could not load account');
	const account: Account = await accRes.json();

	// `account_id` is a FILTER on both of these, not an address — it comes from the record just
	// read, so addressing by uid costs no extra round trip.
	const [holdRes, txRes, actionsRes] = await Promise.all([
		fetch(`${apiUrl()}/crm/holdings?account_id=${account.id}`, { headers }),
		fetch(`${apiUrl()}/crm/transactions?account_id=${account.id}&limit=50`, { headers }),
		fetch(`${apiUrl()}/crm/money/actions`, { headers })
	]);
	// A failed fetch surfaces as a retry banner rather than an empty panel — a transient failure
	// must not read as "no holdings/activity". The actions list failing leaves both buttons.
	const holdings: Holding[] = holdRes.ok ? await holdRes.json() : [];
	const transactions: Transaction[] = txRes.ok ? await txRes.json() : [];
	const actionsBody = actionsRes.ok ? ((await actionsRes.json()) as { disabled?: unknown }) : {};
	const disabledActions = Array.isArray(actionsBody.disabled)
		? actionsBody.disabled.filter((key): key is string => typeof key === 'string')
		: [];
	return {
		account,
		holdings,
		transactions,
		disabledActions,
		loadError: !holdRes.ok || !txRes.ok
	};
};
