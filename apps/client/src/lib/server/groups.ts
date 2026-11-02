/** The signed-in client's groups, in the order a portal design is chosen. */

export type ClientGroup = {
	uid: string;
	name: string;
	slug: string;
};

const GROUPS_TTL_MS = 10_000;
const cache = new Map<string, { value: string[]; expires: number }>();

/**
 * Cached per access token for the same window as the book. Keyed by the contact too: a
 * multi-client login switches groups by switching who is on screen.
 *
 * A failed read is not cached, so the next navigation retries. An empty list is a real
 * answer (this person wears no group) and is cached.
 */
export async function fetchMyGroups(
	token: string | null,
	opts: { apiUrl: string; fetch: typeof fetch; now?: () => number; contactId?: number | null }
): Promise<string[]> {
	if (!token) return [];
	const now = opts.now ?? (() => Date.now());
	const contactId = opts.contactId ?? null;
	const key = contactId === null ? token : `${token}:${contactId}`;
	const cached = cache.get(key);
	if (cached && cached.expires > now()) return cached.value;
	const url =
		contactId === null
			? `${opts.apiUrl}/crm/groups/mine`
			: `${opts.apiUrl}/crm/groups/mine?contact_id=${contactId}`;
	try {
		const res = await opts.fetch(url, {
			headers: { Authorization: `Bearer ${token}` }
		});
		if (!res.ok) return [];
		const body = (await res.json()) as { groups?: ClientGroup[] | null };
		const value = (body.groups ?? [])
			.map((group) => group.slug)
			.filter((slug) => typeof slug === 'string' && slug.length > 0);
		sweep(now());
		cache.set(key, { value, expires: now() + GROUPS_TTL_MS });
		return value;
	} catch {
		return [];
	}
}

function sweep(now: number): void {
	for (const [key, entry] of cache) if (entry.expires <= now) cache.delete(key);
}

/** Test hook. */
export function clearGroupCache(): void {
	cache.clear();
}
