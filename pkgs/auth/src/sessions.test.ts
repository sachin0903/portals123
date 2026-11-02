/**
 * Session revocation.
 *
 * The gap this closes: a password change rotated the credential and left every session minted with
 * the OLD one alive, so an attacker holding a stolen sealed-session cookie kept their access — the
 * one thing the user was trying to stop. `revokeOtherSessions` is what the three security pages
 * call, and it has to be honest about what it managed: a UI that says "signed out everywhere" after
 * a failed sweep is worse than one that says nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WorkOS } from '@workos-inc/node';
import {
	listLiveSessions,
	loadStoredSessions,
	refreshStoredSessions,
	revokeOtherSessions,
	revokeOwnedSession,
	sessionIdFromAccessToken
} from './sessions';

type StubSession = {
	id: string;
	status?: string;
	ipAddress?: string | null;
	userAgent?: string | null;
	createdAt?: string | null;
};

/** A WorkOS stub shaped like @workos-inc/node 7.82: listSessions -> AutoPaginatable. */
function stub(
	sessions: StubSession[],
	opts: { listThrows?: boolean; revokeThrowsFor?: string[]; paginate?: boolean } = {}
) {
	const revokeSession = vi.fn(async ({ sessionId }: { sessionId: string }) => {
		if (opts.revokeThrowsFor?.includes(sessionId)) throw new Error('workos down');
	});
	const listSessions = vi.fn(async () => {
		if (opts.listThrows) throw new Error('workos down');
		return opts.paginate === false
			? { data: sessions }
			: { data: sessions.slice(0, 1), autoPagination: async () => sessions };
	});
	return {
		listSessions,
		revokeSession,
		workos: { userManagement: { listSessions, revokeSession } } as unknown as WorkOS
	};
}

const token = (claims: Record<string, unknown>) =>
	`header.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.signature`;

