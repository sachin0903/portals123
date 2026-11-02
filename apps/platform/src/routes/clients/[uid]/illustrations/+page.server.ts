import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { Actions, PageServerLoad } from './$types';

/** The subset of an illustration document the listing renders (mirrors `IllustrationDoc` in
 *  `$lib/components/illustrations/illustrations.svelte`; declared here so a server module doesn't
 *  have to import a type out of a component). */
type IllustrationDoc = {
	uid: string;
	document_uid?: string | null;
	name: string | null;
	created_at: string | null;
	product_type?: string | null;
	status?: string | null;
	shared_with_client?: boolean;
	inputs?: {
		product_type?: string | null;
		quotation_type?: string | null;
		existing_fund_value?: number | null;
		account_id?: number | null;
		output_reference?: string | null;
		placeholder?: boolean | null;
		placeholder_reason?: string | null;
	} | null;
};

/** The firm's models, as the illustration form's picker needs them: the target return is the
 *  growth rate CTC projects at, so a model without one cannot be illustrated against. */
type Model = {
	id: number;
	name: string;
	target_return: number | string | null;
};

/**
 * Pension illustrations produced for this client, newest first.
 *
 * A dedicated read rather than a filter over /documents: the listing's columns come from `inputs`
 * (the assumptions each PDF was built on), which the general documents projection doesn't expose.
 *
 * This route also serves `POST` from `+server.ts` (the generate call the form makes). SvelteKit
 * negotiates by `accept`: a browser navigation asks for text/html and gets this page, the form's
 * `fetch` does not and gets the endpoint. Both belong to the same URL on purpose — do not "fix" the
 * duplication by moving one of them.
 */
export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	// The sidebar hides this section without documents:read OR illustrations:write (the listing
	// shows filed client paperwork; the section hosts the generate form); someone arriving by URL
	// is told why. Generating itself stays on illustrations:write alone — see `canIllustrate`.
	const { detail: record, canReadDocuments, canIllustrate } = await parent();
	if (!canReadDocuments && !canIllustrate) {
		error(403, 'You do not have permission to view illustrations.');
	}

	const headers = authHeaders(locals.accessToken);
	// The 403 is resolved here; the listing is STREAMED so the section's shape is on screen the
	// moment the layout has named the client.
	return {
		canShare: can(locals.permissions, locals.role, 'documents:send'),
		library: loadLibrary(record.contact.uid, headers, fetch)
	};
};

export const actions: Actions = {
	/** Put one illustration in the client's portal. `documents:send` — the PDF leaves the firm.
	 *  The caller must send `x-sveltekit-action`. Without it this POST is the generate endpoint
	 *  in `+server.ts`, and share never runs. */
	share: async ({ request, locals, fetch, params }) => {
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'documents:send')) {
			return fail(403, { error: 'You do not have permission to share illustrations.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No illustration specified.' });
		const res = await fetch(`${apiUrl()}/crm/contacts/${params.uid}/illustrations/${uid}/share`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};

async function loadLibrary(
	contactUid: string,
	headers: Record<string, string>,
	fetch: typeof globalThis.fetch
) {
	// Both in one round trip: the form cannot be used without the models, so fetching them lazily
	// would only move the wait to the moment the adviser opens it.
	const [res, modelsRes] = await Promise.all([
		fetch(`${apiUrl()}/crm/contacts/${contactUid}/illustrations`, { headers }),
		fetch(`${apiUrl()}/crm/models`, { headers })
	]);
	const illustrations: IllustrationDoc[] = res.ok ? await res.json() : [];
	// An empty list is a real state — a firm with no models configured cannot illustrate, and the
	// picker says so rather than the page failing.
	const models: Model[] = modelsRes.ok ? await modelsRes.json() : [];
	return { illustrations, models, loadError: !res.ok };
}
