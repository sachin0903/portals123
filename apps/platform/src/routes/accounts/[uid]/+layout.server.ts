import { error, redirect } from '@sveltejs/kit';
import { emptyQuery } from '@graphene/ui/book/listing';
import { clientName, providerReference, type Transaction } from '@graphene/ui';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, type AccountDetail } from '$lib/api';
import { accountSidebarFocus } from '@graphene/ui/nav';
import { isMorganLloydPension, isPension } from '$lib/components/accounts/pension';
import { reasonKeys, type ReviewPolicy } from '@graphene/ui/book/reviews';
import type { LayoutServerLoad } from './$types';

/**
 * The account record's shell, loaded once for every section of it.
 *
 * The layer that makes the record read as "the book, focused on this account": it publishes
 * `sidebarFocus`, which the root layout turns into the sidebar's context header + section strip,
 * so the tabs that used to sit inside `AccountDetail` are the chrome now — exactly the shape the
 * client record already had (`clients/[uid]/+layout.server.ts`).
 *
 * The account itself and the permission flags live HERE. Holdings, fee packages, beneficiaries,
 * contribution figures and instructions belong to the pages that paint them: putting those extras
 * on this layout meant switching to Transactions (or narrowing the ledger) re-fetched all five
 * before the band could render.
 *
 * The URL names the account by its `uid`; `platform.accounts.id` is the database's own key and
 * never appears in one. `by-uid` returns the whole record, so addressing by uid costs the same
 * single call — and `account.id` is what the child loaders and the record's own money actions
 * pass on to the id-typed endpoints.
 */

/** The transactions band's own filter keys, shared with its page loader. Underscore-prefixed
 *  because SvelteKit rejects an unrecognised export from a route module at request time — see
 *  `route-exports.test.ts`, which is what catches this. */
export const _TRANSACTION_FILTERS = [
	'status',
	'type',
	'custodian',
	'date_from',
	'date_to'
] as const;

export const load: LayoutServerLoad = async ({ locals, params, fetch, depends }) => {
	if (!locals.user) redirect(303, '/login');
	depends('app:account');
	const headers = authHeaders(locals.accessToken);

	const res = await fetch(`${apiUrl()}/crm/accounts/${params.uid}`, { headers });
	if (res.status === 404) error(404, 'Account not found');
	if (!res.ok) error(res.status, 'Could not load account');
	const account: AccountDetail = await res.json();

	const canReadInstructions = can(locals.permissions, locals.role, 'instructions:read');
	const canMoveMoney = can(locals.permissions, locals.role, 'instructions:write');
	const canEditContact = can(locals.permissions, locals.role, 'contacts:write');
	const canSyncMorganLloyd = can(locals.permissions, locals.role, 'contacts:sync_morgan_lloyd');
	// A switched-off action hides its control. A missing policy, or a failed read, leaves it.
	// The reason flag lives here too, so every section of the record asks when a send requires one.
	// Book policies do not list a payment, withdrawal or fee. Those keys are on the money-actions
	// read, which is the same permission as the controls.
	let disabledActions: string[] = [];
	let moneyReasons: string[] = [];
	let justificationRequired: string[] = [];
	const wantActions = canMoveMoney || canReadInstructions;
	const [actionsRes, policiesRes] = await Promise.all([
		wantActions ? fetch(`${apiUrl()}/firm/money-actions`, { headers }) : null,
		canMoveMoney ? fetch(`${apiUrl()}/reviews/policies`, { headers }) : null
	]);
	if (actionsRes?.ok) {
		const body = (await actionsRes.json()) as {
			disabled?: unknown;
			require_justification?: unknown;
		};
		disabledActions = Array.isArray(body.disabled)
			? body.disabled.filter((key): key is string => typeof key === 'string')
			: [];
		moneyReasons = Array.isArray(body.require_justification)
			? body.require_justification.filter((key): key is string => typeof key === 'string')
			: [];
	}
	if (canMoveMoney) {
		const policies = policiesRes?.ok ? await policiesRes.json() : [];
		justificationRequired = reasonKeys(
			Array.isArray(policies) ? (policies as ReviewPolicy[]) : [],
			moneyReasons
		);
	}

	const title = account.name ?? providerReference(account.uid) ?? 'Account';
	const owner = account.client?.id == null ? null : clientName(account.client);

	return {
		account,
		holdings: [],
		beneficiaries: [],
		transactions: [] as Transaction[],
		transactionQuery: emptyQuery(_TRANSACTION_FILTERS),
		transactionTotal: 0,
		instructions: [],
		feePackages: [],
		canReadInstructions,
		contributionSummary: null,
		transactionFilter: null as string | null,
		loadError: false,
		// A PENSION MORGAN LLOYD ADMINISTERS, which is what this comment always claimed and the
		// gate never checked: it tested the wrapper alone, so every SIPP in the book offered a
		// Drawdown button — including one custodied and administered somewhere Morgan Lloyd has
		// never heard of, where `cam_setmemberdetails` answers 409 and the drawer reads as an
		// outage. The journey is DCube's and DCube is Morgan Lloyd's.
		//
		// `is_pension` is the shelf's own answer (0136) with the wrapper set as the fallback, for
		// the reason the client record gives: the column is what a firm can see and correct, and
		// the nine hardcoded copies of that set do not agree with each other.
		canDrawdown: isMorganLloydPension(account) && canMoveMoney,
		// A pension is payable into wherever its scheme is administered — only the DRAWDOWN
		// journey belongs to the administrator. These were one flag, so narrowing drawdown to
		// Morgan Lloyd took the SIPP payment type off every pension it does not administer.
		canContribute: isPension(account) && canMoveMoney,
		// ANY PENSION, not only one the shelf already tags as Morgan Lloyd-administered. The sync
		// is what creates the scheme; waiting for `scheme_administrator` hid the button on the
		// SIPP whose product only names a custodian. Gated `contacts:sync_morgan_lloyd` rather
		// than `contacts:write`: minting a person and a scheme in a live CRM (no delete) is not
		// editing a client record, and a firm can hide the button from book editors who should
		// not. The endpoint asks for the same slug.
		canPushToMorganLloyd: isPension(account) && canSyncMorganLloyd,
		// Gates the "New transfer" entry point (/transfers/new).
		canMoveMoney,
		disabledActions,
		justificationRequired,
		canEditFees: false,
		canEditContact,
		sidebarFocus: accountSidebarFocus({
			uid: account.uid,
			name: title,
			// The wrapper and the owner are what tell two of a client's accounts apart; the uid does
			// not, and it is already on the record's header.
			subtitle: [account.product_type, owner].filter(Boolean).join(' · ') || null,
			// Up goes to the OWNER, because that is where an account is reached from — the accounts
			// list is a way in, not the place you came from. Falls back to the list when the account
			// has no client on it.
			back:
				account.client?.uid != null
					? { href: `/clients/${account.client.uid}`, label: owner ?? 'Client' }
					: { href: '/accounts', label: 'Accounts' }
		})
	};
};
