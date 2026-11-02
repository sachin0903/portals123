import { redirect } from '@sveltejs/kit';
import { type FeePackageOption, type InstructionRow } from '@graphene/ui';
import { mapOperateHoldings } from '$lib/operate-holdings';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';

type RawHolding = Record<string, unknown>;

/**
 * The operate-as-firm account overview: this account's holdings, instructions and fee catalogue.
 *
 * The account itself comes from the layout; this load adds what the landing view states. Holdings
 * and the ledger live on their own routes so a date filter on Transactions does not re-read the lake.
 */
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, account, writing } = await parent();
	const [holdingResult, instructionResult, feeResult] = await Promise.all([
		targetJson<RawHolding[]>(fetch, locals.accessToken, tenant.uid, '/crm/holdings', {
			account_id: account.id
		}),
		writing && account.client.uid
			? targetJson<InstructionRow[]>(
					fetch,
					locals.accessToken,
					tenant.uid,
					`/firm/contacts/${account.client.uid}/instructions`,
					{ account_id: account.id }
				)
			: null,
		writing
			? targetJson<FeePackageOption[]>(
					fetch,
					locals.accessToken,
					tenant.uid,
					'/catalog/fee-packages',
					{
						...(account.product_id == null ? {} : { product_id: account.product_id }),
						...(account.model_id == null ? {} : { model_id: account.model_id })
					}
				)
			: null
	]);
	return {
		holdings: mapOperateHoldings(holdingResult.ok ? holdingResult.data : [], account, tenant.uid),
		instructions: instructionResult?.ok
			? instructionResult.data.filter((row) => row.account_id === account.id)
			: [],
		feePackages: feeResult?.ok ? feeResult.data : [],
		loadError: !holdingResult.ok
	};
};
