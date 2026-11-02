/**
 * Classifiers for the error shapes WorkOS throws from its authentication
 * endpoints. Pure functions over the raw error body — no SvelteKit or env
 * dependencies — so they live in the shared package and are unit-tested here.
 */

export interface PendingVerification {
	pendingAuthenticationToken: string;
	email?: string;
}

export interface MfaChallenge {
	pendingAuthenticationToken: string;
	/** The TOTP factor to challenge. WorkOS does not pre-create a challenge, so
	 * we hold the factor id and call `challengeFactor` at verification time. */
	factorId: string;
}

export interface MfaEnrollment {
	pendingAuthenticationToken: string;
	/** The user must enrol a TOTP factor before authentication can complete; we
	 * need their id to call `enrollAuthFactor`, and email to label the factor. */
	userId: string;
	email?: string;
}

/**
 * Pull the discriminating error code out of a WorkOS error. The shape varies by
 * endpoint: `/authenticate` errors are OAuth-style (`OauthException`, code in
 * `.error`/`rawData.error`); validation errors like `createUser` are
 * `UnprocessableEntityException` (code on `.code`); others use `rawData.code`.
 * Check every place it might live.
 */
function authErrorCode(error: unknown): string | undefined {
	const e = error as
		{ error?: unknown; code?: unknown; rawData?: Record<string, unknown> } | null | undefined;
	const raw = e?.rawData;
	const candidate =
		(typeof e?.error === 'string' && e.error) ||
		(typeof e?.code === 'string' && e.code) ||
		raw?.error ||
		raw?.code;
	return typeof candidate === 'string' ? candidate : undefined;
}

/** If a WorkOS auth error means "verify your email first", return the pending token. */
export function emailVerificationRequired(error: unknown): PendingVerification | null {
	const raw = (error as { rawData?: Record<string, unknown> } | null | undefined)?.rawData;
	if (
		raw &&
		authErrorCode(error) === 'email_verification_required' &&
		typeof raw.pending_authentication_token === 'string'
	) {
		return {
			pendingAuthenticationToken: raw.pending_authentication_token,
			email: typeof raw.email === 'string' ? raw.email : undefined
		};
	}
	return null;
}

/** Pull the id of the user's TOTP factor out of a WorkOS mfa_challenge error. */
function totpFactorId(raw: Record<string, unknown>): string | undefined {
	const factors = Array.isArray(raw.authentication_factors) ? raw.authentication_factors : [];
	const isTotp = (f: unknown): f is { id: string } =>
		typeof f === 'object' &&
		f !== null &&
		(f as { type?: unknown }).type === 'totp' &&
		typeof (f as { id?: unknown }).id === 'string';
	return factors.find(isTotp)?.id;
}

/**
 * If a WorkOS auth error means "complete your TOTP challenge", return the
 * pending token and the factor to challenge. WorkOS returns the enrolled
 * `authentication_factors` (not a ready-made challenge), so the caller must
 * create the challenge via `mfa.challengeFactor` before `authenticateWithTotp`.
 */
export function mfaChallengeRequired(error: unknown): MfaChallenge | null {
	const raw = (error as { rawData?: Record<string, unknown> } | null | undefined)?.rawData;
	if (!raw || authErrorCode(error) !== 'mfa_challenge') return null;
	if (typeof raw.pending_authentication_token !== 'string') return null;
	const factorId = totpFactorId(raw);
	if (!factorId) return null;
	return { pendingAuthenticationToken: raw.pending_authentication_token, factorId };
}

/**
 * If a WorkOS auth error means "this user must ENROL in MFA before they can
 * finish authenticating" (the environment mandates MFA and the user has no
 * factor yet), return the pending token plus the user id/email needed to
 * enrol a TOTP factor. Distinct from `mfa_challenge`, where a factor exists.
 */
export function mfaEnrollmentRequired(error: unknown): MfaEnrollment | null {
	const raw = (error as { rawData?: Record<string, unknown> } | null | undefined)?.rawData;
	if (!raw || authErrorCode(error) !== 'mfa_enrollment') return null;
	if (typeof raw.pending_authentication_token !== 'string') return null;
	const user = raw.user as { id?: unknown; email?: unknown } | null | undefined;
	if (!user || typeof user.id !== 'string') return null;
	return {
		pendingAuthenticationToken: raw.pending_authentication_token,
		userId: user.id,
		email: typeof user.email === 'string' ? user.email : undefined
	};
}

/**
 * If a WorkOS auth error means "this user belongs to several organisations and
 * must choose one to finish authenticating", return the pending token. The caller
 * completes the sign-in for a specific org via `authenticateWithOrganizationSelection`.
 * `authenticateWithPassword` has no organizationId parameter, so this two-step flow is
 * the only way to sign a multi-org user into a specific org.
 */
export function organizationSelectionRequired(error: unknown): PendingVerification | null {
	const raw = (error as { rawData?: Record<string, unknown> } | null | undefined)?.rawData;
	if (!raw || authErrorCode(error) !== 'organization_selection_required') return null;
	if (typeof raw.pending_authentication_token !== 'string') return null;
	return { pendingAuthenticationToken: raw.pending_authentication_token };
}

/**
 * True when the pending authentication token has expired or been consumed.
 * The MFA challenge/enrolment step can't be retried with it — the user must
 * sign in again to mint a fresh token, so callers should restart the flow
 * rather than show a misleading "invalid code" error.
 */
export function pendingTokenInvalid(error: unknown): boolean {
	return authErrorCode(error) === 'invalid_pending_authentication_token';
}

/**
 * True when a failed `authenticateWithTotp` means the VENDOR REFUSED THE SUBMISSION — a wrong or
 * replayed code, a spent challenge — as opposed to "we could not ask". Only the former may be
 * reported to the user as "that code wasn't right".
 *
 * Every SDK exception carries the HTTP `status` (`GenericServerException` and its subclasses,
 * `UnauthorizedException`); a transport failure carries none. A 4xx is WorkOS answering about what
 * we sent, with two exceptions that are about US, not the code: 401 is a rejected API key, 429 is
 * our own quota. Those, every 5xx, and a request that never got an answer are all "unavailable".
 * The MFA actions used to blame the code for all of them, so a WorkOS blip or a bad key read as
 * "wrong code" to a person who had typed it correctly and would go on typing it correctly.
 */
export function codeRefused(error: unknown): boolean {
	const status = (error as { status?: unknown } | null | undefined)?.status;
	if (typeof status !== 'number') return false;
	return status >= 400 && status < 500 && status !== 401 && status !== 429;
}
