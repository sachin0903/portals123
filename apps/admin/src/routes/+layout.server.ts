import { env } from '$env/dynamic/private';
import { redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { instanceStage } from '$lib/instance-stage';
import { SESSION_COOKIE } from '$lib/server/workos';
import type { NotificationItemData } from '@graphene/ui';
import type { LayoutServerLoad } from './$types';

/** Which portal THIS app is. A session resolving to any other portal is refused (below). */
const THIS_PORTAL = 'operations';

/** A confirmed portal from GET /me. `undefined` is not an answer — fail open. */
function refusePortal(portal: string | null | undefined, expected: string): boolean {
	return portal !== undefined && portal !== expected;
}

/** The bell's initial state: recent notifications + the unread count. Best-effort — a failure
 * yields an empty bell, never a broken shell; the badge self-heals on its next poll. */
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

/** Surface the signed-in user + their permissions/role to the layout so the app shell
 * can render the user and gate permission-scoped nav links — plus the Graphene logo set
 * (`brand`, resolved in hooks.server.ts) so the shells wear the branding-editor logos.
 *
 * Only the wrong-portal redirect is awaited — it must be thrown before the response starts. The
 * bell's seed is STREAMED (returned as promises, which SvelteKit 2 does not await) so the shell's
 * HTML is not held back by another API round trip; the platform portal's root layout records the
 * reasoning in full. The login terms check used to be awaited here too and was removed on
 * 13 Oct 2026. `loadNotifications` never rejects, so a streamed failure is an empty bell. */
export const load: LayoutServerLoad = async ({ locals, fetch, cookies }) => {
	if (locals.user && locals.accessToken && refusePortal(locals.portal, THIS_PORTAL)) {
		cookies.delete(SESSION_COOKIE, { path: '/' });
		redirect(303, '/login?wrong_portal=1');
	}

	const bell = loadNotifications(locals.accessToken, fetch);
	return {
		user: locals.user ? { email: locals.user.email } : null,
		permissions: locals.permissions,
		role: locals.role,
		brand: locals.brand,
		notifications: bell.then((b) => b.items),
		notificationsUnread: bell.then((b) => b.unread),
		traceId: locals.traceId,
		spanId: locals.spanId,
		// Same `APP_STAGE` the API is given. The tenant page uses it to show the outbound-email
		// restriction on dev and staging only.
		appStage: instanceStage(env.APP_STAGE)
	};
};
