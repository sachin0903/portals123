import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, type AccountDetail } from '$lib/api';
import { mapAccountHoldings, type RawHolding } from '$lib/components/accounts/holdings';
import type { PageServerLoad } from './$types';

/**
 * This account's positions. Loaded here (not on the record layout) so opening Holdings does not
 * also pay for instructions, fee packages or the contribution summary — and so a ledger filter
 * never re-reads the lake.
 */
export const load: PageServerLoad = async ({ parent, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const { account } = await parent();
	return {
		band: loadBand(locals.accessToken, fetch, account)
	};
};

async function loadBand(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	account: AccountDetail
) {
	const holdingsRes = await fetch(`${apiUrl()}/crm/holdings?account_id=${account.id}`, {
		headers: authHeaders(accessToken)
	});
	const raw: RawHolding[] = holdingsRes.ok ? await holdingsRes.json() : [];
	return {
		holdings: mapAccountHoldings(raw, account),
		loadError: !holdingsRes.ok
	};
}
