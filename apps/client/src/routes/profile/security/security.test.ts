/**
 * The change-password action on the security page.
 *
 * This form used to be a stub: it validated the three fields and returned
 * `{ passwordChanged: true }` without ever calling WorkOS, so staff and Graphene ops were
 * told they had rotated a credential that was unchanged. The first test below fails against
 * that version. Driven through the real `passwordAuth` with a stub WorkOS client, so the
 * re-authentication step is exercised rather than mocked away.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MIN_PASSWORD_LENGTH, WEAK_PASSWORD_MESSAGE } from '@graphene/auth';
import { actions, load } from './+page.server';

/** The access token this request carries. `sid` is what identifies the session to KEEP. */
const CURRENT_SESSION = 'session_here';
const accessToken = (claims: Record<string, unknown> = { sid: CURRENT_SESSION }) =>
	`header.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.signature`;

function stubWorkOS(
	overrides: {
		authenticateWithPassword?: () => Promise<unknown>;
		updateUser?: (args: unknown) => Promise<unknown>;
		/** Live sessions the user holds, as WorkOS would report them. */
		sessions?: { id: string; status?: string }[];
		listSessionsThrows?: boolean;
	} = {}
) {
	const updateUser = vi.fn(overrides.updateUser ?? (async () => ({ id: 'user_1' })));
	const authenticateWithPassword = vi.fn(
		overrides.authenticateWithPassword ?? (async () => ({ sealedSession: 'sealed' }))
	);
	const sessions = overrides.sessions ?? [
		{ id: CURRENT_SESSION, status: 'active' },
		{ id: 'session_stolen', status: 'active' }
	];
	// Recorded in order: which sessions were revoked matters as much as how many.
	const revokedIds: string[] = [];
	const revokeSession = vi.fn(async (payload: { sessionId: string }) => {
		revokedIds.push(payload.sessionId);
	});
	const listSessions = vi.fn(async () => {
		if (overrides.listSessionsThrows) throw new Error('workos unreachable');
		return { data: sessions, autoPagination: async () => sessions };
	});
	return {
		updateUser,
		authenticateWithPassword,
		listSessions,
		revokeSession,
		revokedIds,
		workos: {
			userManagement: { updateUser, authenticateWithPassword, listSessions, revokeSession }
		}
	};
}

function event(fields: Record<string, string>, workos: unknown, token = accessToken()) {
	const body = new URLSearchParams(fields);
	return {
		request: new Request('http://portal.test/account/security?/password', {
			method: 'POST',
			body
		}),
		url: new URL('http://portal.test/account/security'),
		locals: {
			user: { id: 'user_1', email: 'staff@firm.test' },
			accessToken: token,
			// A 32+ character cookie password, otherwise passwordAuth short-circuits
			// to 'misconfigured' before it reaches WorkOS.
			auth: {
				workos,
				clientId: 'client_test',
				cookiePassword: 'unit-test-cookie-password-32-chars'
			}
		}
	};
}

/** The sweep result a successful change reports when the stub holds one other live session. */
const SWEPT_ONE = { listed: true, revoked: 1, failed: 0, sweptCurrent: false };

const GOOD = {
	current: 'the old passphrase',
	next: 'a brand new passphrase',
	confirm: 'a brand new passphrase'
};

// The action signature is SvelteKit's RequestEvent; the stub carries only what it reads.
type ActionArg = Parameters<typeof actions.password>[0];

// A successful sweep asks the API to copy sessions from WorkOS. Dev points API_URL at a
// live server, and that fetch outlives the test.
beforeEach(() => {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(null, { status: 204 }))
	);
});

