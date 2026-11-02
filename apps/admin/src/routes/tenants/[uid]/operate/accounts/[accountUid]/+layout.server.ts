import { error, redirect } from '@sveltejs/kit';
import {
	clientName,
	isPensionProduct,
	providerReference,
	type AccountDetailData
} from '@graphene/ui';
import { reasonKeys, type ReviewPolicy } from '@graphene/ui/book/reviews';
import { targetJson } from '$lib/operate';
import { accountSidebarFocus, operateBase } from '$lib/record-focus';
import type { LayoutServerLoad } from './$types';

type ModelOption = { id: number; name: string };

type PensionShape = {
	product_type: string | null;
	is_pension?: boolean;
	custodian?: string | null;
	scheme_administrator?: string | null;
};

/** The shelf's answer, with the wrapper set as the fallback — the same rule the platform
 *  account record uses. A mount over an older API that sends no column still works. */
const isPension = (a: PensionShape): boolean => a.is_pension ?? isPensionProduct(a.product_type);

/** An SEI SIPP whose scheme Morgan Lloyd administers. A Seccl SIPP is administered by Seccl.
 *  Contribute stays on any pension: only the drawdown journey is Morgan Lloyd's. */
const isMorganLloydPension = (a: PensionShape): boolean =>
	isPension(a) && a.custodian === 'sei' && a.scheme_administrator === 'morgan-lloyd';

export const load: LayoutServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const accountResult = await targetJson<AccountDetailData>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/crm/accounts/${params.accountUid}`
	);
	if (!accountResult.ok) {
		if (accountResult.status === 404) error(404, 'Account not found.');
		error(accountResult.status || 500, 'Could not load the account.');
	}
	const account = accountResult.data;
	const modelResult = writing
		? await targetJson<ModelOption[]>(fetch, locals.accessToken, tenant.uid, '/crm/models')
		: null;
	const policiesResult = writing
		? await targetJson<ReviewPolicy[]>(fetch, locals.accessToken, tenant.uid, '/reviews/policies')
		: null;
	const moneyResult = writing
		? await targetJson<{ require_justification?: string[] }>(
				fetch,
				locals.accessToken,
				tenant.uid,
				'/firm/money-actions'
			)
		: null;
	const title = account.name ?? providerReference(account.uid) ?? 'Account';
	const owner = account.client.id == null ? null : clientName(account.client);
	const base = operateBase(tenant.uid);
	return {
		sidebarFocus: accountSidebarFocus({
			prefix: base,
			uid: account.uid,
			name: title,
			subtitle: [account.product_type, owner].filter(Boolean).join(' · ') || null,
			back:
				account.client?.uid != null
					? { href: `${base}/clients/${account.client.uid}`, label: owner ?? 'Client' }
					: { href: `${base}/accounts`, label: 'Accounts' }
		}),
		account,
		holdings: [],
		instructions: [],
		feePackages: [],
		models: modelResult?.ok ? modelResult.data : [],
		writing,
		canDrawdown: writing && isMorganLloydPension(account),
		// A pension is payable into wherever its scheme is administered — only Drawdown
		// belongs to Morgan Lloyd. `canContribute` is the note under regular payments (credits
		// are categorised from Transactions), not a Contribute button.
		canContribute: writing && isPension(account),
		loadError: false,
		title,
		owner,
		justificationRequired: reasonKeys(
			policiesResult?.ok && Array.isArray(policiesResult.data) ? policiesResult.data : [],
			moneyResult?.ok && Array.isArray(moneyResult.data?.require_justification)
				? moneyResult.data.require_justification
				: []
		)
	};
};
