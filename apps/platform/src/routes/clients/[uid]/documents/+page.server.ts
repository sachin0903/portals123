import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { ActionEvent } from '@graphene/ui';
import type { ApiDocument, ApiFile, DocumentTypeOption } from '@graphene/ui/book/documents';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import type { Actions, PageServerLoad } from './$types';

/** A published form the client can be asked to complete (the request dialog's picker). */
type FormOption = { uid: string; name: string; status: string };

/**
 * This client's documents and supporting files — the record's "Documents" section.
 *
 * This route used to be a 307 back to `/clients/[id]?tab=documents`. The tab is a route again, and
 * the redirect now runs the other way (the record's load sends `?tab=documents` here), so the two
 * must not both exist — that would be a redirect loop.
 *
 * Everything is scoped to the crm.contact server-side, not sliced client-side out of the firm-wide
 * feed, so it can neither miss nor leak across clients.
 */
export const load: PageServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { detail: contactDetail, canReadDocuments, clientName } = await parent();
	// The sidebar hides this section without documents:read; someone arriving by URL is told why.
	if (!canReadDocuments) error(403, 'You do not have permission to view documents.');

	const headers = authHeaders(locals.accessToken);
	const contactId = contactDetail.contact.id;
	const canSend = can(locals.permissions, locals.role, 'documents:send');
	const canWrite = can(locals.permissions, locals.role, 'documents:write');
	const canReadForms = can(locals.permissions, locals.role, 'forms:read');
	const canReadActivity = can(locals.permissions, locals.role, 'activity:read');
	return {
		canSend,
		canWrite,
		library: loadLibrary({
			uid: params.uid,
			contactId,
			contactUid: contactDetail.contact.uid,
			clientName,
			clientEmail: contactDetail.contact.email,
			canReadForms,
			canReadActivity,
			headers,
			fetch
		})
	};
};

async function loadLibrary({
	uid,
	contactId,
	contactUid,
	clientName,
	clientEmail,
	canReadForms,
	canReadActivity,
	headers,
	fetch
}: {
	uid: string;
	contactId: number;
	contactUid: string;
	clientName: string;
	clientEmail: string | null;
	canReadForms: boolean;
	canReadActivity: boolean;
	headers: HeadersInit;
	fetch: typeof globalThis.fetch;
}) {
	// No archived slice: the tab has no Live/Archived switch any more (archiving reads as DELETING,
	// and a client's record is not where a firm browses its deletions — that is the ops-side "Deleted
	// documents" view, and Restore lives on the document's own record).
	const [docsRes, filesRes, actRes, typesRes, formsRes] = await Promise.all([
		fetch(`${apiUrl()}/documents?contact_id=${contactId}`, { headers }),
		fetch(`${apiUrl()}/files?contact_id=${contactId}`, { headers }),
		// This client's document activity draws from the action-event stream: their document + share
		// events, humanised by the shared EventFeed. Fetched ONLY with activity:read; else skipped.
		canReadActivity
			? fetch(
					// `?contact=` takes the client's UID; `?contact_id=` above takes the serial, and each
					// is named for what it takes. The serial was sent here, matched no uid, and the
					// stream's contact predicate then narrowed nothing — so this panel showed the FIRM's
					// document activity under one client's name.
					`${apiUrl()}/firm/audit/actions?contact=${uid}&document_family=true&limit=${PAGE_SIZE}`,
					{ headers }
				)
			: null,
		// The tag registry — so the client-tab upload dialog offers the firm's existing types.
		fetch(`${apiUrl()}/documents/types`, { headers }),
		canReadForms ? fetch(`${apiUrl()}/forms`, { headers }) : null
	]);
	const documents: ApiDocument[] = docsRes.ok ? await docsRes.json() : [];
	const files: ApiFile[] = filesRes.ok ? await filesRes.json() : [];
	const library: ApiDocument[] = [];
	const events: ActionEvent[] = actRes?.ok
		? (((await actRes.json()) as { items?: ActionEvent[] }).items ?? [])
		: [];
	const documentTypes: DocumentTypeOption[] = typesRes.ok ? await typesRes.json() : [];
	// Only published forms can be filled by a client.
	const forms: FormOption[] = formsRes?.ok
		? ((await formsRes.json()) as FormOption[]).filter((f) => f.status === 'published')
		: [];

	return {
		docsData: {
			client: { id: contactId, uid: contactUid, name: clientName },
			clientOption: { contact_id: contactId, name: clientName, email: clientEmail },
			documents,
			files,
			library,
			events,
			canReadActivity,
			documentTypes,
			forms
		},
		// An outage must not read as "this client has no documents".
		loadError: !docsRes.ok || !filesRes.ok
	};
}

