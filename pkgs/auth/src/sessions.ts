import type { WorkOS } from '@workos-inc/node';
import { deviceLabel } from './client-context';

/**
 * The `sid` inside a WorkOS access token. Read WITHOUT verification on purpose: it is a token the
 * provider just handed us over TLS, and it is only ever used to decide which session to *keep*
 * (or to revoke a session we ourselves created) — never to authorise anything.
 */
export function sessionIdFromAccessToken(
	accessToken: string | undefined | null
): string | undefined {
	const payload = accessToken?.split('.')[1];
	if (!payload) return undefined;
	try {
		const b64 = payload.replace(/-/g, '+').replace(/_/g, '/');
		const claims = JSON.parse(atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4))) as {
			sid?: unknown;
		};
		return typeof claims.sid === 'string' ? claims.sid : undefined;
	} catch {
		return undefined;
	}
}

/**
 * What a session sweep actually managed to do. Deliberately not a boolean: the UI must be able to
 * say "3 other sessions signed out" or "we could not check your other sessions" rather than claim
 * a revocation that never happened.
 */
export interface SessionSweep {
	/** Did we manage to enumerate the user's sessions at all? False ⇒ nothing is known. */
	listed: boolean;
	/** Sessions successfully revoked. */
	revoked: number;
	/** Sessions we tried and failed to revoke (they are still live). */
	failed: number;
	/** True when no `keepSessionId` was resolvable, so the caller's OWN session was swept too. */
	sweptCurrent: boolean;
}

/** A live session that still needs revoking. */
function isLive(status: unknown): boolean {
	// Absent status ⇒ assume live (fail towards revoking, which is the safe direction here).
	return status === undefined || status === null || status === 'active';
}

/**
 * End every other live WorkOS session for one user.
 *
 * Why this exists: changing a password used to leave every session minted with the OLD password
 * alive, so an attacker holding a stolen sealed-session cookie kept their access indefinitely —
 * the one thing a user changing their password is trying to stop.
 *
 * `keepSessionId` is the session making the request (from the access token's `sid`), so the user
 * is not signed out of the tab they are standing in. When it cannot be resolved we sweep
 * EVERYTHING and say so via `sweptCurrent`: the security guarantee comes first, and the honest
 * consequence ("you will need to sign in again") is something the caller can surface. Never
 * silently skip the sweep — that would be the guarantee failing quietly.
 */
export async function revokeOtherSessions(
	workos: WorkOS,
	{ userId, keepSessionId }: { userId: string; keepSessionId?: string }
): Promise<SessionSweep> {
	const nothing: SessionSweep = {
		listed: false,
		revoked: 0,
		failed: 0,
		sweptCurrent: false
	};
	let sessions: { id?: string; status?: unknown }[];
	try {
		const listed = await listAllSessions(workos, userId);
		if (!listed) return nothing;
		sessions = listed;
	} catch (e) {
		console.error('[auth] could not list the user’s sessions; none were revoked:', e);
		return nothing;
	}

	let revoked = 0;
	let failed = 0;
	for (const session of sessions) {
		const sessionId = session?.id;
		if (!sessionId || !isLive(session.status)) continue;
		if (keepSessionId && sessionId === keepSessionId) continue;
		try {
			await workos.userManagement.revokeSession({ sessionId });
			revoked += 1;
		} catch (e) {
			failed += 1;
			console.error('[auth] could not revoke session %s:', sessionId, e);
		}
	}
	return { listed: true, revoked, failed, sweptCurrent: !keepSessionId };
}

type RawSession = {
	id?: string;
	status?: unknown;
	ipAddress?: string | null;
	userAgent?: string | null;
	authMethod?: unknown;
	createdAt?: string | null;
	expiresAt?: string | null;
};

/** Every session WorkOS returns for the user, or null when the list itself failed. */
async function listAllSessions(workos: WorkOS, userId: string): Promise<RawSession[] | null> {
	try {
		// `listSessions(userId)` -> AutoPaginatable<Session> (@workos-inc/node): `.data` is the
		// first page, `.autoPagination()` walks the rest. Read both defensively so a stub client
		// still works.
		const page = await workos.userManagement.listSessions(userId);
		const sessions =
			typeof page?.autoPagination === 'function' ? await page.autoPagination() : (page?.data ?? []);
		return sessions as RawSession[];
	} catch (e) {
		console.error('[auth] could not list the user’s sessions:', e);
		return null;
	}
}

function asText(value: unknown): string | null {
	if (typeof value === 'string' && value) return value;
	if (value && typeof value === 'object' && 'value' in value) {
		const inner = (value as { value?: unknown }).value;
		return typeof inner === 'string' && inner ? inner : null;
	}
	return null;
}

/** One live WorkOS session, with the device label derived from the user agent. */
export interface LiveSession {
	id: string;
	ipAddress: string | null;
	userAgent: string | null;
	device: string | null;
	authMethod: string | null;
	createdAt: string | null;
	expiresAt: string | null;
	current: boolean;
}

