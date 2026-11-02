import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail, fetchJson, jsonHeaders } from '$lib/api';
import type { ApiRequest, RequestAction } from '@graphene/ui/book/documents';
import type { Actions, PageServerLoad } from './$types';

/** One line of the request being created, as the dialog posts it. */
type ItemPayload = {
	action: RequestAction;
	title: string;
	category?: string | null;
	format_hint?: string | null;
	form_uid?: string | null;
	target_document_uid?: string | null;
};

/** One tab's window. The page is three tabs over one queue, so this is what a tab holds before
 *  you scroll — not what exists, which is what `counts` says. */
const PAGE_SIZE = 100;

/** The statuses the three tabs map to, in tab order. */
const TABS = ['pending', 'fulfilled', 'cancelled'] as const;
type Tab = (typeof TABS)[number];

/** The envelope `GET /documents/requests` answers with once `limit` is passed. */
type RequestPage = {
	items: ApiRequest[];
	total: number;
	counts: Record<Tab, number>;
	/** Outstanding AND past its date, across the whole queue — the page's headline number, which
	 *  cannot be derived from a window without describing only the window. */
	overdue: number;
};

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	// The queue is the whole book, the same firm-wide reach as the library itself: documents:show on
	// top of documents:read (routes/documents/+page.server.ts refuses it too), so a role without the
	// firm-wide page cannot reach it by URL either.
	if (!can(locals.permissions, locals.role, 'documents:show')) {
		error(403, 'You do not have permission to view document requests.');
	}
	const rawTab = url.searchParams.get('tab');
	const tab: Tab = TABS.includes(rawTab as Tab) ? (rawTab as Tab) : 'pending';
	const query = url.searchParams.get('q')?.trim() ?? '';
	if (!can(locals.permissions, locals.role, 'documents:read')) {
		return {
			tab,
			query,
			canManage: false,
			canSend: false,
			library: {
				requests: [] as ApiRequest[],
				counts: { pending: 0, fulfilled: 0, cancelled: 0 } as Record<Tab, number>,
				total: 0,
				overdue: 0,
				loadError: false
			}
		};
	}
	const headers = authHeaders(locals.accessToken);
	// Managing an existing request (cancel, move its deadline) is documents:write; RAISING one is
	// documents:send (the six-splits re-key) — a request lands in the client's portal and email,
	// the same client-facing release act as sending a document.
	const canManage = can(locals.permissions, locals.role, 'documents:write');
	const canSend = can(locals.permissions, locals.role, 'documents:send');
	return {
		tab,
		query,
		canManage,
		canSend,
		library: loadLibrary(headers, fetch, tab, query)
	};
};

async function loadLibrary(
	headers: Record<string, string>,
	fetch: typeof globalThis.fetch,
	tab: Tab,
	query: string
) {
	// First paint is the paged queue. The create dialog's pickers (clients / documents / forms)
	// load when it opens (`/documents/requests/pickers`).
	const search = new URLSearchParams({ status: tab, limit: String(PAGE_SIZE), offset: '0' });
	if (query) search.set('q', query);

	const requestsR = await fetchJson<RequestPage>(
		fetch,
		`${apiUrl()}/documents/requests?${search}`,
		headers
	);
	return {
		requests: requestsR.ok ? requestsR.data.items : [],
		// Every tab's size, counted across the whole queue rather than over the rows in hand. The
		// tabs are labelled with these — "Outstanding (12)" — and counting the loaded array would
		// make each one report the size of its own window instead of the size of the book.
		counts: requestsR.ok
			? requestsR.data.counts
			: ({ pending: 0, fulfilled: 0, cancelled: 0 } as Record<Tab, number>),
		total: requestsR.ok ? requestsR.data.total : 0,
		overdue: requestsR.ok ? requestsR.data.overdue : 0,
		loadError: !requestsR.ok
	};
}

export const actions: Actions = {
	/** Ask a client for one or more things (POST /documents/requests).
	 *
	 * `documents:send` (the six-splits re-key): the request lands in the client's portal and
	 * email — client-facing release, and the API's create endpoint demands the same slug. */
	create: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:send')) {
			return fail(403, { error: 'You do not have permission to raise requests.' });
		}
		const form = await request.formData();
		// The client always comes from the FORM: the builder's own picker posts `contact_id`, and a
		// dialog locked to one client (the client record's Documents tab) posts it in a hidden input.
		// There was a second source — `?client=<uid>` on the URL, from a document record's "Raise a
		// request" — but that button is gone with the record's Requests tab, and it never worked: the
		// page's own preselect parsed the uid with `Number()`, so the dialog was never actually
		// locked and the picker was always shown. The API re-checks that this login may even see the
		// client, so a forged `contact_id` earns a refusal rather than a request against somebody
		// else's client.
		const posted = form.get('contact_id');
		const contactId = posted ? Number(posted) : NaN;
		if (!Number.isFinite(contactId) || contactId <= 0) {
			return fail(400, { error: 'Choose the client you are asking.' });
		}
		let items: ItemPayload[];
		try {
			items = JSON.parse(String(form.get('items') ?? '[]')) as ItemPayload[];
		} catch {
			return fail(400, { error: 'Malformed request items.' });
		}
		if (!Array.isArray(items) || items.length === 0) {
			return fail(400, { error: 'Add at least one thing you are asking for.' });
		}
		// Mirror the API's own guard so a mistake is caught next to the field rather than as a 422.
		if (items.some((i) => !i.title?.trim())) {
			return fail(400, { error: 'Every item needs a title.' });
		}
		if (
			items.some(
				(i) => (i.action === 'agree' || i.action === 'acknowledge') && !i.target_document_uid
			)
		) {
			return fail(400, { error: 'Agree and acknowledge items need a document to act on.' });
		}
		if (items.some((i) => i.action === 'complete_form' && !i.form_uid)) {
			return fail(400, { error: 'Choose the form the client should complete.' });
		}
		const res = await fetch(`${apiUrl()}/documents/requests`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				contact_id: contactId,
				priority: String(form.get('priority') ?? 'normal'),
				message: String(form.get('message') ?? '').trim() || null,
				due_date: String(form.get('due_date') ?? '') || null,
				items
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created = (await res.json()) as ApiRequest;
		return { created: true, uid: created.uid };
	},

	/** Cancel a pending request and its still-pending items. */
	cancel: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to cancel requests.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No request specified.' });
		const res = await fetch(`${apiUrl()}/documents/requests/${uid}/cancel`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Move a pending request's deadline (PATCH; an empty value clears it). */
	dueDate: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to edit requests.' });
		}
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No request specified.' });
		const dueDate = String(form.get('due_date') ?? '').trim();
		const res = await fetch(`${apiUrl()}/documents/requests/${uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ due_date: dueDate || null })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
