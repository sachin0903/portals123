import { afterEach, describe, expect, it, vi } from 'vitest';
import type { WorkOS } from '@workos-inc/node';
import {
	COOKIE_PASSWORD_MIN_LENGTH,
	WEAK_PASSWORD_MESSAGE,
	acceptInvitationAndSetPassword,
	confirmPasswordReset,
	isValidCookiePassword,
	passwordAuth,
	verifyPassword,
	type PasswordAuthConfig
} from './auth';

const config: PasswordAuthConfig = { clientId: 'client_test', cookiePassword: 'pw'.repeat(16) };

/** A stub WorkOS whose authenticate* methods run the given impls. */
function stubWorkOS(impl: () => Promise<unknown>, orgSelImpl?: () => Promise<unknown>): WorkOS {
	return {
		userManagement: {
			authenticateWithPassword: vi.fn(impl),
			authenticateWithOrganizationSelection: vi.fn(orgSelImpl ?? (async () => ({})))
		}
	} as unknown as WorkOS;
}

function organizationSelectionError() {
	return {
		status: 403,
		rawData: {
			code: 'organization_selection_required',
			pending_authentication_token: 'org_sel_tok',
			organizations: [{ id: 'org_a' }, { id: 'org_b' }]
		}
	};
}

/** The real WorkOS mfa_challenge error shape (GenericServerException.rawData). */
function mfaChallengeError() {
	return {
		rawData: {
			code: 'mfa_challenge',
			pending_authentication_token: 'pending_tok',
			authentication_factors: [{ type: 'totp', id: 'auth_factor_totp' }]
		}
	};
}

function emailVerificationError() {
	return {
		rawData: {
			code: 'email_verification_required',
			pending_authentication_token: 'verify_tok'
		}
	};
}

function mfaEnrollmentError() {
	return {
		status: 403,
		rawData: {
			code: 'mfa_enrollment',
			pending_authentication_token: 'enrol_tok',
			user: { id: 'user_01ABC', email: 'a@b.com' }
		}
	};
}

afterEach(() => vi.restoreAllMocks());

describe('isValidCookiePassword', () => {
	it(`accepts a password of at least ${COOKIE_PASSWORD_MIN_LENGTH} characters`, () => {
		expect(isValidCookiePassword('x'.repeat(COOKIE_PASSWORD_MIN_LENGTH))).toBe(true);
		expect(isValidCookiePassword('x'.repeat(48))).toBe(true);
	});

	it('rejects a short, empty, or missing password', () => {
		expect(isValidCookiePassword('x'.repeat(COOKIE_PASSWORD_MIN_LENGTH - 1))).toBe(false);
		expect(isValidCookiePassword('set in Coolify')).toBe(false);
		expect(isValidCookiePassword('')).toBe(false);
		expect(isValidCookiePassword(undefined)).toBe(false);
		expect(isValidCookiePassword(null)).toBe(false);
	});
});