/**
 * The user's live sessions, read straight from WorkOS.
 *
 * Profile and security pages read the local mirror instead (`loadStoredSessions`). This
 * remains the direct read: the password sweep still has to enumerate WorkOS, because a
 * session the mirror has not seen must not stay signed in. `listed: false` means the
 * provider could not be asked, which is different from an empty list.
 */
export async function listLiveSessions(
	workos: WorkOS,
	{ userId, currentSessionId }: { userId: string; currentSessionId?: string }
): Promise<{ listed: boolean; sessions: LiveSession[] }> {
	const raw = await listAllSessions(workos, userId);
	if (!raw) return { listed: false, sessions: [] };
	const sessions = raw.flatMap((item) => {
		const id = item?.id;
		if (!id || !isLive(item.status)) return [];
		const userAgent = asText(item.userAgent);
		const row: LiveSession = {
			id,
			ipAddress: asText(item.ipAddress),
			userAgent,
			device: deviceLabel(userAgent),
			authMethod: asText(item.authMethod),
			createdAt: asText(item.createdAt),
			expiresAt: asText(item.expiresAt),
			current: Boolean(currentSessionId && id === currentSessionId)
		};
		return [row];
	});
	sessions.sort((a, b) => {
		if (a.current !== b.current) return a.current ? -1 : 1;
		return (b.createdAt ?? '').localeCompare(a.createdAt ?? '');
	});
	return { listed: true, sessions };
}

/**
 * End one of the user's own live sessions.
 *
 * The session id is checked against that user's list first. WorkOS revokes by id alone, so a
 * guessed id belonging to someone else must never be sent. The caller's own session is refused
 * here — ending this browser is sign-out, not a row on this list.
 */
export async function revokeOwnedSession(
	workos: WorkOS,
	{
		userId,
		sessionId,
		keepSessionId
	}: { userId: string; sessionId: string; keepSessionId?: string }
): Promise<'revoked' | 'current' | 'missing' | 'unavailable'> {
	if (!sessionId) return 'missing';
	if (keepSessionId && sessionId === keepSessionId) return 'current';
	const raw = await listAllSessions(workos, userId);
	if (!raw) return 'unavailable';
	const owned = raw.some((item) => item?.id === sessionId && isLive(item.status));
	if (!owned) return 'missing';
	try {
		await workos.userManagement.revokeSession({ sessionId });
		return 'revoked';
	} catch (e) {
		console.error('[auth] could not revoke session:', e);
		return 'unavailable';
	}
}

/** One stored session, in the shape the API returns and the session list renders. */
export interface StoredSession {
	id: string;
	ip_address: string | null;
	user_agent: string | null;
	device: string | null;
	created_at: string | null;
	expires_at: string | null;
	current: boolean;
}

function storedSession(value: unknown): StoredSession | null {
	if (!value || typeof value !== 'object') return null;
	const row = value as Record<string, unknown>;
	if (typeof row.id !== 'string' || !row.id) return null;
	const text = (item: unknown) => (typeof item === 'string' && item ? item : null);
	return {
		id: row.id,
		ip_address: text(row.ip_address),
		user_agent: text(row.user_agent),
		device: text(row.device),
		created_at: text(row.created_at),
		expires_at: text(row.expires_at),
		current: row.current === true
	};
}

/**
 * The caller's stored sessions. No API base (the unit tests, or a portal with no
 * backend configured) is an empty unread list — it does not fall back to WorkOS.
 * `listed: false` means the mirror could not be read.
 */
export async function loadStoredSessions(
	apiBase: string | null | undefined,
	accessToken: string | null | undefined
): Promise<{ listed: boolean; sessions: StoredSession[] }> {
	if (!apiBase || !accessToken) return { listed: false, sessions: [] };
	try {
		const res = await fetch(`${apiBase.replace(/\/$/, '')}/me/sessions`, {
			headers: { Authorization: `Bearer ${accessToken}` },
			signal: AbortSignal.timeout(8_000)
		});
		if (!res.ok) return { listed: false, sessions: [] };
		const body = (await res.json()) as { sessions?: unknown };
		const sessions = Array.isArray(body.sessions)
			? body.sessions.flatMap((row) => {
					const session = storedSession(row);
					return session ? [session] : [];
				})
			: [];
		return { listed: true, sessions };
	} catch {
		return { listed: false, sessions: [] };
	}
}

/**
 * Ask the API to copy this caller's sessions from WorkOS after a revoke.
 *
 * Best-effort. The poller applies `session.revoked` on its own; this only closes the
 * gap before the next paint. A missing API base does nothing.
 */
export async function refreshStoredSessions(
	apiBase: string | null | undefined,
	accessToken: string | null | undefined
): Promise<void> {
	if (!apiBase || !accessToken) return;
	try {
		await fetch(`${apiBase.replace(/\/$/, '')}/me/sessions/refresh`, {
			method: 'POST',
			headers: { Authorization: `Bearer ${accessToken}` },
			signal: AbortSignal.timeout(8_000)
		});
	} catch {
		// The events poller catches up. A failed refresh must not fail the revoke.
	}
}
