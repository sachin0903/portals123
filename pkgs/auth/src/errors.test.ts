import { describe, expect, it } from 'vitest';
import {
	codeRefused,
	emailVerificationRequired,
	mfaChallengeRequired,
	mfaEnrollmentRequired,
	organizationSelectionRequired,
	pendingTokenInvalid
} from './errors';

/**
 * The MFA actions answered "that code wasn't right" to EVERY failure, including the ones that say
 * nothing about the code. The SDK puts the HTTP status on every exception it throws
 * (`GenericServerException.status`, `UnauthorizedException.status = 401`), and a transport failure
 * has none — that is the whole signal.
 */
describe('codeRefused', () => {
	it('is true for the vendor refusing what was submitted (a 4xx about the code or token)', () => {
		// Transcribed from production: GenericServerException "Invalid one-time code".
		expect(codeRefused({ status: 400, rawData: { message: 'Invalid one-time code' } })).toBe(true);
		expect(codeRefused({ status: 422, code: 'authentication_challenge_expired' })).toBe(true);
		expect(codeRefused({ status: 403 })).toBe(true);
	});

	it('is false when the failure is about us, not the code', () => {
		expect(codeRefused({ status: 401, name: 'UnauthorizedException' })).toBe(false); // bad key
		expect(codeRefused({ status: 429, name: 'RateLimitExceededException' })).toBe(false);
		expect(codeRefused({ status: 500 })).toBe(false);
		expect(codeRefused({ status: 503 })).toBe(false);
	});

	it('is false for a request that never got an answer', () => {
		expect(codeRefused(new TypeError('fetch failed'))).toBe(false);
		expect(codeRefused({ rawData: { code: 'invalid_one_time_code' } })).toBe(false);
		expect(codeRefused(null)).toBe(false);
	});
});

describe('pendingTokenInvalid', () => {
	it('is true for an expired/invalid pending authentication token', () => {
		const error = {
			status: 400,
			rawData: {
				code: 'invalid_pending_authentication_token',
				message: "The pending_authentication_token 'x' has expired or is invalid."
			}
		};
		expect(pendingTokenInvalid(error)).toBe(true);
	});

	it('is false for an invalid one-time code and other errors', () => {
		expect(pendingTokenInvalid({ rawData: { code: 'invalid_one_time_code' } })).toBe(false);
		expect(pendingTokenInvalid({ rawData: { code: 'mfa_challenge' } })).toBe(false);
		expect(pendingTokenInvalid(null)).toBe(false);
	});
});

/**
 * The classifier that gates the whole two-step org-selection flow had no test, and the flow it
 * gates is unreachable without it — a user in several orgs simply cannot sign in. This shape is
 * transcribed from a real 403 thrown by `authenticateWithPassword` for an account holding Client
 * Portal, Platform and Internal Operations memberships at once.
 */
describe('organizationSelectionRequired', () => {
	const realError = {
		status: 403,
		rawData: {
			code: 'organization_selection_required',
			message: 'The user must choose an organization to finish their authentication.',
			pending_authentication_token: '5T4c5t0SemkOPzp5GhcmBIKWm',
			user: { object: 'user', id: 'user_01H', email: 'someone@example.com' },
			organizations: [{ id: 'org_1' }, { id: 'org_2' }, { id: 'org_3' }]
		}
	};

	it('extracts the pending token from a real multi-org rejection', () => {
		expect(organizationSelectionRequired(realError)).toEqual({
			pendingAuthenticationToken: '5T4c5t0SemkOPzp5GhcmBIKWm'
		});
	});

	it('is null for other auth errors and for nothing at all', () => {
		expect(organizationSelectionRequired({ rawData: { code: 'mfa_challenge' } })).toBeNull();
		expect(
			organizationSelectionRequired({ rawData: { code: 'email_verification_required' } })
		).toBeNull();
		expect(organizationSelectionRequired(null)).toBeNull();
		expect(organizationSelectionRequired(undefined)).toBeNull();
		expect(organizationSelectionRequired(new Error('boom'))).toBeNull();
	});

	/** Without a token the second leg cannot be attempted, so treat it as unhandled rather than
	 *  returning a half-built challenge the caller would then fail on. */
	it('is null when the code matches but no pending token came back', () => {
		expect(
			organizationSelectionRequired({
				rawData: { code: 'organization_selection_required', organizations: [{ id: 'org_1' }] }
			})
		).toBeNull();
	});
});

/**
 * Shapes mirror what the WorkOS Node SDK actually throws from
 * `authenticateWithPassword`: a GenericServerException whose `rawData` is the
 * raw error body. The MFA body carries `authentication_factors` (a list) and a
 * `pending_authentication_token` — crucially, NOT an `authentication_challenge_id`.
 */
function mfaChallengeError(factors: unknown[], token = 'pending_tok_123') {
	return {
		rawData: {
			code: 'mfa_challenge',
			message: 'The user must complete an MFA challenge to finish authenticating.',
			pending_authentication_token: token,
			authentication_factors: factors
		}
	};
}