describe('passwordAuth', () => {
	it('returns the sealed session on success', async () => {
		const workos = stubWorkOS(async () => ({ sealedSession: 'sealed_abc' }));
		expect(await passwordAuth(workos, config, 'a@b.com', 'pw')).toEqual({
			status: 'authenticated',
			sealedSession: 'sealed_abc'
		});
	});

	it('forwards the browser IP and user agent on the password grant and on org selection', async () => {
		const workos = stubWorkOS(
			async () => {
				throw organizationSelectionError();
			},
			async () => ({ sealedSession: 'sealed_ops' })
		);
		const client = { ipAddress: '203.0.113.8', userAgent: 'Chrome/120.0' };
		await passwordAuth(workos, { ...config, organizationId: 'org_b' }, 'a@b.com', 'pw', client);
		expect(workos.userManagement.authenticateWithPassword).toHaveBeenCalledWith(
			expect.objectContaining({ ipAddress: '203.0.113.8', userAgent: 'Chrome/120.0' })
		);
		expect(workos.userManagement.authenticateWithOrganizationSelection).toHaveBeenCalledWith(
			expect.objectContaining({ ipAddress: '203.0.113.8', userAgent: 'Chrome/120.0' })
		);
	});

	it('completes org selection into the configured org for a multi-org user', async () => {
		const workos = stubWorkOS(
			async () => {
				throw organizationSelectionError();
			},
			async () => ({ sealedSession: 'sealed_ops' })
		);
		const cfg: PasswordAuthConfig = { ...config, organizationId: 'org_b' };
		expect(await passwordAuth(workos, cfg, 'a@b.com', 'pw')).toEqual({
			status: 'authenticated',
			sealedSession: 'sealed_ops'
		});
		expect(workos.userManagement.authenticateWithOrganizationSelection).toHaveBeenCalledWith(
			expect.objectContaining({
				organizationId: 'org_b',
				pendingAuthenticationToken: 'org_sel_tok'
			})
		);
	});

	it('fails org selection when no organization is configured', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const workos = stubWorkOS(async () => {
			throw organizationSelectionError();
		});
		expect(await passwordAuth(workos, config, 'a@b.com', 'pw')).toEqual({ status: 'failed' });
	});

	it('returns misconfigured (without calling WorkOS) when the application client id is missing', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const auth = vi.fn(async () => ({ sealedSession: 'nope' }));
		const workos = { userManagement: { authenticateWithPassword: auth } } as unknown as WorkOS;
		const badConfig: PasswordAuthConfig = {
			clientId: '',
			cookiePassword: 'x'.repeat(32)
		};
		expect(await passwordAuth(workos, badConfig, 'a@b.com', 'pw')).toEqual({
			status: 'misconfigured'
		});
		expect(auth).not.toHaveBeenCalled();
	});

	it('returns misconfigured (without calling WorkOS) when the cookie password is too short', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const auth = vi.fn(async () => ({ sealedSession: 'nope' }));
		const workos = { userManagement: { authenticateWithPassword: auth } } as unknown as WorkOS;
		const badConfig: PasswordAuthConfig = { clientId: 'client_test', cookiePassword: 'too-short' };
		expect(await passwordAuth(workos, badConfig, 'a@b.com', 'pw')).toEqual({
			status: 'misconfigured'
		});
		expect(auth).not.toHaveBeenCalled();
	});

	it('reports failure when WorkOS returns no sealed session', async () => {
		const workos = stubWorkOS(async () => ({}));
		expect(await passwordAuth(workos, config, 'a@b.com', 'pw')).toEqual({ status: 'failed' });
	});

	it('maps an mfa_challenge error to the mfa outcome with the factor id', async () => {
		const workos = stubWorkOS(async () => {
			throw mfaChallengeError();
		});
		expect(await passwordAuth(workos, config, 'a@b.com', 'pw')).toEqual({
			status: 'mfa',
			challenge: { pendingAuthenticationToken: 'pending_tok', factorId: 'auth_factor_totp' }
		});
	});

	it('maps an mfa_enrollment error to the enroll outcome with user id + email', async () => {
		const workos = stubWorkOS(async () => {
			throw mfaEnrollmentError();
		});
		expect(await passwordAuth(workos, config, 'a@b.com', 'pw')).toEqual({
			status: 'enroll',
			enrollment: {
				pendingAuthenticationToken: 'enrol_tok',
				userId: 'user_01ABC',
				email: 'a@b.com'
			}
		});
	});

	it('maps an email_verification_required error to the verify outcome', async () => {
		const workos = stubWorkOS(async () => {
			throw emailVerificationError();
		});
		expect(await passwordAuth(workos, config, 'a@b.com', 'pw')).toEqual({
			status: 'verify',
			pendingAuthenticationToken: 'verify_tok'
		});
	});

	it('treats an unrecognised error as a failure', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const workos = stubWorkOS(async () => {
			throw new Error('network down');
		});
		expect(await passwordAuth(workos, config, 'a@b.com', 'pw')).toEqual({ status: 'failed' });
	});
});

