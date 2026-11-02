import { redirect } from '@sveltejs/kit';
import { reportAuthEvent } from '@graphene/auth';
import { env } from '$env/dynamic/private';
import { DEV_AUTH_COOKIE, SESSION_COOKIE } from '$lib/server/workos';
import type { Actions } from './$types';

export const actions: Actions = {
	default: async ({ cookies, locals, url, fetch }) => {
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
		// The dev bypass cookie too: with DEMO_AUTH_BYPASS on it beats the sealed session in the hook,
		// so a sign-out that cleared only the session left the browser signed in.
		cookies.delete(DEV_AUTH_COOKIE, { path: '/' });
		redirect(303, logoutUrl);
	}
};
