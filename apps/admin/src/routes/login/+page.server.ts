import { fail, redirect } from '@sveltejs/kit';
import {
	RETURN_TO_PARAM,
	consumeRateLimit,
	loginNotice,
	readSignInClient,
	reportAuthEvent,
	requestPasswordReset,
	returnToFrom,
	validatePortalSession,
	withReturnTo,
	safeClientAddress
} from '@graphene/auth';
import { env } from '$env/dynamic/private';
import {
	ENROLL_COOKIE,
	MFA_COOKIE,
	SESSION_COOKIE,
	cookieOptions,
	pendingCookieOptions
} from '$lib/server/workos';
import { passwordAuth } from '$lib/server/auth';
import type { Actions, PageServerLoad } from './$types';

/** Why the user is looking at the sign-in form. The copy lives here (one place per portal)
 * and the page renders it in an Alert above the card. */
function notice(url: URL): { tone: 'default' | 'success'; message: string } | null {
	if (url.searchParams.has('set'))
		return { tone: 'success', message: 'Your password is set. Sign in to continue.' };
	if (url.searchParams.has('check'))
		return {
			tone: 'default',
			message:
				'Your email address is not confirmed yet. We have sent you a link. Open it to confirm ' +
				'your address and set a password, then sign in here.'
		};
	if (url.searchParams.has('wrong_portal'))
		return {
			tone: 'default',
			message: 'That account does not have access to this portal. Sign in to the right one.'
		};
	if (url.searchParams.has('expired'))
		return { tone: 'default', message: 'That sign-in attempt timed out. Please start again.' };
	// A page guard sent them here, so they were part-way through something.
	if (url.searchParams.has(RETURN_TO_PARAM))
		return {
			tone: 'default',
			message: 'Your session has ended. Sign in to pick up where you left off.'
		};
	return null;
}

export const load: PageServerLoad = async ({ locals, url }) => {
	if (locals.user) redirect(303, returnToFrom(url));
	return { notice: notice(url) };
};

export const actions: Actions = {
	default: async ({ request, cookies, locals, url, fetch, getClientAddress }) => {
		const data = await request.formData();
		const email = String(data.get('email') ?? '').trim();
		const password = String(data.get('password') ?? '');
		if (!email || !password) return fail(400, { error: 'Email and password are required.', email });

		// Bound password spraying. The portal does the WorkOS call itself, so the API never sees this
		// attempt and cannot throttle it — we hand the count over. Fails open if the API is
		// unreachable: a login form that refused everyone would be a worse outage than an unthrottled
		// window. (`login_limits` shipped with no caller, so this was entirely unthrottled.)
		const client = readSignInClient(request, getClientAddress);
		const limit = await consumeRateLimit('login', email, url.host, {
			apiUrl: env.API_URL ?? '',
			internalToken: env.INTERNAL_API_TOKEN ?? '',
			clientIp: client.ipAddress ?? null,
			fetch
		});
		if (!limit.allowed) {
			const mins = Math.max(1, Math.ceil(limit.retryAfterSeconds / 60));
			return fail(429, {
				error: `Too many sign-in attempts. Try again in ${mins} minute${mins === 1 ? '' : 's'}.`,
				email
			});
		}

		// The form POSTs to the current URL, so `?next=` survives the submit. `returnToFrom`
		// validates it (same-origin, path-only) — a raw value here would be an open redirect.
		const next = returnToFrom(url);

		const result = await passwordAuth(locals.auth, email, password, client);
		if (result.status === 'misconfigured')
			return fail(503, {
				error: 'Sign-in is temporarily unavailable. Please try again later.',
				email
			});
		if (result.status === 'failed') {
			await reportAuthEvent('login_failed', {
				apiUrl: env.API_URL ?? '',
				internalToken: env.INTERNAL_API_TOKEN ?? '',
				host: url.host,
				email,
				method: 'password',
				fetch
			});
			return fail(401, { error: 'Invalid email or password.', email });
		}
		if (result.status === 'mfa') {
			cookies.set(MFA_COOKIE, JSON.stringify(result.challenge), pendingCookieOptions);
			redirect(303, withReturnTo('/mfa', { pathname: next }));
		}
		if (result.status === 'enroll') {
			cookies.set(ENROLL_COOKIE, JSON.stringify(result.enrollment), pendingCookieOptions);
			redirect(303, withReturnTo('/mfa/enroll', { pathname: next }));
		}
		if (result.status === 'verify') {
			// Email not confirmed. This product has exactly ONE mechanism for that: the Resend'd
			// set-password link — completing it verifies the address at WorkOS (see
			// `internal.py::forgot_password`). WorkOS's own emails are disabled by design, so there
			// is no code for the user to enter and no /verify page; `?check=1` says so plainly.
			await requestPasswordReset(email, url.host, {
				apiUrl: env.API_URL ?? '',
				internalToken: env.INTERNAL_API_TOKEN ?? '',
				clientIp: safeClientAddress(getClientAddress),
				fetch
			});
			// Carry `next` through: the user is being sent away to confirm their email, and must
			// land back where they were heading once they sign in.
			redirect(303, loginNotice('check', next));
		}
		const portalAccess = await validatePortalSession(
			locals.auth.workos,
			result.accessToken,
			'operations',
			{ apiUrl: env.API_URL ?? '', fetch }
		);
		if (portalAccess === 'denied')
			return fail(403, {
				error: 'This account is deactivated or does not have access to this portal.',
				email
			});
		if (portalAccess === 'unavailable')
			return fail(503, {
				error: 'Sign-in is temporarily unavailable. Please try again later.',
				email
			});
		await reportAuthEvent('login', {
			apiUrl: env.API_URL ?? '',
			internalToken: env.INTERNAL_API_TOKEN ?? '',
			host: url.host,
			workosUserId: result.userId,
			method: 'password',
			client,
			fetch
		});
		cookies.set(SESSION_COOKIE, result.sealedSession, cookieOptions);
		redirect(303, next);
	}
};
