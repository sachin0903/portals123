/**
 * Names and attributes for the auth cookies. Shared across portals so the
 * session/MFA cookies are written identically everywhere. The `secure`
 * flag is injected (it depends on the app's dev/prod mode, which only the
 * SvelteKit layer knows) rather than read from `$app/environment` here.
 *
 * There is no email-verification cookie: verification is not a separate step in this
 * product. Provisioning (and forgot-password) email a set-password link via Resend, and
 * completing it verifies the address at WorkOS — see `internal.py::forgot_password`.
 */

// Carries the pending-auth token + user id/email between login and TOTP enrolment.

const SESSION_MAX_AGE = 60 * 60 * 24 * 400;
// Short-lived: carries a pending-auth token between steps.
const PENDING_MAX_AGE = 600;

export interface CookieOptions {
	path: '/';
	httpOnly: true;
	secure: boolean;
	sameSite: 'lax';
	maxAge: number;
}

export function cookieOptions(dev: boolean): CookieOptions {
	return { path: '/', httpOnly: true, secure: !dev, sameSite: 'lax', maxAge: SESSION_MAX_AGE };
}

export function pendingCookieOptions(dev: boolean): CookieOptions {
	return { ...cookieOptions(dev), maxAge: PENDING_MAX_AGE };
}
