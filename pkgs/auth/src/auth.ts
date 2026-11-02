import type { WorkOS } from '@workos-inc/node';
import {
	emailVerificationRequired,
	mfaChallengeRequired,
	mfaEnrollmentRequired,
	organizationSelectionRequired,
	type MfaChallenge,
	type MfaEnrollment
} from './errors';
import { linkRejection, passwordRejection } from './password-errors';
import { type SignInClient, workosClientFields } from './client-context';
import { sessionIdFromAccessToken } from './sessions';
import { MIN_PASSWORD_LENGTH, WEAK_PASSWORD_MESSAGE } from './validation';

/**
 * Outcome of a password authentication attempt, normalised across the WorkOS
 * success path and its "extra step required" error responses. Callers (login)
 * apply the cookies and redirects so this stays free of SvelteKit deps and
 * easy to unit-test.
 */
export type PasswordAuthResult =
	// `userId`/`organizationId` (the authenticated WorkOS identity, already in the SDK response)
	// let the caller report the sign-in to `POST /internal/auth-event` without unsealing the session.
	| {
			status: 'authenticated';
			sealedSession: string;
			accessToken?: string;
			userId?: string;
			organizationId?: string;
	  }
	| { status: 'mfa'; challenge: MfaChallenge }
	| { status: 'enroll'; enrollment: MfaEnrollment }
	| { status: 'verify'; pendingAuthenticationToken: string }
	// The server can't seal a session (e.g. WORKOS_COOKIE_PASSWORD missing/too
	// short). Distinct from 'failed' so callers don't tell the user their
	// credentials were wrong when it's a server config problem.
	| { status: 'misconfigured' }
	| { status: 'failed' };

// Defined in ./validation (with the other password copy) and re-exported here so the existing
// `from './auth'` / `@graphene/auth` import sites keep working.
export { WEAK_PASSWORD_MESSAGE };

/** Outcome of completing a set/reset-password (from an emailed link). */
export type SetPasswordResult =
	// WorkOS returns the user it just reset, so callers get the identity without a second lookup —
	// the portals push it to `POST /internal/onboarding-complete` so the mirror stops showing a
	// person who has onboarded as never-invited.
	| { status: 'ok'; email?: string; userId?: string }
	// The link is invalid or expired — the user must request a new one.
	| { status: 'invalid' }
	// The password was rejected (too weak / breached); message is safe to show.
	| { status: 'weak'; message: string }
	| { status: 'failed' };

/** Complete a password set/reset with the token from the emailed link. Self-hosted: this is
 * what the portal's set-password page calls instead of AuthKit's hosted page. */
export async function confirmPasswordReset(
	workos: WorkOS,
	token: string,
	newPassword: string
): Promise<SetPasswordResult> {
	try {
		const res = await workos.userManagement.resetPassword({ token, newPassword });
		// `{ user }` per the SDK (userManagement.resetPassword -> Promise<{ user: User }>); read
		// defensively so a stubbed/older client still yields a plain 'ok'.
		const user = (res as { user?: { id?: string; email?: string } } | undefined)?.user;
		return { status: 'ok', email: user?.email, userId: user?.id };
	} catch (e) {
		// THE LINK FIRST. `password_reset_token_expired` names both subjects, and an expired link is
		// not a bad password — telling somebody to pick a stronger one sends them off changing the
		// only thing that was fine.
		if (linkRejection(e)) return { status: 'invalid' };
		// `password-errors` reads the code off the INSTANCE as well as `rawData`, which is what
		// makes this branch reachable at all: a weak password is a 422 and the SDK's
		// `UnprocessableEntityException` has no `rawData` (see that module's header).
		// The LENGTH of what we sent, never the password: it is what lets the classifier notice the
		// provider refusing as "too short" something that already cleared our own floor — proof the
		// dashboard minimum has moved above `MIN_PASSWORD_LENGTH`.
		const rejection = passwordRejection(e, {
			length: newPassword.length,
			minLength: MIN_PASSWORD_LENGTH
		});
		if (rejection) return { status: 'weak', message: rejection };
		console.error('[auth] confirmPasswordReset failed:', e);
		return { status: 'failed' };
	}
}

/** Set the first password behind a long-lived WorkOS invitation, then consume that invitation.
 *
 * Invitation tokens cannot set passwords themselves. The password-reset credential is therefore
 * minted only after the user submits the form and consumed immediately; the emailed credential
 * remains the configurable, revocable invitation token.
 */
