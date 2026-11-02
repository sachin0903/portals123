import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail, jsonHeaders, fetchJson } from '$lib/api';
import type { ApiConversation, ApiGroup } from '@graphene/ui/book/messages';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'messages:read')) {
		error(403, 'You do not have permission to view messages.');
	}
	const q = url.searchParams.get('q');
	// Which thread to open. The header search lands here: `/chats/{uid}` is the messages proxy,
	// not a page, so a click on a message hit has to arrive as a query the inbox can select.
	const openConversation = url.searchParams.get('conversation');
	const openGroup = url.searchParams.get('group');
	// UNCAPPED, deliberately. This list is not a window onto the inbox — it IS the inbox, and the
	// page merges it with the whole contact book: a client whose thread fell outside the newest
	// fifty came back as a directory row reading "No messages yet", which is a false statement
	// about someone the firm has been talking to for a year, and selecting them wrote that blank
	// over the real thread. One row per contact with a thread is bounded by the book itself.
	const qs = new URLSearchParams();
	if (q) qs.set('q', q);
	const query = qs.toString() ? `?${qs}` : '';
	const canRequestDocuments = can(locals.permissions, locals.role, 'documents:send');
	const canWriteMessages = can(locals.permissions, locals.role, 'messages:write');
	// A NOTE IS NOT A MESSAGE. It is written on the client's record, so it takes `contacts:write` —
	// a reader who may reply on a thread does not necessarily hold it.
	const canWriteNotes = can(locals.permissions, locals.role, 'contacts:write');
	// Permissions are resolved here and the inbox is STREAMED: the page's shape is on screen on
	// the click. Both refusals stay out here — a 403 and a 303 have to be thrown before the
	// response starts, not from inside a promise.
	return {
		canRequestDocuments,
		canWriteMessages,
		canWriteNotes,
		openConversation,
		openGroup,
		inbox: loadInbox(locals.accessToken, fetch, query)
	};
};

async function loadInbox(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: string
) {
	const [conversationsRes, groupsRes] = await Promise.all([
		fetchJson<ApiConversation[]>(
			fetch,
			`${apiUrl()}/messaging/conversations${query}`,
			authHeaders(accessToken)
		),
		fetchJson<ApiGroup[]>(fetch, `${apiUrl()}/messaging/groups`, authHeaders(accessToken))
	]);
	if (!conversationsRes.ok)
		return {
			conversations: [] as ApiConversation[],
			groups: [] as ApiGroup[],
			loadError: true
		};
	return {
		conversations: conversationsRes.data,
		groups: groupsRes.ok ? groupsRes.data : ([] as ApiGroup[]),
		loadError: false
	};
}

export const actions: Actions = {
	/** Ask the conversation's client for something (POST /documents/requests) — the composer's
	 *  "request a document" button opens the same builder the client's Documents tab uses, locked
	 *  to that client. Mirrors clients/[id]/documents' `createRequest` action verbatim so
	 *  RequestDialog (which only knows the action name) works unmodified from either route. */
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
	},

	/** The group counterpart — raises the SAME request against every member
	 *  (POST /messaging/groups/{group_uid}/requests), one independent request per member. Reuses
	 *  RequestDialog with `forGroup` locking the target instead of `forClient`. */
	createGroupRequest: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { action: 'createGroupRequest', error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:send')) {
			return fail(403, {
				action: 'createGroupRequest',
				error: 'You do not have permission to raise requests.'
			});
		}
		const form = await request.formData();
		const groupUid = String(form.get('group_uid') ?? '').trim();
		if (!groupUid) {
			return fail(400, { action: 'createGroupRequest', error: 'No group specified.' });
		}
		let items: unknown;
		try {
			items = JSON.parse(String(form.get('items') ?? '[]'));
		} catch {
			return fail(400, { action: 'createGroupRequest', error: 'Malformed request items.' });
		}
		if (!Array.isArray(items) || items.length === 0) {
			return fail(400, {
				action: 'createGroupRequest',
				error: 'Add at least one thing you are asking for.'
			});
		}
		const res = await fetch(`${apiUrl()}/messaging/groups/${groupUid}/requests`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				priority: String(form.get('priority') ?? 'normal'),
				message: String(form.get('message') ?? '').trim() || null,
				due_date: String(form.get('due_date') ?? '') || null,
				items
			})
		});
		if (!res.ok)
			return fail(res.status, { action: 'createGroupRequest', error: await detail(res) });
		return { action: 'createGroupRequest', ok: true };
	}
};
