/**
 * The signed-in caller's current permissions and portal, read from the API rather than the
 * access token.
 *
 * WorkOS's AuthKit used to embed a role's permissions straight into the JWT, and both `platform`
 * and `admin` decoded them client-side — but the registry grew large enough that doing so pushed
 * some roles' sealed session cookies past the browser's ~4096-byte ceiling. The API's
 * `get_current_principal` now reads permissions fresh from the DB on every request instead
 * (`auth.member_permissions`, the actual source of truth — see docs/identity-and-access.md §4),
 * and `GET /me` returns them together with the portal this session resolves to; this is the
 * portal-side half, so the three apps' `hooks.server.ts` share one implementation instead of
 * copies that would silently drift.
 *
 * No caching across users: the cache is keyed by the access token. A short TTL means a permission
 * grant or revoke still lands on the next navigation after that window, while a burst of
 * client-side loads (hover-preload + layout + page) does not each pay GET /me.
 *
 * The TTL is the trade between those two. The hook awaits this call on EVERY request before the
 * layout can run, so on a warm session it sits on the critical path to the first byte of HTML; a
 * reader moving between pages a few seconds apart was paying it each time at 10s. 20s covers a
 * normal run of navigations and still bounds a revoke to well under a minute. The API remains the
 * authority — this answer gates NAVIGATION (which links to show, which portal to admit); every
 * API call re-reads the grants from the DB regardless.
 */

export const PERMISSIONS_TTL_MS = 20_000;

export interface MeSnapshot {
	permissions: string[];
	/**
	 * Confirmed portal for this session (`client` / `platform` / `operations`), or `null` when
	 * the API says there is no active membership for the application this token was minted
	 * through. `undefined` means we never got an answer (no token, transport failure, old API)
	 * — layouts must fail OPEN in that case, the same way they used to when `GET /me/portal`
	 * itself 500'd.
	 */
	portal: string | null | undefined;
}

const meCache = new Map<string, { value: MeSnapshot; expires: number }>();

export interface FetchPermissionsOptions {
	/** Base URL of the API (e.g. http://127.0.0.1:8000). */
	apiUrl: string;
	/** Injected fetch (defaults to global). */
	fetch?: typeof fetch;
}

const EMPTY_ME: MeSnapshot = { permissions: [], portal: undefined };

function parseMe(body: { permissions?: unknown; portal?: unknown }): MeSnapshot {
	const permissions = Array.isArray(body.permissions)
		? body.permissions.filter((p): p is string => typeof p === 'string')
		: [];
	const portal = body.portal === null || typeof body.portal === 'string' ? body.portal : undefined;
	return { permissions, portal };
}

/**
 * Fails closed on permissions (`[]`) and fail-open on portal (`undefined`) on anything short of
 * a clean 200 — no access token, a network error, a non-OK response, or a malformed body. The
 * shell must not guess a portal (that would bounce everyone on an API blip) and must not guess
 * grants (that would render pages the caller cannot use).
 */
export async function fetchMe(
	accessToken: string | null,
	opts: FetchPermissionsOptions
): Promise<MeSnapshot> {
	if (!accessToken || !opts.apiUrl) return EMPTY_ME;
	const cached = meCache.get(accessToken);
	if (cached && cached.expires > Date.now()) return cached.value;
	const doFetch = opts.fetch ?? fetch;
	try {
		const res = await doFetch(`${opts.apiUrl}/me`, {
			headers: { Authorization: `Bearer ${accessToken}` }
		});
		if (!res.ok) return EMPTY_ME;
		const value = parseMe((await res.json()) as { permissions?: unknown; portal?: unknown });
		// Keyed by token, and every WorkOS refresh mints a new one, so without a sweep the map only
		// ever grows. Expired entries go whenever a fresh one is written.
		const now = Date.now();
		for (const [key, entry] of meCache) if (entry.expires <= now) meCache.delete(key);
		meCache.set(accessToken, { value, expires: now + PERMISSIONS_TTL_MS });
		return value;
	} catch {
		return EMPTY_ME;
	}
}

/**
 * Fails closed to `[]` on anything short of a clean 200 with a `permissions: string[]` body.
 * Thin wrapper over {@link fetchMe} for callers that only need the grant set.
 */
export async function fetchPermissions(
	accessToken: string | null,
	opts: FetchPermissionsOptions
): Promise<string[]> {
	return (await fetchMe(accessToken, opts)).permissions;
}

/** Test hook — production callers never need this. */
export function clearPermissionsCache(): void {
	meCache.clear();
}
