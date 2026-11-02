import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { ApiDocument, ApiRequest } from '@graphene/ui/book/documents';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'documents:read')) {
		error(403, 'You do not have permission to view requests.');
	}
	const headers = authHeaders(locals.accessToken);
	// Cancelling / re-dating a request is documents:write; RE-POINTING an item at a different
	// document is documents:send (the six-splits re-key) — it changes what the client is being
	// asked to consent to, the same client-facing release act as raising the request.
	const canManage = can(locals.permissions, locals.role, 'documents:write');
	const canSend = can(locals.permissions, locals.role, 'documents:send');
	// The 403 is resolved here; the request is STREAMED so the record's shape is on screen on the
	// click. A missing request still 404s — from inside the promise, which Streamed reports as retry.
	return {
		canManage,
		canSend,
		record: loadRecord({ uid: params.uid, canManage, canSend, headers, fetch })
	};
};

async function loadRecord({
	uid,
	canManage,
	canSend,
	headers,
	fetch
}: {
	uid: string;
	canManage: boolean;
	canSend: boolean;
	headers: Record<string, string>;
	fetch: typeof globalThis.fetch;
}) {
	// There is no GET /documents/requests/{uid} — the list is RLS-scoped and small, so the detail
	// picks its request out of it. A failing fetch must not read as "request not found".
	const [requestsR, docsR] = await Promise.all([
		fetchJson<ApiRequest[]>(fetch, `${apiUrl()}/documents/requests`, headers),
		// The document list only feeds the reassign picker.
		canSend ? fetchJson<ApiDocument[]>(fetch, `${apiUrl()}/documents`, headers) : null
	]);
	if (!requestsR.ok) {
		return {
			request: null,
			documents: [] as ApiDocument[],
			canManage,
			canSend,
			loadError: true
		};
	}
	const request = requestsR.data.find((r) => r.uid === uid) ?? null;
	if (request === null) error(404, 'Request not found');
	return {
		request,
		documents: docsR?.ok ? docsR.data : [],
		canManage,
		canSend,
		loadError: docsR !== null && !docsR.ok
	};
}

export const actions: Actions = {
	/** Cancel this request and every still-pending item on it. */
	cancel: async ({ params, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to cancel requests.' });
		}
		const res = await fetch(`${apiUrl()}/documents/requests/${params.uid}/cancel`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Move (or clear) the deadline. Only pending requests are editable — the API 409s otherwise. */
	dueDate: async ({ params, request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to edit requests.' });
		}
		const dueDate = String((await request.formData()).get('due_date') ?? '').trim();
		const res = await fetch(`${apiUrl()}/documents/requests/${params.uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ due_date: dueDate || null })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Point an agree/acknowledge item at the correct document. A fulfilled item re-opens and the
	 * client's prior consent is kept on the trail as superseded — the API owns all of that.
	 *
	 * `documents:send` (the six-splits re-key): re-pointing changes what the client is asked to
	 * consent to — release, not drafting — and the API's reassign endpoint demands the same. */
	reassign: async ({ params, request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:send')) {
			return fail(403, { error: 'You do not have permission to amend requests.' });
		}
		const form = await request.formData();
		const itemUid = String(form.get('item_uid') ?? '').trim();
		const targetUid = String(form.get('target_document_uid') ?? '').trim();
		const reason = String(form.get('reason') ?? '').trim();
		if (!itemUid) return fail(400, { error: 'No item specified.' });
		if (!targetUid) return fail(400, { error: 'Choose the document to attach instead.' });
		const res = await fetch(
			`${apiUrl()}/documents/requests/${params.uid}/items/${itemUid}/reassign`,
			{
				method: 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ target_document_uid: targetUid, reason: reason || null })
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