describe('confirmPasswordReset', () => {
	function stubReset(impl: () => Promise<unknown>): WorkOS {
		return { userManagement: { resetPassword: vi.fn(impl) } } as unknown as WorkOS;
	}

	it('reports success', async () => {
		expect(
			await confirmPasswordReset(
				stubReset(async () => ({})),
				'tok',
				'a long passphrase'
			)
		).toEqual({
			status: 'ok'
		});
	});

	it('surfaces the reset user so the portal can push onboarding completion', async () => {
		// The bug: the set-password page calls notifyOnboardingComplete(res.email ?? '') and bails on
		// an empty email, so `POST /internal/onboarding-complete` never fired and someone who HAD
		// onboarded still showed as never-invited until an operator pressed "Sync from WorkOS".
		const workos = stubReset(async () => ({
			user: { id: 'user_42', email: 'jo@firm.example' }
		}));
		expect(await confirmPasswordReset(workos, 'tok', 'a long passphrase')).toEqual({
			status: 'ok',
			email: 'jo@firm.example',
			userId: 'user_42'
		});
	});

	it('classifies an expired/consumed token so the user is told to request a new link', async () => {
		const workos = stubReset(async () => {
			throw { rawData: { code: 'password_reset_token_expired', message: 'Token has expired' } };
		});
		expect(await confirmPasswordReset(workos, 'tok', 'a long passphrase')).toEqual({
			status: 'invalid'
		});
	});

	it('says WHY in our own words, never the provider’s message', async () => {
		// This used to echo `rawData.message` straight into a destructive alert. It cannot: for the
		// 422 a rejected password actually uses, the SDK REPLACES the message with a list of machine
		// codes ("The following requirement must be met:\n\tpassword_too_weak"), so the echo would
		// print those at the user. `password-errors.ts` maps the code to copy instead, and
		// `password-errors.test.ts` pins that against doubles shaped like the real exceptions.
		const workos = stubReset(async () => {
			throw { rawData: { code: 'password_too_weak', message: 'Password found in a breach.' } };
		});
		const res = await confirmPasswordReset(workos, 'tok', 'hunter2hunter');
		expect(res.status).toBe('weak');
		expect(res).not.toHaveProperty('message', 'Password found in a breach.');
		expect((res as { message: string }).message).toMatch(/breach|guess/i);
	});

	it('reaches the weak branch on a REAL 422, which has no rawData at all', async () => {
		// The regression this whole change exists for: `UnprocessableEntityException` keeps its code
		// on the instance, the classifier read only `rawData`, and every rejected password on the
		// set-password page came back as the generic "Could not set your password."
		const workos = stubReset(async () => {
			const e = new Error('Unprocessable entity') as Error & { status: number; code: string };
			e.name = 'UnprocessableEntityException';
			e.status = 422;
			e.code = 'password_too_weak';
			throw e;
		});
		expect((await confirmPasswordReset(workos, 'tok', 'hunter2hunter')).status).toBe('weak');
	});

	it('falls back to an ACTIONABLE message, not "choose a stronger password"', async () => {
		const workos = stubReset(async () => {
			throw { rawData: { code: 'password_too_weak' } };
		});
		const res = await confirmPasswordReset(workos, 'tok', 'hunter2hunter');
		expect(res).toEqual({ status: 'weak', message: WEAK_PASSWORD_MESSAGE });
		// It must say *why* and what to do instead — and must NOT name the breach list, which is a
		// per-project checkbox that may be off. Claiming it is a reason we cannot support.
		expect(WEAK_PASSWORD_MESSAGE).toMatch(/guess/i);
		expect(WEAK_PASSWORD_MESSAGE).toMatch(/passphrase/i);
		expect(WEAK_PASSWORD_MESSAGE).not.toMatch(/breach/i);
	});
});

describe('acceptInvitationAndSetPassword', () => {
	it('reports a rejected password rather than throwing', async () => {
		// THE 500. Wiring the policy-divergence check, this branch read `password.length` where the
		// parameter is `newPassword` — an unresolved identifier, so the action threw a ReferenceError
		// and SvelteKit rendered "Internal Error" instead of the message. It only fired on the
		// INVITATION path (the reset path had the name right) and only inside the catch, so it
		// needed a rejected password on an emailed invite to show at all — which is exactly what
		// somebody testing the new copy does. `pkgs/auth` runs no typecheck, and vitest strips types,
		// so nothing in this package could have caught it.
		const { methods, workos } = stubInvitation();
		methods.resetPassword = vi.fn(async () => {
			const e = new Error('Unprocessable entity') as Error & { status: number; code: string };
			e.name = 'UnprocessableEntityException';
			e.status = 422;
			e.code = 'password_too_weak';
			throw e;
		});
		const res = await acceptInvitationAndSetPassword(workos, 'invite_42', 'a long passphrase');
		expect(res.status).toBe('weak');
		expect((res as { message: string }).message).toMatch(/guess/i);
	});

	function stubInvitation(overrides: Record<string, unknown> = {}) {
		const methods = {
			findInvitationByToken: vi.fn(async () => ({
				id: 'inv_42',
				email: 'jo@firm.example',
				state: 'pending',
				...overrides
			})),
			createPasswordReset: vi.fn(async () => ({ passwordResetToken: 'reset_42' })),
			resetPassword: vi.fn(async () => ({
				user: { id: 'user_42', email: 'jo@firm.example' }
			})),
			acceptInvitation: vi.fn(async () => ({ state: 'accepted' }))
		};
		return { methods, workos: { userManagement: methods } as unknown as WorkOS };
	}

	it('mints the short reset token only at submission, then accepts the invitation', async () => {
		const { methods, workos } = stubInvitation();
		expect(await acceptInvitationAndSetPassword(workos, 'invite_42', 'a long passphrase')).toEqual({
			status: 'ok',
			email: 'jo@firm.example',
			userId: 'user_42'
		});
		expect(methods.createPasswordReset).toHaveBeenCalledWith({ email: 'jo@firm.example' });
		expect(methods.resetPassword).toHaveBeenCalledWith({
			token: 'reset_42',
			newPassword: 'a long passphrase'
		});
		expect(methods.acceptInvitation).toHaveBeenCalledWith('inv_42');
	});

	it('does not mint a password credential for a revoked invitation', async () => {
		const { methods, workos } = stubInvitation({ state: 'revoked' });
		expect(await acceptInvitationAndSetPassword(workos, 'invite_42', 'a long passphrase')).toEqual({
			status: 'invalid'
		});
		expect(methods.createPasswordReset).not.toHaveBeenCalled();
	});
});

