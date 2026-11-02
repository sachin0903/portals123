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
	MFA_COOKIE,
	SESSION_COOKIE,
	cookieOptions,
	isValidCookiePassword,
	pendingTokenInvalid,
	type MfaChallenge
} from '$lib/server/workos';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, cookies, url }) => {
	// `?next=` rides in from the login step, so every exit from here keeps the user's place.
	if (locals.user) redirect(303, returnToFrom(url));
	if (!cookies.get(MFA_COOKIE))
		redirect(303, withReturnTo('/login', { pathname: returnToFrom(url) }));
	return {};
};

export const actions: Actions = {
	default: async ({ request, cookies, locals, url, fetch, getClientAddress }) => {
		const raw = cookies.get(MFA_COOKIE);
		if (!raw) redirect(303, '/login');

		let challenge: MfaChallenge;
		try {
			challenge = JSON.parse(raw) as MfaChallenge;
		} catch {
			// Corrupted/garbled cookie — clear it and restart the flow rather than 500.
			cookies.delete(MFA_COOKIE, { path: '/' });
			redirect(303, '/login');
		}
		if (!challenge.pendingAuthenticationToken || !challenge.factorId) {
			cookies.delete(MFA_COOKIE, { path: '/' });
			redirect(303, '/login');
		}

		const data = await request.formData();
		const code = String(data.get('code') ?? '').trim();
		if (!code) return fail(400, { error: 'Enter the code from your authenticator app.' });

		// A short/missing cookie password would let the code verify but throw when
		// sealing the session — which used to surface as a bogus "invalid code".
		// Fail clearly (and log) instead of blaming the user's code.
		if (!isValidCookiePassword(locals.auth.cookiePassword)) {
			console.error('[mfa] cannot seal session: WORKOS_COOKIE_PASSWORD missing or too short.');
			return fail(503, { error: 'Sign-in is temporarily unavailable. Please try again later.' });
		}

		const client = readSignInClient(request, getClientAddress);
		let sealedSession: string | undefined;
		let accessToken: string | undefined;
		let workosUserId: string | undefined;
		try {
			// WorkOS doesn't pre-create a challenge for the mfa_challenge error, so
			// create a fresh one for the factor on each attempt, then exchange the code.
			const { id: authenticationChallengeId } =
				await locals.auth.workos.multiFactorAuth.challengeFactor({
					authenticationFactorId: challenge.factorId
				});
			const res = await locals.auth.workos.userManagement.authenticateWithTotp({
				clientId: locals.auth.clientId,
				code,
				pendingAuthenticationToken: challenge.pendingAuthenticationToken,
				authenticationChallengeId,
				...workosClientFields(client),
				session: { sealSession: true, cookiePassword: locals.auth.cookiePassword }
			});
			sealedSession = res.sealedSession;
			accessToken = res.accessToken;
			workosUserId = res.user?.id;
		} catch (e) {
			console.error('[mfa] authenticateWithTotp failed:', e);
			// The pending token is single-use and short-lived. Once it expires there's
			// no point retrying codes — restart the sign-in to mint a fresh one.
			if (pendingTokenInvalid(e)) {
				cookies.delete(MFA_COOKIE, { path: '/' });
				redirect(303, loginNotice('expired', returnToFrom(url)));
			}
			// Only a refusal is the user's code. A WorkOS outage, our own quota or a rejected API
			// key used to read as "wrong code" to someone typing the right one; the pending token
			// is still live in those cases, so the form stays up for a retry.
			if (!codeRefused(e))
				return fail(503, {
					error: "We couldn't check your code just now. Please try again in a moment."
				});
			return fail(401, { error: "That code wasn't right. Try again." });
		}
		if (!sealedSession) return fail(500, { error: 'No session returned from WorkOS.' });
		const portalAccess = await validatePortalSession(
			locals.auth.workos,
			accessToken,
			'operations',
			{
				apiUrl: env.API_URL ?? '',
				fetch
			}
		);
		if (portalAccess !== 'allowed') {
			cookies.delete(MFA_COOKIE, { path: '/' });
			return fail(portalAccess === 'denied' ? 403 : 503, {
				error:
					portalAccess === 'denied'
						? 'This account is deactivated or does not have access to this portal.'
						: 'Sign-in is temporarily unavailable. Please return to sign in and try again.'
			});
		}

		cookies.set(SESSION_COOKIE, sealedSession, cookieOptions);
		cookies.delete(MFA_COOKIE, { path: '/' });
		// Record the sign-in — an MFA login completes HERE, not on the password path, so without
		// this the whole MFA cohort produces no auth-login event. Method is 'mfa' (WorkOS-aligned).
		await reportAuthEvent('login', {
			apiUrl: env.API_URL ?? '',
			internalToken: env.INTERNAL_API_TOKEN ?? '',
			host: url.host,
			workosUserId,
			method: 'mfa',
			client,
			fetch
		});
		// The whole point of carrying `next` through login → MFA: land where they were going.
		redirect(303, returnToFrom(url));
	}
};