describe('change password', () => {
	it('actually calls WorkOS and only then reports success', async () => {
		const stub = stubWorkOS();
		const result = await actions.password(event(GOOD, stub.workos) as unknown as ActionArg);
		expect(stub.authenticateWithPassword).toHaveBeenCalledOnce();
		expect(stub.updateUser).toHaveBeenCalledWith({
			userId: 'user_1',
			password: 'a brand new passphrase'
		});
		expect(result).toEqual({ passwordChanged: true, sessions: SWEPT_ONE });
	});

	it('rejects a wrong current password without touching the credential', async () => {
		const stub = stubWorkOS({
			authenticateWithPassword: async () => {
				throw new Error('invalid credentials');
			}
		});
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const result = (await actions.password(event(GOOD, stub.workos) as unknown as ActionArg)) as {
			status: number;
			data: { pwError: string };
		};
		expect(result.status).toBe(401);
		expect(result.data.pwError).toBe('Your current password is incorrect.');
		expect(stub.updateUser).not.toHaveBeenCalled();
	});

	it('rejects a weak new password locally, before the round trip', async () => {
		const stub = stubWorkOS();
		const result = (await actions.password(
			event(
				{ current: 'old passphrase', next: 'short', confirm: 'short' },
				stub.workos
			) as unknown as ActionArg
		)) as { status: number; data: { pwError: string } };
		expect(result.status).toBe(400);
		expect(result.data.pwError).toContain('at least 10 characters');
		expect(stub.authenticateWithPassword).not.toHaveBeenCalled();
		expect(stub.updateUser).not.toHaveBeenCalled();
	});

	it('rejects mismatched confirmation and an unchanged password', async () => {
		const stub = stubWorkOS();
		const mismatch = (await actions.password(
			event({ ...GOOD, confirm: 'something else' }, stub.workos) as unknown as ActionArg
		)) as { status: number; data: { pwError: string } };
		expect(mismatch.data.pwError).toBe('New passwords do not match.');

		const same = (await actions.password(
			event(
				{
					current: 'the old passphrase',
					next: 'the old passphrase',
					confirm: 'the old passphrase'
				},
				stub.workos
			) as unknown as ActionArg
		)) as { status: number; data: { pwError: string } };
		expect(same.data.pwError).toBe('Your new password must be different from the current one.');
		expect(stub.updateUser).not.toHaveBeenCalled();
	});

	it('surfaces a provider password rejection as an actionable 400, never as success', async () => {
		const stub = stubWorkOS({
			updateUser: async () => {
				throw Object.assign(new Error('password is too weak'), {
					rawData: { code: 'password_too_weak' }
				});
			}
		});
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const result = (await actions.password(event(GOOD, stub.workos) as unknown as ActionArg)) as {
			status: number;
			data: { pwError: string };
		};
		expect(result.status).toBe(400);
		expect(result.data.pwError).toBe(WEAK_PASSWORD_MESSAGE);
	});
});

describe('the stated password rule', () => {
	type LoadArg = Parameters<typeof load>[0];

	const loadEvent = () =>
		({
			locals: {
				user: { id: 'user_1', email: 'staff@firm.test' },
				auth: { workos: { multiFactorAuth: { listUserAuthFactors: async () => ({ data: [] }) } } }
			},
			url: new URL('http://portal.test/account/security'),
			request: { headers: { get: () => null } },
			getClientAddress: () => '203.0.113.8'
		}) as unknown as LoadArg;

	// The drift the audit caught: the form advertised `minlength=8` and "At least 8 characters"
	// while the action refused anything under 10, so a 9-character password passed the browser and
	// was rejected after a round trip. The page now renders the load's own values, which come from
	// the same constants the action validates against.
	it('is the rule the action actually enforces', async () => {
		const data = (await load(loadEvent())) as {
			minPasswordLength: number;
			passwordHint: string;
		};
		expect(data.minPasswordLength).toBe(MIN_PASSWORD_LENGTH);
		expect(data.passwordHint).toContain(`at least ${MIN_PASSWORD_LENGTH} characters`);
		expect(data.passwordHint).not.toMatch(/\b8 characters\b/);
	});

	it('refuses the password the old caption said was fine', async () => {
		const stub = stubWorkOS();
		const nine = 'a'.repeat(MIN_PASSWORD_LENGTH - 1);
		const result = (await actions.password(
			event(
				{ current: 'the old passphrase', next: nine, confirm: nine },
				stub.workos
			) as unknown as ActionArg
		)) as { status: number; data: { pwError: string } };
		expect(result.status).toBe(400);
		expect(result.data.pwError).toContain(`at least ${MIN_PASSWORD_LENGTH} characters`);
		expect(stub.updateUser).not.toHaveBeenCalled();
	});
});