describe('mfaChallengeRequired', () => {
	it('extracts the pending token and the TOTP factor id', () => {
		const result = mfaChallengeRequired(
			mfaChallengeError([{ type: 'totp', id: 'auth_factor_totp' }])
		);
		expect(result).toEqual({
			pendingAuthenticationToken: 'pending_tok_123',
			factorId: 'auth_factor_totp'
		});
	});

	it('picks the TOTP factor when several factor types are present', () => {
		const result = mfaChallengeRequired(
			mfaChallengeError([
				{ type: 'sms', id: 'auth_factor_sms' },
				{ type: 'totp', id: 'auth_factor_totp' }
			])
		);
		expect(result?.factorId).toBe('auth_factor_totp');
	});

	it('also detects the OAuth-style error where the code sits in `error`', () => {
		// The refresh-token path throws an OauthException; the discriminator is in
		// `.error` (and `rawData.error`) rather than `rawData.code`.
		const result = mfaChallengeRequired({
			error: 'mfa_challenge',
			rawData: {
				error: 'mfa_challenge',
				pending_authentication_token: 'pending_tok_456',
				authentication_factors: [{ type: 'totp', id: 'auth_factor_totp' }]
			}
		});
		expect(result).toEqual({
			pendingAuthenticationToken: 'pending_tok_456',
			factorId: 'auth_factor_totp'
		});
	});

	it('returns null when no TOTP factor is present', () => {
		expect(
			mfaChallengeRequired(mfaChallengeError([{ type: 'sms', id: 'auth_factor_sms' }]))
		).toBeNull();
		expect(mfaChallengeRequired(mfaChallengeError([]))).toBeNull();
	});

	it('returns null for an unrelated auth error', () => {
		expect(
			mfaChallengeRequired({ rawData: { code: 'invalid_credentials', message: 'bad password' } })
		).toBeNull();
	});

	it('returns null for non-error inputs', () => {
		expect(mfaChallengeRequired(null)).toBeNull();
		expect(mfaChallengeRequired(undefined)).toBeNull();
		expect(mfaChallengeRequired(new Error('boom'))).toBeNull();
	});
});

describe('mfaEnrollmentRequired', () => {
	// The real WorkOS mfa_enrollment body: a pending token + the (factorless) user.
	function enrollmentError(token = 'pending_enrol_123') {
		return {
			status: 403,
			rawData: {
				code: 'mfa_enrollment',
				message: 'The user must enroll in MFA to finish authenticating.',
				pending_authentication_token: token,
				user: { object: 'user', id: 'user_01ABC', email: 'gio@grapheneplatforms.com' }
			}
		};
	}

	it('extracts the pending token, user id and email', () => {
		expect(mfaEnrollmentRequired(enrollmentError())).toEqual({
			pendingAuthenticationToken: 'pending_enrol_123',
			userId: 'user_01ABC',
			email: 'gio@grapheneplatforms.com'
		});
	});

	it('omits the email when absent', () => {
		const result = mfaEnrollmentRequired({
			rawData: {
				code: 'mfa_enrollment',
				pending_authentication_token: 'tok',
				user: { id: 'user_01ABC' }
			}
		});
		expect(result).toEqual({
			pendingAuthenticationToken: 'tok',
			userId: 'user_01ABC',
			email: undefined
		});
	});

	it('returns null when the user id is missing', () => {
		expect(
			mfaEnrollmentRequired({
				rawData: { code: 'mfa_enrollment', pending_authentication_token: 'tok', user: {} }
			})
		).toBeNull();
	});

	it('returns null for an mfa_challenge error and non-error inputs', () => {
		expect(mfaEnrollmentRequired(mfaChallengeError([{ type: 'totp', id: 'f' }]))).toBeNull();
		expect(mfaEnrollmentRequired(null)).toBeNull();
		expect(mfaEnrollmentRequired(undefined)).toBeNull();
	});
});

describe('emailVerificationRequired', () => {
	it('extracts the pending token and email', () => {
		const result = emailVerificationRequired({
			rawData: {
				code: 'email_verification_required',
				pending_authentication_token: 'pending_tok_789',
				email: 'gio@grapheneplatforms.com'
			}
		});
		expect(result).toEqual({
			pendingAuthenticationToken: 'pending_tok_789',
			email: 'gio@grapheneplatforms.com'
		});
	});

	it('omits the email when absent', () => {
		const result = emailVerificationRequired({
			rawData: { code: 'email_verification_required', pending_authentication_token: 'tok' }
		});
		expect(result).toEqual({ pendingAuthenticationToken: 'tok', email: undefined });
	});

	it('detects the OAuth-style error where the code sits in `error`', () => {
		const result = emailVerificationRequired({
			error: 'email_verification_required',
			rawData: { error: 'email_verification_required', pending_authentication_token: 'tok' }
		});
		expect(result?.pendingAuthenticationToken).toBe('tok');
	});

	it('returns null for an mfa_challenge error', () => {
		expect(emailVerificationRequired(mfaChallengeError([{ type: 'totp', id: 'f' }]))).toBeNull();
	});

	it('returns null for non-error inputs', () => {
		expect(emailVerificationRequired(null)).toBeNull();
		expect(emailVerificationRequired(undefined)).toBeNull();
	});
});
