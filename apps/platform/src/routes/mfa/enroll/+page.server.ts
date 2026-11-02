import { fail, redirect } from '@sveltejs/kit';
import {
	codeRefused,
	loginNotice,
	readSignInClient,
	reportAuthEvent,
	returnToFrom,
	validatePortalSession,
	withReturnTo,
	workosClientFields
} from '@graphene/auth';
import { env } from '$env/dynamic/private';
import {
	ENROLL_COOKIE,
	SESSION_COOKIE,
	cookieOptions,
	isValidCookiePassword,
	pendingTokenInvalid,
	type MfaEnrollment
} from '$lib/server/workos';
import type { Actions, PageServerLoad } from './$types';

/** Parse the login-set enrolment cookie, or null if missing/garbled. */
function readEnrollment(raw: string | undefined): MfaEnrollment | null {
	if (!raw) return null;
	try {
		const e = JSON.parse(raw) as MfaEnrollment;
		return e.pendingAuthenticationToken && e.userId ? e : null;
	} catch {
		return null;
	}
}

export const load: PageServerLoad = async ({ locals, cookies, url }) => {
	// `?next=` rides in from the login step, so every exit from here keeps the user's place.
	if (locals.user) redirect(303, returnToFrom(url));
	if (!readEnrollment(cookies.get(ENROLL_COOKIE))) {
		cookies.delete(ENROLL_COOKIE, { path: '/' });
		redirect(303, withReturnTo('/login', { pathname: returnToFrom(url) }));
	}
	return {};
};

export const actions: Actions = {
	// Enrol a fresh TOTP factor and hand its QR + secret to the page. WorkOS only
	// returns these at creation, so the factor id rides a hidden field into verify.
	enroll: async ({ cookies, locals }) => {
		const enrollment = readEnrollment(cookies.get(ENROLL_COOKIE));
		if (!enrollment) {
			cookies.delete(ENROLL_COOKIE, { path: '/' });
			redirect(303, '/login');
		}
		try {
			const { authenticationFactor } =
				await locals.auth.workos.multiFactorAuth.createUserAuthFactor({
					userId: enrollment.userId,
					type: 'totp',
					totpIssuer: 'Graphene',
					totpUser: enrollment.email ?? enrollment.userId
				});
			return {
				step: 'confirm' as const,
				factorId: authenticationFactor.id,
				qrCode: authenticationFactor.totp?.qrCode,
				secret: authenticationFactor.totp?.secret
			};
		} catch (e) {
			console.error('[mfa-enroll] enrollAuthFactor failed:', e);
			return fail(500, { error: 'Could not start MFA enrolment. Please sign in again.' });
		}
	},

	// Challenge the freshly enrolled factor and exchange the code + pending token
	// for a sealed session — this both confirms enrolment and completes login.
	verify: async ({ request, cookies, locals, url, fetch, getClientAddress }) => {
		const enrollment = readEnrollment(cookies.get(ENROLL_COOKIE));
		if (!enrollment) {
			cookies.delete(ENROLL_COOKIE, { path: '/' });
			redirect(303, '/login');
		}

		const data = await request.formData();
		const factorId = String(data.get('factorId') ?? '');
		const code = String(data.get('code') ?? '').trim();
		if (!factorId || !code)
			return fail(400, {
				step: 'confirm' as const,
				factorId,
				error: 'Enter the 6-digit code from your authenticator app.'
			});

		// A short/missing cookie password would let the code verify but throw when
		// sealing the session — which used to surface as a bogus "invalid code".
		// Fail clearly (and log) instead of blaming the user's code.
		if (!isValidCookiePassword(locals.auth.cookiePassword)) {
			console.error(
				'[mfa-enroll] cannot seal session: WORKOS_COOKIE_PASSWORD missing or too short.'
			);
			return fail(503, {
				step: 'confirm' as const,
				factorId,
				error: 'Sign-in is temporarily unavailable. Please try again later.'
			});
		}

		const client = readSignInClient(request, getClientAddress);
		let sealedSession: string | undefined;
		let accessToken: string | undefined;
		try {
			const { id: authenticationChallengeId } =
				await locals.auth.workos.multiFactorAuth.challengeFactor({
					authenticationFactorId: factorId
				});
			const res = await locals.auth.workos.userManagement.authenticateWithTotp({
				clientId: locals.auth.clientId,
				code,
				pendingAuthenticationToken: enrollment.pendingAuthenticationToken,
				authenticationChallengeId,
				...workosClientFields(client),
				session: { sealSession: true, cookiePassword: locals.auth.cookiePassword }
			});
			sealedSession = res.sealedSession;
			accessToken = res.accessToken;
		} catch (e) {
			console.error('[mfa-enroll] authenticateWithTotp failed:', e);
			if (pendingTokenInvalid(e)) {
				cookies.delete(ENROLL_COOKIE, { path: '/' });
				redirect(303, loginNotice('expired', returnToFrom(url)));
			}
			// Only a refusal is the user's code — see /mfa. The factor id rides back so the same
			// QR stays on screen; a transient failure must not make them re-scan.
			if (!codeRefused(e))
				return fail(503, {
					step: 'confirm' as const,
					factorId,
					error: "We couldn't check your code just now. Please try again in a moment."
				});
			return fail(401, {
				step: 'confirm' as const,
				factorId,
				error: "That code wasn't right. Try again."
			});
		}
		if (!sealedSession)
			return fail(500, {
				step: 'confirm' as const,
				factorId,
				error: 'No session returned from WorkOS.'
			});
		const portalAccess = await validatePortalSession(locals.auth.workos, accessToken, 'platform', {
			apiUrl: env.API_URL ?? '',
			fetch
		});
		if (portalAccess !== 'allowed') {
			cookies.delete(ENROLL_COOKIE, { path: '/' });
			return fail(portalAccess === 'denied' ? 403 : 503, {
				step: 'confirm' as const,
				factorId,
				error:
					portalAccess === 'denied'
						? 'This account is deactivated or does not have access to this portal.'
						: 'Sign-in is temporarily unavailable. Please return to sign in and try again.'
			});
		}

		cookies.set(SESSION_COOKIE, sealedSession, cookieOptions);
		cookies.delete(ENROLL_COOKIE, { path: '/' });
		// Record the sign-in — enrolment completes the login here, so without this a first-time-MFA
		// user produces no auth-login event. Method 'mfa'; the subject is the enrolling user.
		await reportAuthEvent('login', {
			apiUrl: env.API_URL ?? '',
			internalToken: env.INTERNAL_API_TOKEN ?? '',
			host: url.host,
			workosUserId: enrollment.userId,
			method: 'mfa',
			client,
			fetch
		});
		// The whole point of carrying `next` through login → enrol: land where they were going.
		redirect(303, returnToFrom(url));
	}
};
