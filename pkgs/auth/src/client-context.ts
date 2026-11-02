/**
 * The browser a sign-in actually came from.
 *
 * Password auth runs server-to-server, so WorkOS records a null IP and user agent unless the
 * portal forwards them on the call that mints the session. There is no device-type field on a
 * WorkOS session — the short label is derived here from the user agent.
 */

export interface SignInClient {
	ipAddress?: string;
	userAgent?: string;
}

const MAX_IP = 64;
const MAX_USER_AGENT = 256;

/** Control characters are header-splitting material. A scan, not a regex: a control-character
 * class trips `no-control-regex` (same reason as `return-to.ts`). */
function stripControls(value: string): string {
	let out = '';
	for (const ch of value) {
		const code = ch.codePointAt(0) ?? 0;
		if (code < 0x20 || code === 0x7f) continue;
		out += ch;
	}
	return out;
}

function clean(value: string | null | undefined, max: number): string | undefined {
	if (!value) return undefined;
	const trimmed = stripControls(value).trim();
	if (!trimmed) return undefined;
	return trimmed.length > max ? trimmed.slice(0, max) : trimmed;
}

/** Normalise an IP and user agent before they are sent to WorkOS or the audit trail. */
export function signInClient(input: {
	ipAddress?: string | null;
	userAgent?: string | null;
}): SignInClient {
	const ipAddress = clean(input.ipAddress, MAX_IP);
	const userAgent = clean(input.userAgent, MAX_USER_AGENT);
	return {
		...(ipAddress ? { ipAddress } : {}),
		...(userAgent ? { userAgent } : {})
	};
}

/**
 * Read the browser off a portal request. A missing client address must not fail the sign-in —
 * the fields are optional on WorkOS's side, and a misconfigured address header is not a wrong
 * password.
 */
export function readSignInClient(
	request: { headers: { get(name: string): string | null } },
	address: () => string
): SignInClient {
	let ipAddress: string | null = null;
	try {
		ipAddress = address();
	} catch {
		ipAddress = null;
	}
	return signInClient({ ipAddress, userAgent: request.headers.get('user-agent') });
}

/** The fields WorkOS's authenticate calls accept. Absent when we have nothing to forward. */
export function workosClientFields(client: SignInClient | undefined): {
	ipAddress?: string;
	userAgent?: string;
} {
	if (!client) return {};
	return {
		...(client.ipAddress ? { ipAddress: client.ipAddress } : {}),
		...(client.userAgent ? { userAgent: client.userAgent } : {})
	};
}

/** "Chrome on macOS". Null when there is no user agent to read. */
export function deviceLabel(userAgent: string | null | undefined): string | null {
	if (!userAgent) return null;
	const ua = userAgent;
	let browser: string | null = null;
	if (/Edg\//.test(ua)) browser = 'Edge';
	else if (/OPR\/|Opera/.test(ua)) browser = 'Opera';
	else if (/Firefox\//.test(ua)) browser = 'Firefox';
	else if (/Chrome\//.test(ua)) browser = 'Chrome';
	else if (/Safari\//.test(ua)) browser = 'Safari';
	let os: string | null = null;
	if (/iPhone|iPad|iPod/.test(ua)) os = 'iOS';
	else if (/Android/.test(ua)) os = 'Android';
	else if (/Mac OS X|Macintosh/.test(ua)) os = 'macOS';
	else if (/Windows/.test(ua)) os = 'Windows';
	else if (/CrOS/.test(ua)) os = 'ChromeOS';
	else if (/Linux/.test(ua)) os = 'Linux';
	if (browser && os) return `${browser} on ${os}`;
	return browser ?? os;
}

/** A session row the portals render. Only the current browser is filled in here. */
export interface CurrentSessionFacts {
	current: boolean;
	ip_address: string | null;
	user_agent: string | null;
	device: string | null;
}

/**
 * Fill a blank current-browser row from the request that is viewing the page.
 *
 * WorkOS only stores IP and user agent when they were sent on the call that minted the session.
 * A session from before that still comes back empty, but the browser marked current is this
 * request, so its address and user agent are known here. Other rows are left as WorkOS returned
 * them — we do not have their browser.
 */
export function fillCurrentBrowser<T extends CurrentSessionFacts>(
	sessions: T[],
	client: SignInClient | undefined
): T[] {
	if (!client?.ipAddress && !client?.userAgent) return sessions;
	return sessions.map((session) => {
		if (!session.current) return session;
		const userAgent = session.user_agent || client.userAgent || null;
		const ipAddress = session.ip_address || client.ipAddress || null;
		const device = session.device || deviceLabel(userAgent);
		if (
			userAgent === session.user_agent &&
			ipAddress === session.ip_address &&
			device === session.device
		) {
			return session;
		}
		return { ...session, user_agent: userAgent, ip_address: ipAddress, device };
	});
}