describe('verifyPassword (step-up re-authentication)', () => {
	/** A token whose payload carries `sid` — the session id we must revoke. */
	function accessToken(sid: string): string {
		const payload = Buffer.from(JSON.stringify({ sid })).toString('base64url');
		return `header.${payload}.signature`;
	}

	function stubReauth(impl: () => Promise<unknown>) {
		const revokeSession = vi.fn(async () => {});
		const authenticateWithPassword = vi.fn(impl);
		return {
			revokeSession,
			authenticateWithPassword,
			workos: {
				userManagement: { authenticateWithPassword, revokeSession }
			} as unknown as WorkOS
		};
	}

	it('never asks WorkOS to seal a session, and revokes the one it creates', async () => {
		// The defect: change-password and 2FA-disable went through `passwordAuth`, which always
		// passes `session: { sealSession: true, … }`, so every step-up check minted a real session
		// that was then dropped on the floor — on three surfaces, with no revocation anywhere.
		const stub = stubReauth(async () => ({ accessToken: accessToken('session_9') }));
		expect(await verifyPassword(stub.workos, { clientId: 'client_test' }, 'a@b.com', 'pw')).toBe(
			'ok'
		);
		const [payload] = stub.authenticateWithPassword.mock.calls[0] as unknown as [
			Record<string, unknown>
		];
		expect(payload).not.toHaveProperty('session');
		expect(stub.revokeSession).toHaveBeenCalledWith({ sessionId: 'session_9' });
	});

	it('needs no cookie password (it seals nothing)', async () => {
		const stub = stubReauth(async () => ({ accessToken: accessToken('session_1') }));
		// `passwordAuth` returns 'misconfigured' without a 32-char cookie password; this path has no
		// such failure mode, so a step-up check cannot break on session config.
		await expect(
			verifyPassword(stub.workos, { clientId: 'client_test' }, 'a@b.com', 'pw')
		).resolves.toBe('ok');
	});

	it('treats "another step required" as confirmation of the password', async () => {
		for (const rawData of [
			{
				code: 'mfa_challenge',
				pending_authentication_token: 'tok',
				authentication_factors: [{ type: 'totp', id: 'factor_1' }]
			},
			{ code: 'mfa_enrollment', pending_authentication_token: 'tok', user: { id: 'user_1' } },
			{ code: 'email_verification_required', pending_authentication_token: 'tok' },
			{ code: 'organization_selection_required', pending_authentication_token: 'tok' }
		]) {
			const stub = stubReauth(async () => {
				throw { rawData };
			});
			expect(await verifyPassword(stub.workos, { clientId: 'client_test' }, 'a@b.com', 'pw')).toBe(
				'ok'
			);
			// Nothing was minted, so there is nothing to revoke.
			expect(stub.revokeSession).not.toHaveBeenCalled();
		}
	});

	it('reports a wrong password without revoking anything', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const stub = stubReauth(async () => {
			throw { status: 401, rawData: { code: 'invalid_credentials' } };
		});
		expect(await verifyPassword(stub.workos, { clientId: 'client_test' }, 'a@b.com', 'nope')).toBe(
			'wrong'
		);
		expect(stub.revokeSession).not.toHaveBeenCalled();
	});

	it('succeeds even if the revoke fails — the check has already passed', async () => {
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const revokeSession = vi.fn(async () => {
			throw new Error('WorkOS unavailable');
		});
		const workos = {
			userManagement: {
				authenticateWithPassword: vi.fn(async () => ({ accessToken: accessToken('session_3') })),
				revokeSession
			}
		} as unknown as WorkOS;
		expect(await verifyPassword(workos, { clientId: 'client_test' }, 'a@b.com', 'pw')).toBe('ok');
		expect(revokeSession).toHaveBeenCalledOnce();
	});
});
