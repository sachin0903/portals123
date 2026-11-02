import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { type FeePackageOption, type InstructionRow, type Transaction } from '@graphene/ui';
import { type ReviewRow } from '@graphene/ui/book/reviews';
import { apiUrl, authHeaders, jsonHeaders, type AccountDetail } from '$lib/api';
import { asContributionSummary } from '$lib/components/accounts/contribution-summary';
import { mapAccountHoldings, type RawHolding } from '$lib/components/accounts/holdings';
import { isPension } from '$lib/components/accounts/pension';
import type { Actions, PageServerLoad } from './$types';

/** The overview's own window onto the ledger — the Transactions band loads the rest. */
const RECENT_MOVEMENTS = 10;

/**
 * The account overview: this account's holdings, fee catalogue, beneficiaries, contribution
 * summary and pending instructions.
 *
 * The contact itself comes from the layout (one read for every section); this load adds what the
 * landing view states and nothing more. The LEDGER still lives on its own route — a date filter on
 * Transactions does not re-read anything here — but the overview takes a ten-row window of it for
 * its own Recent transactions card, which is a different question from "show me the ledger".
 */
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { account, canReadInstructions } = await parent();
	const headers = authHeaders(locals.accessToken);
	const canReadContributions = can(locals.permissions, locals.role, 'contributions:read');
	return {
		band: loadBand({
			account,
			canReadInstructions,
			canReadContributions,
			headers,
			fetch
		})
	};
};

async function loadBand({
	account,
	canReadInstructions,
	canReadContributions,
	headers,
	fetch
}: {
	account: AccountDetail;
	canReadInstructions: boolean;
	canReadContributions: boolean;
	headers: HeadersInit;
	fetch: typeof globalThis.fetch;
}) {
	// Keep the original body — extracted so the overview STREAMS.
	const instructionParams = new URLSearchParams({ account_id: String(account.id) });
	const summaryParams = new URLSearchParams();
	if (account.client.id != null) summaryParams.set('contact_id', String(account.client.id));
	summaryParams.set('account_id', String(account.id));

	const [
		holdingsRes,
		allocationRes,
		recentRes,
		instructionsRes,
		summaryRes,
		beneficiariesRes,
		pendingRes
	] = await Promise.all([
		fetch(`${apiUrl()}/crm/holdings?account_id=${account.id}`, { headers }),
		// HOW THE MONEY IS ARRANGED, classified server-side and keyed by this account — the same
		// read the client record's overview makes for the whole portfolio. It has to be the
		// endpoint rather than a pass over the holdings above: the mapping those rows go through
		// drops the provider's `asset_type`, which is half of what `classifyHolding` reads.
		fetch(`${apiUrl()}/crm/holdings/summary?account_id=${account.id}`, { headers }),
		// THE LATEST MOVEMENTS, for the overview's own card. A WINDOW, not the ledger: the
		// Transactions band loads its own page with the filters, the facets and the total, and this
		// answers "what has happened to this account lately" in ten rows.
		fetch(`${apiUrl()}/crm/transactions?account_id=${account.id}&limit=${RECENT_MOVEMENTS}`, {
			headers
		}),
		canReadInstructions && account.client.uid != null
			? fetch(`${apiUrl()}/firm/contacts/${account.client.uid}/instructions?${instructionParams}`, {
					headers
				})
			: null,
		// `isPension(account)`, not the wrapper set. The gate below uses the shelf's answer
		// (0136) and these two fetches did not, so for an account the shelf calls a pension
		// whose `product_type` is outside the hardcoded set both were SKIPPED — leaving a live
		// Contribute button with no allowance or MPAA figures behind it, and `beneficiaries: []`
		// under a comment claiming `[]` is honest "whether the cause is an empty set or a
		// 403", when the cause was a fetch that never happened and the panel then told the
		// client the administrator decides at their own discretion. The `PRODUCT_ALIASES`
		// shape again: the rule applied where it was noticed, not at the siblings beside it.
		canReadContributions && account.client.id != null && isPension(account)
			? fetch(`${apiUrl()}/contributions/summary?${summaryParams}`, { headers })
			: null,
		// Beneficiary nominations, pension accounts only — an ISA or a GIA passes under the will,
		// so a nomination there would be a wish with no effect. A read the caller is not entitled
		// to is not made at all, which is this loader's standing rule.
		isPension(account)
			? fetch(`${apiUrl()}/crm/accounts/${account.uid}/beneficiaries`, { headers })
			: null,
		fetch(`${apiUrl()}/reviews/pending?subject_type=account&subject_id=${account.uid}`, {
			headers
		})
	]);

	const raw: RawHolding[] = holdingsRes.ok ? await holdingsRes.json() : [];
	const instructions: InstructionRow[] = instructionsRes?.ok
		? ((await instructionsRes.json()) as InstructionRow[])
				.filter((instruction) => instruction.account_id === account.id)
				.sort((a, b) => b.created_at.localeCompare(a.created_at))
		: [];

	// A FAILED SUMMARY IS NO DONUT, never a ring drawn from nothing: the card is absent when the
	// read fails, exactly as it is for an account nobody has valued yet.
	const allocations: { label: string; value: number }[] = allocationRes.ok
		? (((await allocationRes.json()) as { allocations?: { label: string; value: number }[] })
				.allocations ?? [])
		: [];

	// BOTH SHAPES READ, NOT ASSUMED. `/crm/transactions` answers a bare list and opts into
	// `{items, total}` with `paged=true` (crm.py) — which this read does not send, so a list is
	// what comes back. Reading either costs one line and is what the record's Messages tab did not
	// do, which is how a listing that changed shape turned into a 500.
	const recentPayload = recentRes.ok ? await recentRes.json() : [];
	const transactions: Transaction[] = Array.isArray(recentPayload)
		? recentPayload
		: ((recentPayload as { items?: Transaction[] }).items ?? []);

	return {
		holdings: mapAccountHoldings(raw, account),
		allocations,
		transactions,
		// `[]` on a failed read is honest here in a way it is not everywhere: the panel says "no
		// beneficiaries nominated" and names the consequence (the administrator decides at their own
		// discretion), which is the right prompt whether the cause is an empty set or a 403.
		beneficiaries: beneficiariesRes?.ok ? await beneficiariesRes.json() : [],
		instructions,
		// FEES ARE VIEW ONLY FOR NOW (feedback, Oct 2026). To re-enable editing, restore the
		// `/catalog/fee-packages` read and set `canEditFees: Boolean(feePackagesRes?.ok)`.
		feePackages: [] as FeePackageOption[],
		contributionSummary: summaryRes?.ok ? asContributionSummary(await summaryRes.json()) : null,
		canEditFees: false,
		// EVERY READ THE OVERVIEW DRAWS FROM. The banner beside it says "holdings or transactions
		// couldn't be loaded", and a failed ledger used to leave an account looking like one that
		// has never had a movement — the same silence for a failed allocation read.
		loadError: !holdingsRes.ok || !recentRes.ok || !allocationRes.ok,
		pendingReviews: pendingRes.ok ? ((await pendingRes.json()) as ReviewRow[]) : []
	};
}

