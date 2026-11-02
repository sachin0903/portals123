import { loginPath } from '@graphene/auth';
import { error, fail, redirect } from '@sveltejs/kit';
import { selfServeAccounts } from '$lib/account-facts';
import { adviserOrFirm } from '$lib/adviser';
import {
	apiUrl,
	authHeaders,
	detail,
	fetchJson,
	jsonHeaders,
	publicId,
	type Account
} from '$lib/api';
import {
	KIND_BY_ROUTE,
	parseAmount,
	type MoneyPreview,
	type MoneyRaised,
	type PendingRequest
} from '$lib/money-movement';
import type { PortalContext } from '$lib/page-data';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, fetch, parent, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	const kind = KIND_BY_ROUTE[params.kind];
	if (!kind) error(404, 'Not found');
	// A book with money_movement may move money on any of its accounts. Without it the page
	// still exists for the execution-only accounts in a mixed book — an advised account stays
	// off the list, and a book with none of them 404s rather than offering a form it cannot use.
	const moneyMovement = locals.classification?.capabilities?.money_movement === true;

	// The client's REAL accounts (GET /crm/accounts is RLS-scoped to their own). Narrowed to the
	// active client: `contact_scope()` is EVERY client this login acts for, so an un-narrowed
	// picker offered another person's accounts under the heading of the one on screen.
	const { activeContactId } = await parent();
	const cid = activeContactId !== null ? `?contact_id=${activeContactId}` : '';
	const headers = authHeaders(locals.accessToken);
	// Who the page tells the client to contact: their adviser, or the FIRM when nobody is
	// assigned ($lib/adviser) — a direct client told to "contact your adviser" has none to
	// contact. Best-effort: a failed context read falls back to the firm, not to a 404.
	const [res, ctx, pendingRes, actionsRes] = await Promise.all([
		fetchJson<Account[]>(fetch, `${apiUrl()}/crm/accounts${cid}`, headers),
		fetchJson<PortalContext>(fetch, `${apiUrl()}/crm/context${cid}`, headers),
		fetchJson<PendingRequest[]>(fetch, `${apiUrl()}/crm/money/instructions${cid}`, headers),
		fetchJson<{ disabled?: string[] }>(fetch, `${apiUrl()}/crm/money/actions`, headers)
	]);
	const disabled =
		actionsRes.ok && Array.isArray(actionsRes.data.disabled) ? actionsRes.data.disabled : [];
	if (kind === 'deposit' && disabled.includes('transaction.payment')) error(404, 'Not found');
	if (kind === 'withdrawal' && disabled.includes('transaction.withdrawal')) error(404, 'Not found');
	const accounts = res.ok ? selfServeAccounts(res.data, moneyMovement) : [];
	if (res.ok && accounts.length === 0 && !moneyMovement) error(404, 'Not found');
	const contact = adviserOrFirm(ctx.ok ? ctx.data.adviser : null, locals.branding, locals.appName);
	// `?account=` preselects the picker (the account page's own Payment/Withdraw buttons); an
	// unknown or ineligible uid is ignored rather than refused — the picker still offers the rest.
	const wanted = publicId(url.searchParams.get('account'));
	const preselected =
		accounts.find((a) => a.uid === wanted)?.uid ?? (accounts.length === 1 ? accounts[0].uid : null);
	return {
		kind,
		/** The segment in the URL, for links back to this page (`deposit` ≠ the API's word). */
		routeKind: params.kind,
		accounts,
		contact,
		preselected,
		pending: pendingRes.ok ? pendingRes.data : [],
		loadError: !res.ok
	};
};

type MoneyBody = { kind: 'deposit' | 'withdrawal'; account_uid: string; amount: number };

/** What every action needs from the posted form, or the words for the 400. A plain result, never
 *  `fail()`: returning `fail()` from a helper put `undefined` in the action's return type and the
 *  page's `form` collapsed to `{}`. */
function readRequest(
	form: FormData,
	routeKind: string
): { ok: true; body: MoneyBody } | { ok: false; status: number; error: string } {
	const kind = KIND_BY_ROUTE[routeKind];
	const account_uid = publicId(form.get('account'));
	const amount = parseAmount(form.get('amount'));
	if (!kind) return { ok: false, status: 404, error: 'Not found.' };
	if (!account_uid) return { ok: false, status: 400, error: 'Choose an account.' };
	if (amount === null)
		return { ok: false, status: 400, error: 'Enter an amount greater than zero.' };
	return { ok: true, body: { kind, account_uid, amount } };
}

/**
 * Three actions, one per step. The STEP lives in the action's result rather than in client
 * state, so a reload or a browser without JS lands on the same screen: `preview` answers with
 * the review step, `confirm` with the done step, and a failure returns to the amount step with
 * the API's own words. There is still no client-side money logic: the review shows what the
 * API's preview said, and the raise is re-decided server-side from the account's position.
 */
/** Ask the API what this request would do without recording anything. */
async function fetchPreview(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	body: MoneyBody
): Promise<
	| { ok: true; preview: MoneyPreview }
	| { ok: false; status: number; error: string; body: MoneyBody }
> {
	const res = await fetch(`${apiUrl()}/crm/money/preview`, {
		method: 'POST',
		headers: jsonHeaders(accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return { ok: false, status: res.status, error: await detail(res), body };
	const preview = (await res.json()) as MoneyPreview;
	// A refusal is the API's answer to THIS amount, said on the amount step with the figure
	// that is available — not a review of something that cannot happen.
	if (preview.refusal) return { ok: false, status: 400, error: preview.refusal, body };
	return { ok: true, preview };
}

// Not annotated as `Actions`: that annotation widens every return to `Record<string, any> | void`,
// and the page's `form` type collapses to `{}` — none of `step` / `preview` / `error` typecheck.
// The inferred returns are what `ActionData` is built from.
export const actions = {
	/** Amount → Review. */
	preview: async ({ request, locals, params, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const read = readRequest(await request.formData(), params.kind);
		if (!read.ok) return fail(read.status, { error: read.error });
		const got = await fetchPreview(fetch, locals.accessToken, read.body);
		if (!got.ok) return fail(got.status, { error: got.error, ...got.body });
		return { step: 'review' as const, preview: got.preview, ...read.body };
	},

	/** Review → Done. Raises the instruction; the API decides again from the account's position. */
	confirm: async ({ request, locals, params, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const read = readRequest(form, params.kind);
		if (!read.ok) return fail(read.status, { error: read.error });
		// The declaration is the client's, not a checkbox the browser ticked: "this is your
		// decision, not advice" has to have been read, so an unticked box returns to the review
		// (re-previewed — the step is only ever rendered from a preview the API just gave).
		if (form.get('understood') !== 'on') {
			const got = await fetchPreview(fetch, locals.accessToken, read.body);
			if (!got.ok) return fail(got.status, { error: got.error, ...got.body });
			return fail(400, {
				error: 'Please confirm you’ve read this and want to go ahead.',
				step: 'review' as const,
				preview: got.preview,
				...read.body
			});
		}
		const res = await fetch(`${apiUrl()}/crm/money/instructions`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(read.body)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res), ...read.body });
		const raised = (await res.json()) as MoneyRaised;
		return { step: 'done' as const, raised };
	},

	/** Cancel one of the client's own pending requests. */
	cancel: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const uid = publicId(form.get('uid'));
		if (!uid) return fail(400, { error: 'No request specified.' });
		const res = await fetch(`${apiUrl()}/crm/money/instructions/${uid}/cancel`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { cancelled: uid };
	}
};
