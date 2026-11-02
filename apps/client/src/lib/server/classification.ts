/** The signed-in client's resolved book label — design set + capabilities. */

export type ClientClassification = {
	uid: string;
	name: string;
	slug: string;
	capabilities: { money_movement?: boolean };
	source: 'pinned' | 'matched' | 'default';
};

/**
 * Cached per access token for the same short window `fetchMe` uses: the book a login is in
 * changes when a firm re-pins it, not per request, and this used to be paid on EVERY request —
 * each page, each bell poll, each proxy call — with no cache at all, alongside the `/me` lookup
 * that had one. A grant lands on the next navigation after the window, same as permissions.
 *
 * Entries are keyed by token, and a token is minted afresh on every WorkOS refresh, so the map
 * would grow without bound on a busy portal; expired entries are swept whenever it is written to.
 */
const CLASSIFICATION_TTL_MS = 10_000;
const cache = new Map<string, { value: ClientClassification | null; expires: number }>();

export async function fetchMyClassification(
	token: string | null,
	opts: { apiUrl: string; fetch: typeof fetch; now?: () => number; contactId?: number | null }
): Promise<ClientClassification | null> {
	if (!token) return null;
	const now = opts.now ?? (() => Date.now());
	// Keyed by the contact too: a multi-client login switches books by switching who is on
	// screen, and a cache that only knew the token would keep serving the previous person's
	// shopfront for the whole window.
	const contactId = opts.contactId ?? null;
	const key = contactId === null ? token : `${token}:${contactId}`;
	const cached = cache.get(key);
	if (cached && cached.expires > now()) return cached.value;
	const url =
		contactId === null
			? `${opts.apiUrl}/crm/classifications/mine`
			: `${opts.apiUrl}/crm/classifications/mine?contact_id=${contactId}`;
	try {
		const res = await opts.fetch(url, {
			headers: { Authorization: `Bearer ${token}` }
		});
		if (!res.ok) return null;
		const body = (await res.json()) as { classification?: ClientClassification | null };
		const value = body.classification ?? null;
		sweep(now());
		cache.set(key, { value, expires: now() + CLASSIFICATION_TTL_MS });
		return value;
	} catch {
		return null;
	}
}

function sweep(now: number): void {
	for (const [key, entry] of cache) if (entry.expires <= now) cache.delete(key);
}

/** Test hook. */
export function clearClassificationCache(): void {
	cache.clear();
}