export const actions: Actions = {
	/** Soft-archive one of this client's documents (documents:write — not the contacts:write the
	 *  record's own actions guard on). Posted to by `client-documents.svelte`'s row action, which
	 *  calls it DELETE — the API state stays `archived`, the label does not. */
	archive: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { action: 'archive', error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, {
				action: 'archive',
				error: 'You do not have permission to archive documents.'
			});
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { action: 'archive', error: 'No document specified.' });
		const res = await fetch(`${apiUrl()}/documents/${uid}/archive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'archive', error: await detail(res) });
		return { action: 'archive', ok: true };
	},

	/** Restore a deleted (`archived`) document to the live list (documents:write) — the inverse of
	 *  archive.
	 *
	 *  Nothing in this tab posts here any more: the Live/Archived switch and its per-row Restore are
	 *  gone, and the document RECORD's own Restore posts to `/documents?/unarchive` (one action, from
	 *  both of its hosts). Kept rather than deleted because it is the client-scoped door to the same
	 *  API call, guarded the same way — the surface that used it went, not the capability. */
	restore: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { action: 'restore', error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, {
				action: 'restore',
				error: 'You do not have permission to restore documents.'
			});
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { action: 'restore', error: 'No document specified.' });
		const res = await fetch(`${apiUrl()}/documents/${uid}/unarchive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'restore', error: await detail(res) });
		return { action: 'restore', ok: true };
	},

	/** File one or more existing firm documents under this client — sets each one's made-for
	 *  (intended_contact_id) via PATCH /documents/{uid}. documents:write; the API re-checks the client
	 *  is one the caller sees. Patched sequentially so the first failure is reported precisely. */
	assign: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { action: 'assign', error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, {
				action: 'assign',
				error: 'You do not have permission to file documents.'
			});
		}
		const form = await request.formData();
		const uids = form
			.getAll('uid')
			.map((u) => String(u))
			.filter(Boolean);
		const contactId = Number(form.get('contact_id'));
		if (uids.length === 0) return fail(400, { action: 'assign', error: 'No document specified.' });
		if (!Number.isFinite(contactId) || contactId <= 0) {
			return fail(400, { action: 'assign', error: 'No client specified.' });
		}
		for (const uid of uids) {
			const res = await fetch(`${apiUrl()}/documents/${uid}`, {
				method: 'PATCH',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ intended_contact_id: contactId })
			});
			if (!res.ok) return fail(res.status, { action: 'assign', error: await detail(res) });
		}
		return { action: 'assign', ok: true, count: uids.length };
	},

	/** Ask this client for one or more things (POST /documents/requests) — the in-place request
	 *  dialog, so raising a request never leaves the client's record. Mirrors the Requests page's
	 *  `create` action; the client is fixed to this record (the dialog posts its contact_id). */
	createRequest: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { action: 'createRequest', error: 'Not signed in.' });
		// documents:send (the six-splits re-key): a request is client-facing release — it lands in
		// the client's portal and email — and the API's create endpoint demands the same slug.
		if (!can(locals.permissions, locals.role, 'documents:send')) {
			return fail(403, {
				action: 'createRequest',
				error: 'You do not have permission to raise requests.'
			});
		}
		const form = await request.formData();
		const requestContactId = Number(form.get('contact_id'));
		if (!Number.isFinite(requestContactId) || requestContactId <= 0) {
			return fail(400, { action: 'createRequest', error: 'No client specified.' });
		}
		let items: unknown;
		try {
			items = JSON.parse(String(form.get('items') ?? '[]'));
		} catch {
			return fail(400, { action: 'createRequest', error: 'Malformed request items.' });
		}
		if (!Array.isArray(items) || items.length === 0) {
			return fail(400, {
				action: 'createRequest',
				error: 'Add at least one thing you are asking for.'
			});
		}
		const res = await fetch(`${apiUrl()}/documents/requests`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				contact_id: requestContactId,
				priority: String(form.get('priority') ?? 'normal'),
				message: String(form.get('message') ?? '').trim() || null,
				due_date: String(form.get('due_date') ?? '') || null,
				items
			})
		});
		if (!res.ok) return fail(res.status, { action: 'createRequest', error: await detail(res) });
		const created = (await res.json()) as { uid?: string };
		return { action: 'createRequest', ok: true, uid: created.uid };
	}
};