export async function acceptInvitationAndSetPassword(
	workos: WorkOS,
	invitationToken: string,
	newPassword: string
): Promise<SetPasswordResult> {
	let settingPassword = false;
	try {
		const invitation = await workos.userManagement.findInvitationByToken(invitationToken);
		if (String(invitation.state).toLowerCase() !== 'pending') return { status: 'invalid' };
		const reset = await workos.userManagement.createPasswordReset({ email: invitation.email });
		settingPassword = true;
		const result = await workos.userManagement.resetPassword({
			token: reset.passwordResetToken,
			newPassword
		});
		const user = result.user;
		if (user.email.toLowerCase() !== invitation.email.toLowerCase()) return { status: 'failed' };
		settingPassword = false;
		await workos.userManagement.acceptInvitation(invitation.id);
		return { status: 'ok', email: user.email, userId: user.id };
	} catch (e) {
		// The password is checked FIRST here and the link second — the mirror of
		// `confirmPasswordReset` — because `settingPassword` says which call we were inside. Only
		// the password call can fail on the password, so an invitation error cannot be misread as a
		// weak one; past that flag, everything else is the invitation.
		if (settingPassword) {
			const rejection = passwordRejection(e, {
				length: newPassword.length,
				minLength: MIN_PASSWORD_LENGTH
			});
			if (rejection) return { status: 'weak', message: rejection };
		}
		if (linkRejection(e)) return { status: 'invalid' };
		console.error('[auth] acceptInvitationAndSetPassword failed:', e);
		return { status: 'failed' };
	}
}

export interface PasswordAuthConfig {
	clientId: string;
	cookiePassword: string;
	/** Authenticate the user into this specific WorkOS organisation. Set it when a portal maps
	 * to one org (e.g. the admin portal → Internal Operations): it makes login deterministic and
	 * avoids the `organization_selection_required` error for users who belong to several orgs. */
	organizationId?: string;
}

/**
 * WorkOS seals session cookies with iron-session, which requires the password to
 * be at least 32 characters. A shorter value throws only at *seal* time — which
 * surfaces downstream as a baffling "invalid code" on the MFA step — so callers
 * should check it up front and fail clearly instead.
 */
export const COOKIE_PASSWORD_MIN_LENGTH = 32;

export function isValidCookiePassword(password: string | undefined | null): boolean {
	return typeof password === 'string' && password.length >= COOKIE_PASSWORD_MIN_LENGTH;
}

/** Authenticate with email + password and classify the result.
 *
 * `client` is the browser this sign-in came from. WorkOS stores it on the session only when the
 * authenticate call sends it — a server-side password grant otherwise records a null IP and user
 * agent. Forward it on every call that can mint the session, including organisation selection.
 */
