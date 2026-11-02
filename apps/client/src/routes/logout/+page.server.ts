import { redirect } from '@sveltejs/kit';
import { reportAuthEvent } from '@graphene/auth';
import { env } from '$env/dynamic/private';
import { endViewAs } from '$lib/server/view-as';
import {
	ACTIVE_CONTACT_COOKIE,
	cookieOptions,
	DEV_AUTH_COOKIE,
	SESSION_COOKIE,
	VIEW_AS_COOKIE
} from '$lib/server/workos';
import type { Actions } from './$types';

export const actions: Actions = {
	default: async ({ cookies, locals, url, fetch }) => {
		// Sign out from inside a view-as session ends THAT session only. The shell still offers
		// Sign out, and following the normal path would call WorkOS `getLogoutUrl` for whichever
		// login shares this host — the adviser portal's — and revoke it.
		const viewAsToken = cookies.get(VIEW_AS_COOKIE);
		if (viewAsToken) {
			await endViewAs(viewAsToken, fetch);
			cookies.delete(VIEW_AS_COOKIE, { path: '/' });
			redirect(303, '/view-as/ended?left=1');
		}
		const sessionData = cookies.get(SESSION_COOKIE);
		// Record the sign-out before the session is cleared (the WorkOS id is on locals). Best-effort.
		if (locals.user?.id) {
			await reportAuthEvent('logout', {
				apiUrl: env.API_URL ?? '',
				internalToken: env.INTERNAL_API_TOKEN ?? '',
				host: url.host,
				workosUserId: locals.user.id,
				fetch
			});
		}
		let logoutUrl = '/';
		if (sessionData) {
			try {
				const session = locals.auth.workos.userManagement.loadSealedSession({
					sessionData,
					cookiePassword: locals.auth.cookiePassword
				});
				logoutUrl = await session.getLogoutUrl();
			} catch {
				// Session unreadable; just clear it locally.
			}
			cookies.delete(SESSION_COOKIE, { path: '/' });
		}
		cookies.delete(ACTIVE_CONTACT_COOKIE, cookieOptions);
		// The dev bypass cookie too: with DEMO_AUTH_BYPASS on, it beat the sealed session in the hook,
		// so a sign-out that cleared only the session left the browser signed in.
		cookies.delete(DEV_AUTH_COOKIE, { path: '/' });
		// And a staff member's view-as session, which the hook resolves ahead of the WorkOS one:
		// signing out from inside it must not leave the portal still answering as the client.
		cookies.delete(VIEW_AS_COOKIE, { path: '/' });
		redirect(303, logoutUrl);
	}
};