/**
 * The account overview's own mutations.
 *
 * FORM ACTIONS, where this page's other writes are `+server.ts` endpoints driven by fetch. The two
 * are not arbitrary: the fetch endpoints back DIALOGS, which need a JSON answer to decide whether
 * to close and what to say. A beneficiary nomination is a plain form in a panel — so it takes the
 * house default (docs/api.md and the contributions workbench), which gets CSRF, progressive
 * enhancement and a server-side re-read of the loader for free. The re-read is the part that
 * matters here: the panel's running total has to move the moment a nomination lands.
 */
async function detail(res: Response, fallback: string): Promise<string> {
	try {
		const body = (await res.json()) as { detail?: unknown };
		return typeof body.detail === 'string' ? body.detail : fallback;
	} catch {
		return fallback;
	}
}

/** `contacts:write`, not the money family: a nomination is a fact about the member's wishes, not a
 *  rail money moves along. The API demands exactly this slug, so the two cannot drift. */
function guard(locals: App.Locals, uid: string): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'contacts:write')) redirect(303, `/accounts/${uid}`);
}

export const actions: Actions = {
	addBeneficiary: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid);
		const data = await request.formData();
		const str = (k: string) => String(data.get(k) ?? '').trim();
		const res = await fetch(`${apiUrl()}/crm/accounts/${params.uid}/beneficiaries`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				name: str('name'),
				proportion: Number(str('proportion')),
				title: str('title') || null,
				relationship: str('relationship') || null
			})
		});
		// The API's 409 names the headroom ("30% is still unallocated"), which is the whole point of
		// surfacing its message verbatim — "that is too much" without the number is a refusal an
		// adviser has to do arithmetic to act on.
		if (!res.ok)
			return fail(res.status, {
				action: 'addBeneficiary',
				error: await detail(res, 'Could not add that beneficiary.')
			});
		return { action: 'addBeneficiary', ok: true };
	},

	removeBeneficiary: async ({ request, locals, params, fetch }) => {
		guard(locals, params.uid);
		const uid = String((await request.formData()).get('uid') ?? '');
		const res = await fetch(`${apiUrl()}/crm/accounts/${params.uid}/beneficiaries/${uid}`, {
			method: 'DELETE',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok)
			return fail(res.status, {
				action: 'removeBeneficiary',
				error: await detail(res, 'Could not withdraw that nomination.')
			});
		return { action: 'removeBeneficiary', ok: true };
	}
};
