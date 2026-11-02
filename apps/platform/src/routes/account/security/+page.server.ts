import { fail, redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import {
	MIN_PASSWORD_LENGTH,
	PASSWORD_HINT,
	loginPath,
	fillCurrentBrowser,
	loadStoredSessions,
	passwordRejection,
	readSignInClient,
	refreshStoredSessions,
	revokeOtherSessions,
	revokeOwnedSession,
	sessionIdFromAccessToken,
	validateNewPassword,
	verifyPassword,
	type SessionSweep
} from '@graphene/auth';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url, request, getClientAddress }) => {
	// Session expired mid-task: keep the user's place so they land back here after signing in.
	if (!locals.user) redirect(303, loginPath(url));
	const userId = locals.user.id;
	const enrolledPromise = (async () => {
		try {
			const factors = await locals.auth.workos.multiFactorAuth.listUserAuthFactors({
				userId
			});
			return factors.data.some((f) => f.type === 'totp');
		} catch (e) {
			console.error('[mfa-settings] listAuthFactors failed:', e);
			return false;
		}
	})();
	const [enrolled, stored] = await Promise.all([
		enrolledPromise,
		loadStoredSessions(env.API_URL, locals.accessToken)
	]);
	return {
		email: locals.user.email,
		enrolled,
		liveSessions: fillCurrentBrowser(stored.sessions, readSignInClient(request, getClientAddress)),
		sessionsListed: stored.listed,
		// The password rule, from the same constants the action enforces — so the form's `minlength`
		// and its caption cannot say "at least 8 characters" while `validateNewPassword` refuses
		// anything under 10. Surfaced through `data` to keep `@graphene/auth` out of the client bundle.
		minPasswordLength: MIN_PASSWORD_LENGTH,
		passwordHint: PASSWORD_HINT
	};
};

/** Sweep the caller's OTHER live sessions, keeping the one making this request.
 *
 * Shared by the password change (a rotated credential must not leave sessions minted with the old
 * one alive) and by the explicit "sign out everywhere else" button, so the affordance and the
 * automatic sweep cannot drift apart. `sid` comes from the access token this request carries; if it
 * cannot be read, `revokeOtherSessions` sweeps everything and reports `sweptCurrent` so the page
 * can tell the user they will have to sign in again — the guarantee is never silently skipped.
 *
 * One assumption worth naming: on the password path, `verifyPassword` has already revoked the
 * session its own check created, so the sweep should see it as `revoked` and skip it. If WorkOS
 * were eventually consistent about that, the reported count would be one too high (it would be
 * revoked twice, harmlessly). We await the revoke, so this holds. */
async function sweepOtherSessions(locals: App.Locals): Promise<SessionSweep> {
	const sweep = await revokeOtherSessions(locals.auth.workos, {
		userId: locals.user!.id,
		keepSessionId: sessionIdFromAccessToken(locals.accessToken)
	});
	// The sweep has to ask WorkOS, or a session the mirror has not seen stays signed in.
	// Refresh copies that result locally so the next paint is not a poll behind.
	if (sweep.listed) await refreshStoredSessions(env.API_URL, locals.accessToken);
	return sweep;
}

