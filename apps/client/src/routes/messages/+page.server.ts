import { loginPath } from '@graphene/auth';
import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { ApiConversation, ApiMessage } from '$lib/messaging';
import type { PageServerLoad } from './$types';
import { adviserOrFirm, type Adviser } from '$lib/adviser';

export const load: PageServerLoad = async ({ locals, fetch, parent, depends, url }) => {
	if (!locals.user) redirect(303, loginPath(url));
	// The page re-reads the thread after every push and every send with `invalidate('app:messages')`
	// — this loader only, not the root layout's five calls. It used to call `invalidateAll()`, and
	// from the stream's own `open` handler, so each reload replaced `data`, restarted the effect that
	// owns the EventSource, and the fresh connection's `open` reloaded again: ~9 full reloads a
	// second per open tab, measured.
	depends('app:messages');
	const headers = authHeaders(locals.accessToken);
	const { activeContactId } = await parent();
	const cid = activeContactId !== null ? `?contact_id=${activeContactId}` : '';

	const [adviserRes, threadRes] = await Promise.all([
		fetchJson<{ adviser: Adviser | null }>(fetch, `${apiUrl()}/crm/home${cid}`, headers),
		fetchJson<{ conversation: ApiConversation; messages: ApiMessage[] }>(
			fetch,
			`${apiUrl()}/messaging/thread${cid}`,
			headers
		)
	]);

	if (!adviserRes.ok) return { adviser: null, conversation: null, messages: [], loadError: true };

	// Nobody assigned ⇒ the thread is with the FIRM ($lib/adviser::firmContact): its name and icon
	// head the conversation, and whoever on the staff replies is named on their own messages.
	const adviser = adviserOrFirm(adviserRes.data.adviser, locals.branding, locals.appName);

	// Opening the thread no longer depends on an assigned adviser — a 404 here is a real
	// failure (no linked contact, or the API is down), not "wait until someone is assigned".
	if (!threadRes.ok) {
		return {
			adviser,
			conversation: null,
			messages: [],
			loadError: true
		};
	}

	return {
		adviser,
		conversation: threadRes.data.conversation,
		messages: threadRes.data.messages,
		loadError: false,
		// The conversation is the screen on a phone: its own header names who it is with and
		// carries the way back, so the shell's top bar stands down below `lg` (app.d.ts).
		immersive: true
	};
};
