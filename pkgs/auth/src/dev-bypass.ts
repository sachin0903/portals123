/**
 * The portals' half of the `dev:` bypass — the `dev-auth` cookie the E2E suite and local dev use
 * to drive authenticated pages without a WorkOS login.
 *
 * ONE implementation, shared by every portal's `hooks.server.ts`, mirroring the API's
 * `_dev_bypass_allowed` (`apps/api/app/auth.py`) guard for guard. Each portal used to carry its
 * own copy with TWO of the four checks — the flag and `APP_STAGE !== 'production'` — while the
 * hook's own comment called it "the same double guard the API enforces". It was not: an
 * `APP_STAGE` of `staging`, `prod` or `Dev` passed, no peer check was made, and the cookie beat a
 * real sealed session. On a host with the flag on, any browser could mint `locals.user`.
 *
 * Four guards, all of which must hold:
 *  1. the explicit flag, `DEMO_AUTH_BYPASS=1`;
 *  2. a stage from an ALLOWLIST — `dev`, `local`, `test` — not "anything but production";
 *  3. a loopback peer;
 *  4. a request that does not look proxied: no forwarding header, and a `Host` naming loopback.
 *
 * As the API's docstring says, these are heuristics about the shape of the request, not proof of
 * the caller. The real protections are that the flag defaults off and the stage must be a
 * development one; the peer/proxy checks exist so a copied dev environment file cannot on its own
 * open a hole, and so a deployment behind a proxy fails closed.
 */

const DEV_BYPASS_STAGES = new Set(['dev', 'local', 'test']);
const LOOPBACK_HOSTS = new Set(['127.0.0.1', '::1', 'localhost', '::ffff:127.0.0.1']);
const FORWARDING_HEADERS = ['x-forwarded-for', 'x-forwarded-host', 'x-real-ip', 'forwarded'];

export interface DevPrincipal {
	sub: string;
	email?: string;
	first_name?: string;
	last_name?: string;
	org_id?: string;
	role?: string;
	permissions?: string[];
}

export interface DevBypassRequest {
	/** `DEMO_AUTH_BYPASS` as the environment spells it. */
	flag: string | undefined;
	/** `APP_STAGE`; absent reads as production (the safe side). */
	stage: string | undefined;
	/** The peer address, or a thrower — SvelteKit's `getClientAddress()` throws on adapters that
	 * cannot supply one, and "cannot tell" is "not loopback". */
	clientAddress: () => string;
	headers: Headers;
}

/** The bare hostname the caller asked for (`Host` minus any port), lowercased. */
export function requestHostname(host: string | null): string {
	const value = ((host ?? '').split(',')[0] ?? '').trim().toLowerCase();
	if (value.startsWith('['))
		return value.includes(']') ? value.slice(1, value.indexOf(']')) : value;
	return value.includes(':') ? value.slice(0, value.lastIndexOf(':')) : value;
}

/**
 * Whether an unverified `dev:` principal may be accepted for this request, and why not if not.
 * The reason is for a log line, never for a response body.
 */
export function devBypassAllowed(req: DevBypassRequest): { allowed: boolean; reason: string } {
	if (req.flag !== '1') return { allowed: false, reason: 'DEMO_AUTH_BYPASS is off' };
	const stage = req.stage ?? 'production';
	if (!DEV_BYPASS_STAGES.has(stage)) {
		return {
			allowed: false,
			reason: `APP_STAGE=${JSON.stringify(stage)} is not a development stage`
		};
	}
	let peer: string;
	try {
		peer = req.clientAddress();
	} catch {
		return { allowed: false, reason: 'the caller address is not available' };
	}
	if (!LOOPBACK_HOSTS.has(peer))
		return { allowed: false, reason: `caller ${peer} is not loopback` };
	const present = FORWARDING_HEADERS.filter((h) => req.headers.has(h));
	if (present.length) {
		return { allowed: false, reason: `proxied request (headers: ${present.join(', ')})` };
	}
	const hostname = requestHostname(req.headers.get('host'));
	if (!LOOPBACK_HOSTS.has(hostname)) {
		return { allowed: false, reason: `request Host ${JSON.stringify(hostname)} is not loopback` };
	}
	return { allowed: true, reason: '' };
}

/**
 * The principal a `dev-auth` cookie carries, or null when the bypass is not allowed for this
 * request or the token is not a well-formed `dev:<base64url-json>` with a string `sub`.
 */
export function devPrincipal(
	token: string | undefined,
	req: DevBypassRequest
): DevPrincipal | null {
	if (!token?.startsWith('dev:')) return null;
	if (!devBypassAllowed(req).allowed) return null;
	try {
		const parsed: unknown = JSON.parse(Buffer.from(token.slice(4), 'base64url').toString('utf8'));
		if (!parsed || typeof parsed !== 'object') return null;
		const sub = (parsed as { sub?: unknown }).sub;
		if (typeof sub !== 'string' || !sub) return null;
		return parsed as DevPrincipal;
	} catch {
		return null;
	}
}
