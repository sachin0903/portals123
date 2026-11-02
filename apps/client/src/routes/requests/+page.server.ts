import { loginPath } from '@graphene/auth';
import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson, publicId } from '$lib/api';
import { toFormField, type ApiRequestForm } from '$lib/forms';
import type { FormField } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

/** What a client is asked to do on one line of a request (the API's `RequestItemOut`). */
export type ApiRequestItem = {
	uid: string;
	action: 'upload' | 'agree' | 'acknowledge' | 'complete_form';
	title: string;
	category: string | null;
	format_hint: string | null;
	form_uid: string | null;
	target_document_uid: string | null;
	fulfilled_document_uid: string | null;
	status: 'pending' | 'fulfilled' | 'cancelled';
};

/** A request raised for this client (the API's `RequestOut`). `GET /documents/requests` is
 * RLS-scoped: staff see the firm's, a client sees only requests whose contact is in their scope. */
export type ApiRequest = {
	uid: string;
	contact_id: number;
	contact_name: string | null;
	priority: 'low' | 'normal' | 'high' | 'urgent';
	message: string | null;
	due_date: string | null;
	status: 'pending' | 'fulfilled' | 'cancelled';
	created_at: string;
	items: ApiRequestItem[];
};

/** An item plus (for a pending `complete_form`) the resolved builder form to fill in-app. */
export type RequestItem = ApiRequestItem & {
	formName?: string | null;
	formFields?: FormField[];
	formAnswers?: Record<string, unknown>;
};
export type ClientRequest = Omit<ApiRequest, 'items'> & { items: RequestItem[] };

export const load: PageServerLoad = async ({ locals, fetch, parent, depends, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	depends('app:requests');
	const headers = authHeaders(locals.accessToken);
	// Narrowed to the active client, like every other read on this portal — the composed pages'
	// own action-items slice already passed it, so this standalone page was the odd one out and
	// listed one client's outstanding requests while the nav said you were looking at another.
	const { activeContactId } = await parent();
	const cid = activeContactId !== null ? `?contact_id=${activeContactId}` : '';
	const reqR = await fetchJson<ApiRequest[]>(
		fetch,
		`${apiUrl()}/documents/requests${cid}`,
		headers
	);
	// A failed fetch surfaces as a retry banner rather than an empty page — a transient 401 at the
	// token-refresh boundary must not read as "nothing to do".
	const loadError = !reqR.ok;
	const requests: ClientRequest[] = (reqR.ok ? reqR.data : [])
		// Cancelled requests are no longer anyone's business; the rest are shown with their state.
		.filter((r) => r.status !== 'cancelled')
		.map((r) => ({ ...r, items: r.items.map((i) => ({ ...i })) }));

	// Pending complete_form items render a real builder form in-app; the endpoint is ownership-checked
	// server-side. Small N per client.
	await Promise.all(
		requests.flatMap((r) =>
			r.items
				.filter((i) => i.action === 'complete_form' && i.status === 'pending')
				.map(async (i) => {
					const res = await fetch(`${apiUrl()}/documents/requests/${r.uid}/items/${i.uid}/form`, {
						headers
					});
					if (res.ok) {
						const def: ApiRequestForm = await res.json();
						i.formName = def.name;
						i.formFields = def.fields.map(toFormField);
						i.formAnswers = def.answers ?? {};
					}
				})
		)
	);

	return { requests, loadError };
};

export const actions: Actions = {
	/** Fulfil an `agree`/`acknowledge` (confirm-only) or `upload` (multipart file) item. The API runs
	 * this elevated and checks the request belongs to the caller's contact scope. */
	fulfilItem: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const reqUid = publicId(form.get('req_uid'));
		const itemUid = publicId(form.get('item_uid'));
		if (!reqUid || !itemUid) return fail(400, { error: 'No item specified.' });
		const file = form.get('file');
		const body = new FormData();
		// A named ZERO-BYTE file is forwarded rather than filtered out. Dropping it here made the API
		// answer "This action requires a file upload" — which reads as "you chose nothing" to someone who
		// did choose something — instead of naming the real problem ("document must be 1..N bytes"). A
		// part with no filename is an unfilled file input, so that one is still omitted.
		if (file instanceof File && (file.size > 0 || file.name !== '')) {
			body.append('file', file, file.name);
		}
		const res = await fetch(`${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}/fulfill`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken),
			body
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Submit a completed form item: POST the collected answers to the per-item form-response
	 * endpoint, which stores them and marks the item done. */
	submitForm: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const reqUid = publicId(form.get('req_uid'));
		const itemUid = publicId(form.get('item_uid'));
		if (!reqUid || !itemUid) return fail(400, { error: 'No item specified.' });
		let answers: unknown;
		try {
			answers = JSON.parse(String(form.get('answers') ?? '{}'));
		} catch {
			return fail(400, {
				error: 'We couldn’t read your answers. Please check them and try again.'
			});
		}
		const res = await fetch(
			`${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}/form-response`,
			{
				method: 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ answers })
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Store answers and leave the item pending. */
	saveForm: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const reqUid = publicId(form.get('req_uid'));
		const itemUid = publicId(form.get('item_uid'));
		if (!reqUid || !itemUid) return fail(400, { error: 'No item specified.' });
		let answers: unknown;
		try {
			answers = JSON.parse(String(form.get('answers') ?? '{}'));
		} catch {
			return fail(400, {
				error: 'We couldn’t read your answers. Please check them and try again.'
			});
		}
		const res = await fetch(
			`${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}/form-response/save`,
			{
				method: 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ answers })
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { saved: true };
	},

	/** Resolve what an agree/acknowledge item asks the client to review, so nothing is confirmed
	 * unseen. A declaration authored as inline HTML carries no stored file (`/terms`); anything
	 * with a blob returns its name, content type and the SAME-ORIGIN URL its bytes are proxied on
	 * (never a presigned one — see the onboarding action, whose reasoning this mirrors). */
	targetDoc: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		const form = await request.formData();
		const reqUid = publicId(form.get('req_uid'));
		const itemUid = publicId(form.get('item_uid'));
		if (!reqUid || !itemUid) return fail(400, { error: 'No item specified.' });
		const base = `${apiUrl()}/documents/requests/${reqUid}/items/${itemUid}`;
		const termsRes = await fetch(`${base}/terms`, { headers: authHeaders(locals.accessToken) });
		const terms = termsRes.ok
			? ((await termsRes.json()) as { name: string | null; content: string | null })
			: null;
		if (terms?.content) {
			return { ok: true, name: terms.name, content: terms.content, url: null, content_type: null };
		}
		const res = await fetch(`${base}/target`, { headers: authHeaders(locals.accessToken) });
		if (!res.ok) {
			// A terms row with neither HTML nor a stored file is a bare acknowledgement; only "no
			// reviewable document at all" is a failure.
			if (terms) {
				return { ok: true, name: terms.name, content: null, url: null, content_type: null };
			}
			return fail(res.status, { error: await detail(res) });
		}
		const doc = (await res.json()) as { name: string | null; content_type: string | null };
		return {
			ok: true,
			...doc,
			url: `/api/request-items/${reqUid}/${itemUid}/content`,
			content: null
		};
	}
};