export async function passwordAuth(
	workos: WorkOS,
	config: PasswordAuthConfig,
	email: string,
	password: string,
	client?: SignInClient
): Promise<PasswordAuthResult> {
	if (!isValidCookiePassword(config.cookiePassword)) {
		// Deliberately does NOT name an env var. In the three portals this value is NOT read from the
		// environment at all: `resolveAuth` fetches it per REQUEST HOST from `/internal/env-config`,
		// which reads `auth.environment_secrets`. So the overwhelmingly common cause is a host that
		// matches no `auth.tenant_environments` row — browsing `localhost:5185` when the row says
		// `portals2.localhost:5185`, say — and the old wording ("set a long random value in the
		// environment") sent you to a file that has nothing to do with it. Name both causes, most
		// likely first, and name the thing to check.
		console.error(
			'[auth] no usable cookie password for this request, so a session cannot be sealed. ' +
				'Either the request host matches no configured environment (check the host against ' +
				'auth.tenant_environments — this is the usual cause), or the environment it matched ' +
				`has a cookie password shorter than ${COOKIE_PASSWORD_MIN_LENGTH} characters.`
		);
		return { status: 'misconfigured' };
	}
	if (!config.clientId) {
		// The WorkOS SDK throws TypeError("clientId is required") for an empty id, and the login
		// pages report every thrown error here as "Invalid email or password." A portal whose
		// application was never stored (auth.tenant_environments.*_client_id) is unconfigured, the
		// same 503 as a missing cookie password — never a verdict on the password.
		console.error(
			'[auth] this host has no WorkOS application client id, so sign-in cannot start. ' +
				'The environment row matched, but its portal application is unset ' +
				'(auth.tenant_environments client_portal_client_id, platform_client_id, or ' +
				'operations_client_id).'
		);
		return { status: 'misconfigured' };
	}
	const session = { sealSession: true, cookiePassword: config.cookiePassword };
	const browser = workosClientFields(client);
	try {
		const res = await workos.userManagement.authenticateWithPassword({
			clientId: config.clientId,
			email,
			password,
			...browser,
			session
		});
		return res.sealedSession
			? {
					status: 'authenticated',
					sealedSession: res.sealedSession,
					...(res.accessToken ? { accessToken: res.accessToken } : {}),
					userId: res.user?.id,
					organizationId: res.organizationId ?? undefined
				}
			: { status: 'failed' };
	} catch (e) {
		const mfa = mfaChallengeRequired(e);
		if (mfa) return { status: 'mfa', challenge: mfa };

		const enrollment = mfaEnrollmentRequired(e);
		if (enrollment) return { status: 'enroll', enrollment };

		const pending = emailVerificationRequired(e);
		if (pending)
			return { status: 'verify', pendingAuthenticationToken: pending.pendingAuthenticationToken };

		// The user belongs to several orgs. `authenticateWithPassword` can't take an org, so
		// complete the sign-in for this portal's org via the pending token. (Requires knowing
		// the portal's org id — the caller resolves it per host / per portal.)
		const orgSel = organizationSelectionRequired(e);
		if (orgSel && config.organizationId) {
			try {
				const res = await workos.userManagement.authenticateWithOrganizationSelection({
					clientId: config.clientId,
					organizationId: config.organizationId,
					pendingAuthenticationToken: orgSel.pendingAuthenticationToken,
					...browser,
					session
				});
				return res.sealedSession
					? {
							status: 'authenticated',
							sealedSession: res.sealedSession,
							...(res.accessToken ? { accessToken: res.accessToken } : {}),
							userId: res.user?.id,
							organizationId: res.organizationId ?? undefined
						}
					: { status: 'failed' };
			} catch (e2) {
				// MFA can still be demanded after org selection.
				const mfa2 = mfaChallengeRequired(e2);
				if (mfa2) return { status: 'mfa', challenge: mfa2 };
				const enroll2 = mfaEnrollmentRequired(e2);
				if (enroll2) return { status: 'enroll', enrollment: enroll2 };
				console.error('[auth] authenticateWithOrganizationSelection failed:', e2);
				return { status: 'failed' };
			}
		}

		console.error('[auth] authenticateWithPassword failed:', e);
		return { status: 'failed' };
	}
}

/**
 * Outcome of a re-authentication check.
 * `ok`    — the password is correct (or correct-but-another-step-required).
 * `wrong` — the attempt did not confirm the password, so the action must not proceed.
 */
export type PasswordCheck = 'ok' | 'wrong';

/**
 * Confirm the user's CURRENT password — the step-up check before changing a password or turning
 * 2FA off — without leaving a live session behind.
 *
 * Why not `passwordAuth`: it always asks WorkOS to seal a session, so every re-auth minted a real
 * session that was then thrown away (three surfaces doing it, and no revocation anywhere). Here we
 * authenticate without sealing and immediately revoke the session the call created, so a step-up
 * check leaves no credential and no session behind.
 *
 * An "extra step required" answer (MFA challenge/enrolment, unverified email, org selection) means
 * the password itself was accepted — WorkOS only reaches those states after checking it — so it
 * counts as `ok`. Anything else is `wrong` (fail-closed, and the same classification `passwordAuth`
 * has always used: WorkOS's exact code for a bad password is undocumented, so we do not guess at a
 * finer split).
 */
export async function verifyPassword(
	workos: WorkOS,
	config: { clientId: string },
	email: string,
	password: string
): Promise<PasswordCheck> {
	try {
		const res = await workos.userManagement.authenticateWithPassword({
			clientId: config.clientId,
			email,
			password
		});
		const sessionId = sessionIdFromAccessToken(res?.accessToken);
		if (sessionId) {
			try {
				await workos.userManagement.revokeSession({ sessionId });
			} catch (e) {
				// Best-effort: the check has already succeeded, and a lingering session is not a
				// reason to refuse the user's password change.
				console.error('[auth] could not revoke the re-authentication session:', e);
			}
		}
		return 'ok';
	} catch (e) {
		if (
			mfaChallengeRequired(e) ||
			mfaEnrollmentRequired(e) ||
			emailVerificationRequired(e) ||
			organizationSelectionRequired(e)
		)
			return 'ok';
		console.error('[auth] re-authentication failed:', e);
		return 'wrong';
	}
}