export const actions: Actions = {
	enroll: async ({ locals, url }) => {
		if (!locals.user) redirect(303, loginPath({ pathname: url.pathname }));
		try {
			const { authenticationFactor } =
				await locals.auth.workos.multiFactorAuth.createUserAuthFactor({
					userId: locals.user.id,
					type: 'totp',
					totpIssuer: 'Graphene',
					totpUser: locals.user.email
				});
			return {
				step: 'confirm' as const,
				factorId: authenticationFactor.id,
				qrCode: authenticationFactor.totp?.qrCode,
				secret: authenticationFactor.totp?.secret
			};
		} catch (e) {
			console.error('[mfa-settings] enrollAuthFactor failed:', e);
			return fail(500, { error: 'Could not start MFA enrolment.' });
		}
	},

	confirm: async ({ request, locals, url }) => {
		if (!locals.user) redirect(303, loginPath({ pathname: url.pathname }));
		const data = await request.formData();
		const factorId = String(data.get('factorId') ?? '');
		const code = String(data.get('code') ?? '').trim();
		if (!factorId || !code)
			return fail(400, { step: 'confirm' as const, factorId, error: 'Enter the 6-digit code.' });

		try {
			const challenge = await locals.auth.workos.multiFactorAuth.challengeFactor({
				authenticationFactorId: factorId
			});
			const result = await locals.auth.workos.multiFactorAuth.verifyChallenge({
				authenticationChallengeId: challenge.id,
				code
			});
			if (!result.valid)
				return fail(400, {
					step: 'confirm' as const,
					factorId,
					error: 'That code did not match. Try again.'
				});
		} catch (e) {
			console.error('[mfa-settings] verifyChallenge failed:', e);
			return fail(400, {
				step: 'confirm' as const,
				factorId,
				error: 'That code was invalid or expired. Try again.'
			});
		}
		return { enrolled: true as const };
	},

	disable: async ({ request, locals, url }) => {
		if (!locals.user) redirect(303, loginPath({ pathname: url.pathname }));

		const data = await request.formData();
		const password = String(data.get('password') ?? '');
		if (!password)
			return fail(400, { error: 'Enter your password to disable two-factor authentication.' });

		// Re-authenticate before removing the factor. `verifyPassword` authenticates WITHOUT sealing
		// a session (and revokes the one WorkOS creates), so a step-up check leaves no session
		// behind. An "extra step required" answer still confirms the password, so the user does not
		// have to complete an MFA challenge in order to switch MFA off.
		if (
			(await verifyPassword(locals.auth.workos, locals.auth, locals.user.email, password)) !== 'ok'
		)
			return fail(401, { error: 'Incorrect password. Two-factor authentication was not changed.' });

		try {
			const factors = await locals.auth.workos.multiFactorAuth.listUserAuthFactors({
				userId: locals.user.id
			});
			await Promise.all(
				factors.data
					.filter((factor) => factor.type === 'totp')
					.map((factor) => locals.auth.workos.multiFactorAuth.deleteFactor(factor.id))
			);
		} catch (e) {
			console.error('[mfa-settings] deleteFactor failed:', e);
			return fail(500, { error: 'Could not disable two-factor authentication.' });
		}
		return { enrolled: false as const, disabled: true as const };
	},

	password: async ({ request, locals, url }) => {
		if (!locals.user) redirect(303, loginPath({ pathname: url.pathname }));
		const data = await request.formData();
		const current = String(data.get('current') ?? '');
		const next = String(data.get('next') ?? '');
		const confirm = String(data.get('confirm') ?? '');
		if (!current || !next) return fail(400, { pwError: 'Fill in both password fields.' });
		if (next !== confirm) return fail(400, { pwError: 'New passwords do not match.' });
		if (next === current)
			return fail(400, { pwError: 'Your new password must be different from the current one.' });
		const weak = validateNewPassword(next, locals.user.email);
		if (weak) return fail(400, { pwError: weak });

		// Re-authenticate before changing the password. Same reasoning as `disable`, and the same
		// session-free check — this used to mint a sealed session per attempt and discard it.
		if (
			(await verifyPassword(locals.auth.workos, locals.auth, locals.user.email, current)) !== 'ok'
		)
			return fail(401, { pwError: 'Your current password is incorrect.' });

		try {
			await locals.auth.workos.userManagement.updateUser({
				userId: locals.user.id,
				password: next
			});
		} catch (e) {
			console.error('[security] updateUser password failed:', e);
			// One classifier, shared with the set-password page (`@graphene/auth`), so the two
			// surfaces cannot disagree about what a rejection means. This used to stringify the error
			// and flatten every reason onto WEAK_PASSWORD_MESSAGE — so a password refused for being
			// too LONG was reported as appearing in a breach, which is both wrong and unactionable.
			// The length of what we sent, so a "too short" refusal on a password that already
			// cleared our own floor is caught as a policy divergence rather than echoed back
			// with a number we now know to be wrong.
			const rejection = passwordRejection(e, {
				length: next.length,
				minLength: MIN_PASSWORD_LENGTH
			});
			if (rejection) return fail(400, { pwError: rejection });
			return fail(500, { pwError: 'Could not change your password. Please try again.' });
		}

		// The credential is rotated; every session minted with the OLD one is still live. Without
		// this sweep an attacker holding a stolen sealed-session cookie keeps their access — exactly
		// what the user changing their password is trying to stop. Runs after the update so a failed
		// change never signs anyone out.
		return { passwordChanged: true as const, sessions: await sweepOtherSessions(locals) };
	},

	/** The user-facing affordance for the same sweep, usable without changing a password (a lost
	 * laptop, a shared machine). No step-up check: it only ever *removes* access. */
	signOutOthers: async ({ locals, url }) => {
		if (!locals.user) redirect(303, loginPath({ pathname: url.pathname }));
		const sessions = await sweepOtherSessions(locals);
		if (!sessions.listed)
			return fail(502, {
				sessionsError: 'Could not reach the identity provider to sign out your other sessions.'
			});
		return { signedOutOthers: true as const, sessions };
	},

	revokeSession: async ({ request, locals, url }) => {
		if (!locals.user) redirect(303, loginPath({ pathname: url.pathname }));
		const sessionId = String((await request.formData()).get('session_id') ?? '').trim();
		if (!sessionId) return fail(400, { sessionsError: 'A session is required.' });
		const result = await revokeOwnedSession(locals.auth.workos, {
			userId: locals.user.id,
			sessionId,
			keepSessionId: sessionIdFromAccessToken(locals.accessToken)
		});
		if (result === 'unavailable')
			return fail(502, {
				sessionsError: 'Could not reach the identity provider to sign out that session.'
			});
		if (result === 'current')
			return fail(400, { sessionsError: 'Use sign out to end this browser.' });
		if (result !== 'revoked')
			return fail(404, { sessionsError: 'That session is no longer signed in.' });
		await refreshStoredSessions(env.API_URL, locals.accessToken);
		return { revokedSession: true as const };
	}
};
