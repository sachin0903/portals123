import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { SESSION_COOKIE } from '$lib/server/workos';
import { can } from '@graphene/api-client/permissions';
import type { NotificationItemData } from '@graphene/ui';
import type { LayoutServerLoad } from './$types';

/** Which portal THIS app is. A session that resolves to a different portal is refused (below). */
const THIS_PORTAL = 'platform';

/** A confirmed portal from GET /me. `undefined` is not an answer — fail open the same way a
 * transport failure on the old `/me/portal` call did. `null` is a confirmed "no membership". */
function refusePortal(portal: string | null | undefined, expected: string): boolean {
	return portal !== undefined && portal !== expected;
}

/** The bell's initial state: recent notifications + the unread count. Best-effort — the shell must
 * render even if the notifications API is briefly unavailable, so a failure yields an empty bell,
 * never a broken layout. The badge then self-heals on its next poll. */
async function loadNotifications(
	accessToken: string | null | undefined,
	fetchFn: typeof fetch
): Promise<{ items: NotificationItemData[]; unread: number }> {
	if (!accessToken) return { items: [], unread: 0 };
	try {
		const feed = await fetchFn(`${apiUrl()}/notifications?limit=8`, {
			headers: authHeaders(accessToken)
		});
		if (!feed.ok) return { items: [], unread: 0 };
		const body = (await feed.json()) as { items?: NotificationItemData[]; unread_count?: number };
		return {
			items: body.items ?? [],
			unread: typeof body.unread_count === 'number' ? body.unread_count : 0
		};
	} catch {
		return { items: [], unread: 0 };
	}
}

/** The Messages nav dot's initial state — same best-effort discipline as the bell: a failure or a
 * caller without `messages:read` (the section wouldn't even show in the nav) both read as "no
 * dot" rather than a broken layout. */
async function loadChatsUnread(
	accessToken: string | null | undefined,
	canReadMessages: boolean,
	fetchFn: typeof fetch
): Promise<boolean> {
	if (!accessToken || !canReadMessages) return false;
	try {
		const res = await fetchFn(`${apiUrl()}/messaging/unread`, {
			headers: authHeaders(accessToken)
		});
		return res.ok ? Boolean((await res.json()).unread) : false;
	} catch {
		return false;
	}
}

/** Surface the signed-in user + their permissions to the layout so the app shell can
 * render the user and gate permission-scoped nav links.
 *
 * WHAT IS AWAITED HERE IS ON THE CRITICAL PATH TO THE FIRST BYTE of every page: the hook has
 * already paid for the session and `GET /me` before this runs, and nothing can be sent until this
 * returns. So only the decision that has to be made BEFORE the response starts is awaited — the
 * wrong-portal refusal, because it is a redirect and a redirect cannot be thrown once HTML is
 * streaming. The login terms check used to be awaited here too (`GET /terms/outstanding`); it was
 * removed on 13 Oct 2026 — terms are collected inside a journey, and that call was the one still
 * in front of the first byte. The bell and the Messages dot are STREAMED: returned as promises
 * (SvelteKit 2 does not await top-level promises) so the shell renders at once and they fill in
 * behind it, the same way `clients/+page.server.ts` streams `book`. They are both best-effort and
 * can never reject (see each loader), so a streamed failure is an empty bell, not a broken layout. */
export const load: LayoutServerLoad = async ({ locals, fetch, cookies }) => {
	if (locals.user && locals.accessToken && refusePortal(locals.portal, THIS_PORTAL)) {
		cookies.delete(SESSION_COOKIE, { path: '/' });
		redirect(303, '/login?wrong_portal=1');
	}

	const canReadMessages = can(locals.permissions, locals.role, 'messages:read');
	// Not awaited — see the doc comment above. One fetch feeds both bell keys; `.then` on a promise
	// that never rejects cannot reject either, so neither streamed value can fail the layout.
	const bell = loadNotifications(locals.accessToken, fetch);
	return {
		// The NAME as well as the address. WorkOS carries `firstName`/`lastName` and this dropped
		// them, so the account row fell back to the email — `sidebar-user` renders the email small
		// underneath a name, and with no name there was nothing for it to sit under.
		user: locals.user
			? {
					email: locals.user.email,
					name: [locals.user.firstName, locals.user.lastName].filter(Boolean).join(' ') || undefined
				}
			: null,
		permissions: locals.permissions,
		role: locals.role,
		branding: locals.branding,
		appName: locals.appName,
		notifications: bell.then((b) => b.items),
		notificationsUnread: bell.then((b) => b.unread),
		chatsUnread: loadChatsUnread(locals.accessToken, canReadMessages, fetch),
		traceId: locals.traceId,
		spanId: locals.spanId
	};
};
