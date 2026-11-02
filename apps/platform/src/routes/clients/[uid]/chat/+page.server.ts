import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { ApiConversation, ApiMessage } from '@graphene/ui/book/messages';
import type { ApiRequest } from '@graphene/ui/book/documents';
import type { RailAccount, RailClient, RailNote } from '@graphene/ui/book/messages';
import { railAccounts, railClient, railNotes, type RailDetail } from '$lib/rail';
import type { Actions, PageServerLoad } from './$types';

/**
 * This client's message thread — the record's "Messages" section.
 *
 * The SAME conversation the platform inbox's Direct Messages shows for this client
 * (messaging.conversations is keyed one-per-contact, never one-per-surface) — this is just
 * another place to read and reply to it, not a second thread.
 */
export const load: PageServerLoad = async ({ locals, fetch, params, parent, depends }) => {
	if (!locals.user) redirect(303, '/login');
	// A targeted refresh key. The page refreshes with `invalidateAll()` today; this is what a
	// later `invalidate('app:chat')` would hang off, because the thread's own fetches run after
	// this load has returned (it streams) and so are not tracked as dependencies by URL.
	depends('app:chat');
	const { detail: contactDetail, canReadMessages, clientName } = await parent();
	// The sidebar hides this section without messages:read; someone arriving by URL is told why.
	if (!canReadMessages) error(403, 'You do not have permission to view messages.');

	const canRequestDocuments = can(locals.permissions, locals.role, 'documents:send');
	const canWriteMessages = can(locals.permissions, locals.role, 'messages:write');
	// The rail's two FETCHED panels, each on its own permission. Skipped (not 403'd) for a caller
	// who lacks it, so the panel is absent rather than permanently empty — the same posture the
	// record's own sections take.
	const canReadRequests = can(locals.permissions, locals.role, 'documents:read');
	const canWriteNotes = can(locals.permissions, locals.role, 'contacts:write');
	// The shell (and the 403) are resolved here; the thread is STREAMED so the pane's shape is on
	// screen the moment the layout has named the client.
	return {
		canRequestDocuments,
		canWriteMessages,
		canWriteNotes,
		thread: loadThread({
			locals,
			fetch,
			contactUid: params.uid,
			contactId: contactDetail.contact.id,
			clientName,
			canRequestDocuments,
			canWriteMessages,
			canReadRequests,
			canWriteNotes
		})
	};
};

