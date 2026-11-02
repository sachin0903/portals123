// The document record's loader and its two route-scoped write actions, shared by the two routes
// that render it: `/documents/[uid]` (the library's detail) and `/clients/[id]/documents/[doc]`
// (the same record reached from the client it belongs to, keeping the client in the URL).
//
// Parameterised by the document uid rather than reading a param off the event, because the two
// routes name it differently (`uid` vs `doc`) — which is the whole reason this is a module and
// not a copy.

import { error, fail, redirect } from '@sveltejs/kit';
import type { RequestEvent } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { ActionEvent } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail, fetchJson, jsonHeaders } from '$lib/api';
import { toRow, type ApiDocument } from '@graphene/ui/book/documents';

const EMPTY = {
	doc: null,
	previewUrl: null,
	previewContentType: null,
	events: [] as ActionEvent[],
	canManage: false,
	canSend: false,
	loadError: false
};

export type DocumentDetailData = typeof EMPTY | Awaited<ReturnType<typeof loadDocumentDetail>>;

/**
 * Load one document and its activity.
 *
 * `contactId` scopes the record to a client: pass it from the nested route and a document that is
 * neither made for nor delivered to that client 404s, so `/clients/5/documents/{uid}` cannot
 * render client 9's document under client 5's breadcrumb. It costs the client's two document
 * lists, ridden along in the same `Promise.all` so they add no wall-clock — `DocumentOut` exposes
 * the intended contact as a display NAME and no id, which is the cheaper check we do not have.
 */
export async function loadDocumentDetail(
	uid: string,
	{ locals, fetch }: Pick<RequestEvent, 'locals' | 'fetch'>,
	contactId?: number
) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'documents:read')) return EMPTY;

	const headers = authHeaders(locals.accessToken);
	// Drafting (rename/archive) is documents:write; sending to a client is documents:send
	// (the six-splits re-key) — the record offers each only to a caller who holds it.
	const canManage = can(locals.permissions, locals.role, 'documents:write');
	const canSend = can(locals.permissions, locals.role, 'documents:send');
	const res = await fetch(`${apiUrl()}/documents/${uid}`, { headers });
	if (res.status === 404) return EMPTY;
	if (!res.ok) error(res.status, 'Failed to load document');

	const api: ApiDocument = await res.json();
	// The side panels are secondary to the document itself, so a failure there degrades rather than
	// becoming an error page — it must never read as "no activity".
	const [activityR, liveR, archivedR] = await Promise.all([
		// This document's own trail now draws from the action-event stream (`?document=<uid>`), not
		// the retired documents.audit_log feed — humanised by the shared EventFeed.
		fetchJson<{ items: ActionEvent[] }>(
			fetch,
			`${apiUrl()}/firm/audit/actions?document=${uid}&limit=${PAGE_SIZE}`,
			headers
		),
		// Both slices, because an archived document reached from a client is still that client's —
		// checking only the live list would 404 it.
		contactId === undefined
			? null
			: fetchJson<ApiDocument[]>(fetch, `${apiUrl()}/documents?contact_id=${contactId}`, headers),
		contactId === undefined
			? null
			: fetchJson<ApiDocument[]>(
					fetch,
					`${apiUrl()}/documents?contact_id=${contactId}&status=archived`,
					headers
				)
	]);

	if (contactId !== undefined) {
		// A failed membership read is NOT a 404: that would turn a flaky list call into "this
		// document does not exist". Only a successful read of BOTH slices that does not contain it
		// refuses.
		if (liveR?.ok && archivedR?.ok) {
			const known = [...liveR.data, ...archivedR.data];
			if (!known.some((d) => d.uid === uid)) {
				error(404, 'That document is not filed under this client.');
			}
		}
	}

	// NOTHING is counted as a partial failure any more, so this is `false` rather than a live
	// expression. It read `!requestsR.ok`, and the Requests tab it fed is gone. Of what is left:
	// `activityR` is EXCLUDED on purpose — the events pane reads the action stream, gated by the
	// separate `activity:read` permission, so a documents:read user without it 403s that call, which
	// must degrade to an empty pane rather than a permanent banner over a loaded record; and the two
	// membership slices fail OPEN by design (see above), so flagging them would report a banner on a
	// record that is being shown correctly. The field stays because the shared component takes it and
	// the next secondary fetch added here should be counted.
	// (`clientsR` was also once in here — it fetched the client book for the send dialog's recipient
	// picker, which went with the send affordance, and a failure of a request nobody wanted put
	// "Some of this document's history couldn't be loaded" over a record that had loaded perfectly.)
	const loadError = false;
	const events = activityR.ok ? (activityR.data.items ?? []) : [];
	// Preview + download go through our same-origin content proxy (see content/+server.ts): the
	// object store is compose-internal, so a presigned URL wouldn't resolve in the browser. Null
	// until the document has a retrievable file (a queued generate has no content_type yet). It
	// stays on the flat path from both routes — it is a resource endpoint, not a page.
	const previewUrl = api.content_type ? `/documents/${uid}/content` : null;
	return {
		doc: toRow(api),
		previewUrl,
		previewContentType: api.content_type,
		events,
		canManage,
		canSend,
		loadError
	};
}

/** Rename a document (PATCH /documents/{uid}).
 *
 * NAME ONLY. This used to post `document_type` as well, and carried a `typeChangeBlocked` guard
 * because it was the second write path for a type — guarding only the library's `?/retype` would
 * have left this as a one-line bypass. Neither surface changes a type any more, so both the field
 * and the guard are gone. Renaming an archived document stays allowed, which is what that guard's
 * `nextType` no-op arm existed to preserve. */
export async function renameDocument(
	uid: string,
	{ request, locals, fetch }: Pick<RequestEvent, 'request' | 'locals' | 'fetch'>
) {
	if (!locals.user) return fail(401, { error: 'Not signed in.' });
	if (!can(locals.permissions, locals.role, 'documents:write')) {
		return fail(403, { error: 'You do not have permission to edit documents.' });
	}
	const form = await request.formData();
	const name = String(form.get('name') ?? '').trim();
	// The API COALESCEs nulls, so an empty name is a silent no-op that reports success — reject it
	// here instead of telling the user their blank name was saved.
	if (!name) return fail(400, { error: 'A document needs a name.' });
	const res = await fetch(`${apiUrl()}/documents/${uid}`, {
		method: 'PATCH',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({ name })
	});
	if (!res.ok) return fail(res.status, { error: await detail(res) });
	return { ok: true };
}

/** Re-queue a stuck render (POST /documents/{uid}/retry). The API re-enqueues a failed document,
 * or a queued one whose render job was lost; a document already waiting on a live job is a no-op.
 * Only generated documents in a failed/queued state qualify (the API enforces this). */
export async function retryDocument(
	uid: string,
	{ locals, fetch }: Pick<RequestEvent, 'locals' | 'fetch'>
) {
	if (!locals.user) return fail(401, { error: 'Not signed in.' });
	if (!can(locals.permissions, locals.role, 'documents:write')) {
		return fail(403, { error: 'You do not have permission to retry documents.' });
	}
	const res = await fetch(`${apiUrl()}/documents/${uid}/retry`, {
		method: 'POST',
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return fail(res.status, { error: await detail(res) });
	return { ok: true };
}