beforeEach(() => {
	vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('sessionIdFromAccessToken', () => {
	it('reads the sid, and gives up quietly on anything else', () => {
		expect(sessionIdFromAccessToken(token({ sid: 'session_1' }))).toBe('session_1');
		expect(sessionIdFromAccessToken(token({ sid: 42 }))).toBeUndefined();
		expect(sessionIdFromAccessToken(token({}))).toBeUndefined();
		expect(sessionIdFromAccessToken('not-a-jwt')).toBeUndefined();
		expect(sessionIdFromAccessToken(undefined)).toBeUndefined();
		expect(sessionIdFromAccessToken(null)).toBeUndefined();
	});
});

describe('revokeOtherSessions', () => {
	it('revokes every other live session and keeps the caller’s own', async () => {
		const s = stub([
			{ id: 'session_here', status: 'active' },
			{ id: 'session_laptop', status: 'active' },
			{ id: 'session_phone', status: 'active' }
		]);
		const sweep = await revokeOtherSessions(s.workos, {
			userId: 'user_1',
			keepSessionId: 'session_here'
		});
		expect(sweep).toEqual({ listed: true, revoked: 2, failed: 0, sweptCurrent: false });
		expect(s.revokeSession.mock.calls.map(([a]) => a.sessionId)).toEqual([
			'session_laptop',
			'session_phone'
		]);
		expect(s.revokeSession).not.toHaveBeenCalledWith({ sessionId: 'session_here' });
	});

	it('walks every page, not just the first', async () => {
		// The first page holds only one session; a `.data`-only implementation would leave the rest
		// of the user's sessions alive and still report success.
		const s = stub([
			{ id: 'session_here' },
			{ id: 'session_2' },
			{ id: 'session_3' },
			{ id: 'session_4' }
		]);
		const sweep = await revokeOtherSessions(s.workos, {
			userId: 'user_1',
			keepSessionId: 'session_here'
		});
		expect(sweep.revoked).toBe(3);
	});

	it('falls back to the first page when the client cannot paginate', async () => {
		const s = stub([{ id: 'session_a' }, { id: 'session_b' }], { paginate: false });
		expect((await revokeOtherSessions(s.workos, { userId: 'user_1' })).revoked).toBe(2);
	});

	it('skips sessions that are already expired or revoked', async () => {
		const s = stub([
			{ id: 'session_old', status: 'expired' },
			{ id: 'session_gone', status: 'revoked' },
			{ id: 'session_live', status: 'active' }
		]);
		const sweep = await revokeOtherSessions(s.workos, { userId: 'user_1', keepSessionId: 'x' });
		expect(sweep.revoked).toBe(1);
		expect(s.revokeSession).toHaveBeenCalledExactlyOnceWith({ sessionId: 'session_live' });
	});

	it('sweeps EVERYTHING, and says so, when the current session cannot be identified', async () => {
		// The tempting shortcut is to skip the sweep when `sid` is unreadable — that turns a security
		// guarantee off on a parsing accident. We revoke the lot instead and flag `sweptCurrent` so
		// the page can warn that the user will have to sign in again.
		const s = stub([{ id: 'session_a' }, { id: 'session_b' }]);
		const sweep = await revokeOtherSessions(s.workos, {
			userId: 'user_1',
			keepSessionId: sessionIdFromAccessToken('garbage')
		});
		expect(sweep).toEqual({ listed: true, revoked: 2, failed: 0, sweptCurrent: true });
	});

	it('never claims a revocation it did not achieve', async () => {
		const unreachable = stub([{ id: 'session_a' }], { listThrows: true });
		expect(await revokeOtherSessions(unreachable.workos, { userId: 'user_1' })).toEqual({
			listed: false,
			revoked: 0,
			failed: 0,
			sweptCurrent: false
		});
		expect(unreachable.revokeSession).not.toHaveBeenCalled();

		const partial = stub([{ id: 'session_a' }, { id: 'session_b' }], {
			revokeThrowsFor: ['session_b']
		});
		const sweep = await revokeOtherSessions(partial.workos, {
			userId: 'user_1',
			keepSessionId: 'session_here'
		});
		// One failure must not abort the rest of the sweep, and it must be reported.
		expect(sweep).toEqual({ listed: true, revoked: 1, failed: 1, sweptCurrent: false });
	});

	it('handles a user with no other sessions without pretending otherwise', async () => {
		const s = stub([{ id: 'session_here' }]);
		expect(
			await revokeOtherSessions(s.workos, { userId: 'user_1', keepSessionId: 'session_here' })
		).toEqual({ listed: true, revoked: 0, failed: 0, sweptCurrent: false });
	});
});

const CHROME_MAC =
	'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';

describe('listLiveSessions', () => {
	it('keeps IP, user agent and a device label, and marks the current browser', async () => {
		const s = stub([
			{
				id: 'session_old',
				status: 'active',
				ipAddress: '203.0.113.8',
				userAgent: CHROME_MAC,
				createdAt: '2026-01-01T00:00:00Z'
			},
			{
				id: 'session_here',
				status: 'active',
				ipAddress: '203.0.113.9',
				userAgent: CHROME_MAC,
				createdAt: '2026-02-01T00:00:00Z'
			},
			{ id: 'session_dead', status: 'revoked', ipAddress: '198.51.100.1', userAgent: CHROME_MAC }
		]);
		const listed = await listLiveSessions(s.workos, {
			userId: 'user_1',
			currentSessionId: 'session_here'
		});
		expect(listed.listed).toBe(true);
		expect(listed.sessions.map((row) => row.id)).toEqual(['session_here', 'session_old']);
		expect(listed.sessions[0]).toMatchObject({
			ipAddress: '203.0.113.9',
			userAgent: CHROME_MAC,
			device: 'Chrome on macOS',
			current: true
		});
	});

	it('reports an unlistable provider instead of an empty session list', async () => {
		const s = stub([], { listThrows: true });
		expect(await listLiveSessions(s.workos, { userId: 'user_1' })).toEqual({
			listed: false,
			sessions: []
		});
	});
});

describe('revokeOwnedSession', () => {
	it('revokes a session that is on the user’s own list', async () => {
		const s = stub([
			{ id: 'session_here', status: 'active' },
			{ id: 'session_laptop', status: 'active' }
		]);
		expect(
			await revokeOwnedSession(s.workos, {
				userId: 'user_1',
				sessionId: 'session_laptop',
				keepSessionId: 'session_here'
			})
		).toBe('revoked');
		expect(s.revokeSession).toHaveBeenCalledWith({ sessionId: 'session_laptop' });
	});

	it('refuses a session that is not the user’s, and refuses the current browser', async () => {
		const s = stub([{ id: 'session_here', status: 'active' }]);
		expect(
			await revokeOwnedSession(s.workos, {
				userId: 'user_1',
				sessionId: 'session_someone_else',
				keepSessionId: 'session_here'
			})
		).toBe('missing');
		expect(
			await revokeOwnedSession(s.workos, {
				userId: 'user_1',
				sessionId: 'session_here',
				keepSessionId: 'session_here'
			})
		).toBe('current');
		expect(s.revokeSession).not.toHaveBeenCalled();
	});
});

describe('stored sessions', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('does not call the network when the portal has no API configured', async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);
		expect(await loadStoredSessions(undefined, 'token')).toEqual({ listed: false, sessions: [] });
		expect(await loadStoredSessions('http://api.test', null)).toEqual({
			listed: false,
			sessions: []
		});
		await refreshStoredSessions(undefined, 'token');
		expect(fetchMock).not.toHaveBeenCalled();
	});

	it('reads the mirror and ignores a row with no id', async () => {
		const fetchMock = vi.fn(
			async () =>
				new Response(
					JSON.stringify({
						sessions: [
							{
								id: 'session_1',
								ip_address: '203.0.113.8',
								user_agent: 'Chrome',
								device: 'Chrome on macOS',
								created_at: '2026-10-01T00:00:00Z',
								expires_at: null,
								current: true
							},
							{ ip_address: 'nope' }
						]
					}),
					{ status: 200 }
				)
		);
		vi.stubGlobal('fetch', fetchMock);
		const result = await loadStoredSessions('http://api.test/', 'token');
		expect(fetchMock).toHaveBeenCalledWith(
			'http://api.test/me/sessions',
			expect.objectContaining({ headers: { Authorization: 'Bearer token' } })
		);
		expect(result).toEqual({
			listed: true,
			sessions: [
				{
					id: 'session_1',
					ip_address: '203.0.113.8',
					user_agent: 'Chrome',
					device: 'Chrome on macOS',
					created_at: '2026-10-01T00:00:00Z',
					expires_at: null,
					current: true
				}
			]
		});
	});

	it('treats a failed read as unread, and posts a refresh after a revoke', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response('no', { status: 503 }))
		);
		expect(await loadStoredSessions('http://api.test', 'token')).toEqual({
			listed: false,
			sessions: []
		});
		const refresh = vi.fn(async () => new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', refresh);
		await refreshStoredSessions('http://api.test', 'token');
		expect(refresh).toHaveBeenCalledWith(
			'http://api.test/me/sessions/refresh',
			expect.objectContaining({ method: 'POST' })
		);
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => {
				throw new Error('down');
			})
		);
		await expect(refreshStoredSessions('http://api.test', 'token')).resolves.toBeUndefined();
	});
});
