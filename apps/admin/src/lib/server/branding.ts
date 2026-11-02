import { env } from '$env/dynamic/private';
import type { Brand } from '@graphene/ui';

/**
 * The admin shell's brand — the Graphene (operations) tenant's own logo set, read from
 * `GET /internal/admin-branding`.
 *
 * The admin portal has no host row in `auth.tenant_environments` (only client/platform hosts
 * exist), so it cannot ride the portals' `resolveEnvironment` host lookup; this is the minimal
 * counterpart, same shape: token-guarded internal endpoint, 60s in-process cache (positive AND
 * 404-negative), transient failures NOT cached so a blip never pins the fallback for the TTL.
 *
 * Deliberately ONLY logos/icon/name — the admin surface keeps the product look (no tenant
 * colour theming), so the endpoint serves nothing else and this maps nothing else. `null` ⇒
 * the shells render the built-in Graphene mark, exactly as before this existed.
 */

interface AdminBrandingPayload {
	app_name: string | null;
	logo_url: string | null;
	logo_dark_url: string | null;
	icon_url: string | null;
	logo_variant: 'icon' | 'wordmark' | null;
}

interface CacheEntry {
	value: Brand | null;
	expires: number;
}

let cache: CacheEntry | null = null;

/** Clear the cache (tests, or after the Graphene branding is edited). */
export function clearAdminBrandCache(): void {
	cache = null;
}

function toBrand(raw: AdminBrandingPayload): Brand | null {
	const brand: Brand = {
		name: raw.app_name ?? undefined,
		logoUrl: raw.logo_url ?? undefined,
		logoDarkUrl: raw.logo_dark_url ?? undefined,
		iconUrl: raw.icon_url ?? undefined,
		logoVariant: raw.logo_variant ?? undefined
	};
	// All-unset branding (a fresh instance) is indistinguishable from "no brand" — return null so
	// callers and the shells stay on the static default without a half-empty Brand in the way.
	return brand.logoUrl || brand.logoDarkUrl || brand.iconUrl || brand.name ? brand : null;
}

/**
 * Fetch (with the cache) the Graphene logo set, `null` when unset/unknown/unreachable.
 * Framework-agnostic core so tests can inject `apiUrl`/`internalToken`/`fetch`.
 */
export async function fetchAdminBrand(opts: {
	apiUrl: string;
	internalToken: string;
	fetch?: typeof fetch;
	cacheTtlMs?: number;
	/** Skip a still-fresh cache entry, then write the new result back — branding-editor
	 * navigations so a logo save is on the next paint instead of waiting out the TTL. */
	bypassCache?: boolean;
}): Promise<Brand | null> {
	if (!opts.apiUrl || !opts.internalToken) return null;

	const now = Date.now();
	if (!opts.bypassCache && cache && cache.expires > now) return cache.value;

	const doFetch = opts.fetch ?? fetch;
	const ttl = opts.cacheTtlMs ?? 60_000;
	try {
		const res = await doFetch(`${opts.apiUrl}/internal/admin-branding`, {
			headers: { 'x-internal-token': opts.internalToken }
		});
		if (res.ok) {
			const value = toBrand((await res.json()) as AdminBrandingPayload);
			cache = { value, expires: now + ttl };
			return value;
		}
		if (res.status === 404) {
			// Core environment not registered (or the token gate said "not found"): negative-cache
			// so an unconfigured instance doesn't ask again on every request.
			cache = { value: null, expires: now + ttl };
			return null;
		}
		// Any other status is transient: do not cache, fall back for THIS request only.
		return null;
	} catch {
		return null; // network blip: same — uncached fallback
	}
}

/** The per-request entry point: reads the env like `resolveAuth` does and defers to the core. */
export async function resolveAdminBrand(
	fetch: typeof globalThis.fetch,
	opts?: { bypassCache?: boolean }
): Promise<Brand | null> {
	return fetchAdminBrand({
		apiUrl: env.API_URL ?? '',
		internalToken: env.INTERNAL_API_TOKEN ?? '',
		fetch,
		bypassCache: opts?.bypassCache
	});
}