async function loadThread({
	locals,
	fetch,
	contactUid,
	contactId,
	clientName,
	canRequestDocuments,
	canWriteMessages,
	canReadRequests,
	canWriteNotes
}: {
	locals: App.Locals;
	fetch: typeof globalThis.fetch;
	contactUid: string;
	contactId: number;
	clientName: string;
	canRequestDocuments: boolean;
	canWriteMessages: boolean;
	canReadRequests: boolean;
	canWriteNotes: boolean;
}) {
	const headers = authHeaders(locals.accessToken);

	// The rail's fetched panels do not depend on the conversation, so they START NOW — before the
	// first await — rather than after the thread has been opened and read. That is faster, and it
	// is what keeps them inside this load's dependency tracking: SvelteKit only records a fetch
	// made before `load` returns, and the thread below streams. Neither counts towards
	// `loadError`: this page is the CONVERSATION, and a side panel that failed must not put a
	// "messages couldn't be loaded" banner over a thread that loaded perfectly.
	//
	// THE FAT CONTACT is read here too. The layout supplies only the SHELL, whose `notes` are
	// always empty and whose contact carries none of the reachability fields — so the rail beside
	// this thread had no notes, no accounts and no client block at all, while the same rail in the
	// inbox had all three. One read, mapped by the same `$lib/rail` the inbox's endpoint uses, so
	// the two cannot state different things about one client.
	const detailP = can(locals.permissions, locals.role, 'contacts:read')
		? // `contactUid` — the URL already names the client by uid, and the shell's own copy of it
			// is one more thing that can be absent.
			fetch(`${apiUrl()}/crm/contacts/${contactUid}`, { headers })
		: Promise.resolve(null);
	const requestsP = canReadRequests
		? // NO `limit`. `GET /documents/requests` answers TWO shapes and picks by that one
			// parameter: absent ⇒ the bare list (its own docstring names this rail as a caller
			// that reads it), present ⇒ `{items, total, counts}`. Sending one handed the rail an
			// object, and `requests.filter(...)` threw — a 500 on the record's Messages tab while
			// the inbox, which sends no limit, was fine.
			fetch(`${apiUrl()}/documents/requests?contact_id=${contactId}`, { headers })
		: Promise.resolve(null);
	// A rail read that rejects outright (the API unreachable) must not become an unhandled
	// rejection while the thread is still being opened: settle it to "no panel".
	const rail = Promise.all([detailP.catch(() => null), requestsP.catch(() => null)]);

	const empty = {
		conversation: null as ApiConversation | null,
		messages: [] as ApiMessage[],
		clientName,
		canRequestDocuments,
		canWriteMessages,
		requests: [] as ApiRequest[],
		tasks: [],
		client: null as RailClient | null,
		accounts: [] as RailAccount[],
		notes: [] as RailNote[],
		canWriteNotes
	};

	// Open (or lazily create) the channel — messaging.get_or_create_conversation_for_contact,
	// the same call the inbox's own "new conversation" flow makes. Creating needs
	// messages:write; a read-only visitor lists THIS client's existing thread
	// (`?contact_id=`) instead of minting one, and never walks the firm's whole inbox.
	const conversationsForContact = `${apiUrl()}/messaging/conversations?contact_id=${contactId}`;
	/** This client's threads — `null` when the READ ITSELF failed, which is a different answer
	 *  from "they have none" and shows the error banner rather than the empty state.
	 *
	 *  A body that is not a list counts as a failed read, never a thrown `.find` of `undefined`:
	 *  one unexpected answer used to take the whole page to a 500, on a surface whose rule is that
	 *  a part that failed states so over a thread that loaded. */
	// READS MADE AFTER THE FIRST AWAIT use the global fetch, not the event's. This function
	// streams, so by the time these run `load` has returned and SvelteKit can no longer record
	// them as dependencies — it says so, once per call, in the dev log. The record is what
	// `invalidate(url)` would key off, and this page refreshes by `invalidateAll()` or the
	// `app:chat` key instead; the auth header travels with the call either way.
	const later = globalThis.fetch;
	const listConversations = async (): Promise<ApiConversation[] | null> => {
		const listed = await later(conversationsForContact, { headers });
		if (!listed.ok) return null;
		const body = await listed.json().catch(() => null);
		return Array.isArray(body) ? (body as ApiConversation[]) : null;
	};
	let conversation: ApiConversation | null = null;
	if (canWriteMessages) {
		const openRes = await fetch(`${apiUrl()}/messaging/conversations`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ contact_id: contactId })
		});
		if (openRes.ok) {
			conversation = await openRes.json();
		} else {
			const found = (await listConversations())?.find((row) => row.contact_id === contactId);
			if (found) conversation = found;
			else return { ...empty, loadError: true };
		}
	} else {
		const listed = await listConversations();
		if (!listed) return { ...empty, loadError: true };
		conversation = listed.find((row) => row.contact_id === contactId) ?? null;
		if (!conversation) return { ...empty, loadError: false };
	}
	if (!conversation) return { ...empty, loadError: true };
	// `uid`, not `id`: the endpoint is `/messaging/conversations/{conversation_uid}/messages`
	// (0112_messaging_uids; docs/api.md → "Addressing"). The open call above returns BOTH keys, so
	// passing the serial got a well-formed 404 rather than a type error — the thread simply rendered
	// its load-error banner, on a page whose every other call already used the uid.
	const messagesRes = await later(
		`${apiUrl()}/messaging/conversations/${conversation.uid}/messages`,
		{ headers: authHeaders(locals.accessToken) }
	);
	const messages: ApiMessage[] = messagesRes.ok ? await messagesRes.json() : [];

	// The rail reads were started with the thread; they have usually landed by now.
	const [detailRes, requestsRes] = await rail;
	const railDetail: RailDetail | null = detailRes?.ok ? await detailRes.json() : null;

	return {
		conversation,
		messages,
		clientName,
		canRequestDocuments,
		// The rail's People panel takes the advisers straight off `data.detail`, which the record's
		// own layout already merges into this page's data — so they need no fetch and cannot
		// disagree with the record this thread sits inside.
		requests: requestsRes?.ok ? ((await requestsRes.json()) as ApiRequest[]) : [],
		tasks: [],
		client: railClient(railDetail),
		accounts: railAccounts(railDetail),
		notes: railNotes(railDetail),
		canWriteNotes,
		canWriteMessages,
		loadError: !messagesRes.ok
	};
}

export const actions: Actions = {
	/** Ask this client for something from their own chat thread — verbatim copy of the
	 *  Documents section's `createRequest` (and the platform inbox's), so RequestDialog works
	 *  unmodified from any of the three. */
	createRequest: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { action: 'createRequest', error: 'Not signed in.' });
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