describe('step-up re-authentication', () => {
	// Every change-password / 2FA-disable attempt used to go through `passwordAuth`, which always
	// asks WorkOS to seal a session — so confirming your own password minted a live session that was
	// then thrown away. The check is now session-free, and revokes what WorkOS creates anyway.
	function reauthStub() {
		const revokeSession = vi.fn(async () => {});
		// `sid` in the access-token payload is what identifies the session to revoke.
		const payload = Buffer.from(JSON.stringify({ sid: 'session_9' })).toString('base64url');
		const authenticateWithPassword = vi.fn(async () => ({
			accessToken: `header.${payload}.signature`
		}));
		const updateUser = vi.fn(async () => ({ id: 'user_1' }));
		return {
			revokeSession,
			authenticateWithPassword,
			updateUser,
			workos: {
				userManagement: { authenticateWithPassword, revokeSession, updateUser }
			}
		};
	}

	it('does not seal a session, and revokes the one WorkOS creates', async () => {
		const stub = reauthStub();
		const result = (await actions.password(event(GOOD, stub.workos) as unknown as ActionArg)) as {
			passwordChanged: boolean;
		};
		expect(result.passwordChanged).toBe(true);
		const [payload] = stub.authenticateWithPassword.mock.calls[0] as unknown as [
			Record<string, unknown>
		];
		expect(payload).not.toHaveProperty('session');
		expect(stub.revokeSession).toHaveBeenCalledWith({ sessionId: 'session_9' });
	});
});

describe('the user’s other sessions', () => {
	// The hole this closes: a password change rotated the credential and left every session minted
	// with the OLD one alive, so an attacker holding a stolen sealed-session cookie kept their
	// access indefinitely — the one thing changing a password is meant to stop. Every assertion
	// below fails against the version that only called updateUser.
	type SweepResult = {
		passwordChanged?: boolean;
		signedOutOthers?: boolean;
		sessions?: { listed: boolean; revoked: number; failed: number; sweptCurrent: boolean };
	};

	it('signs the old credential’s sessions out, keeping the tab the user is in', async () => {
		const stub = stubWorkOS({
			sessions: [
				{ id: CURRENT_SESSION, status: 'active' },
				{ id: 'session_stolen', status: 'active' },
				{ id: 'session_phone', status: 'active' }
			]
		});
		const result = (await actions.password(
			event(GOOD, stub.workos) as unknown as ActionArg
		)) as SweepResult;
		expect(stub.listSessions).toHaveBeenCalledWith('user_1');
		expect(stub.revokedIds).toEqual(['session_stolen', 'session_phone']);
		expect(stub.revokedIds).not.toContain(CURRENT_SESSION);
		expect(result.sessions).toEqual({
			listed: true,
			revoked: 2,
			failed: 0,
			sweptCurrent: false
		});
	});

	it('revokes nothing when the password change itself failed', async () => {
		const stub = stubWorkOS({
			updateUser: async () => {
				throw new Error('nope');
			}
		});
		vi.spyOn(console, 'error').mockImplementation(() => {});
		await actions.password(event(GOOD, stub.workos) as unknown as ActionArg);
		expect(stub.revokeSession).not.toHaveBeenCalled();
	});

	it('reports honestly when the provider cannot be reached, rather than implying success', async () => {
		const stub = stubWorkOS({ listSessionsThrows: true });
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const result = (await actions.password(
			event(GOOD, stub.workos) as unknown as ActionArg
		)) as SweepResult;
		// The password DID change, so this is still a success — but the sweep is reported as unknown.
		expect(result.passwordChanged).toBe(true);
		expect(result.sessions).toEqual({
			listed: false,
			revoked: 0,
			failed: 0,
			sweptCurrent: false
		});
	});

	it('sweeps everything, and flags it, when the current session cannot be identified', async () => {
		// Skipping the sweep on an unreadable token would turn the guarantee off by accident.
		const stub = stubWorkOS();
		const result = (await actions.password(
			event(GOOD, stub.workos, 'not-a-jwt') as unknown as ActionArg
		)) as SweepResult;
		expect(result.sessions).toEqual({ listed: true, revoked: 2, failed: 0, sweptCurrent: true });
	});

	it('offers the same sweep as its own control, without a password change', async () => {
		const stub = stubWorkOS();
		const result = (await actions.signOutOthers(
			event({}, stub.workos) as unknown as Parameters<typeof actions.signOutOthers>[0]
		)) as SweepResult;
		expect(result.signedOutOthers).toBe(true);
		expect(result.sessions).toEqual(SWEPT_ONE);
		expect(stub.updateUser).not.toHaveBeenCalled();
		expect(stub.authenticateWithPassword).not.toHaveBeenCalled();
	});

	it('fails the control loudly when the sweep could not run at all', async () => {
		const stub = stubWorkOS({ listSessionsThrows: true });
		vi.spyOn(console, 'error').mockImplementation(() => {});
		const result = (await actions.signOutOthers(
			event({}, stub.workos) as unknown as Parameters<typeof actions.signOutOthers>[0]
		)) as { status: number; data: { sessionsError: string } };
		expect(result.status).toBe(502);
		expect(result.data.sessionsError).toContain('We couldn’t sign out your other devices.');
	});
});
