import { apiUrl, authHeaders } from '$lib/api';

/**
 * The numeric tenant id for a route's `uid`.
 *
 * The admin tenant section is addressed by UID (`/tenants/ten_1743d0…`) so a URL never leaks a
 * serial key and stays stable, but the ~40 endpoints under `/admin/tenants/{tenant_id}` keep
 * their integer contract — the numeric id is the internal key everywhere else, and re-typing
 * forty routes is a bigger change than one lookup.
 *
 * **Prefer `parent()` in a `load`.** The `[uid]` layout already resolves the tenant, so a page
 * load should read `(await parent()).tenant.id` rather than call this and pay a second round
 * trip. This exists for the two places that have no parent to ask: form ACTIONS (which run
 * outside the load tree) and `+server.ts` endpoints.
 */
export type ResolvedTenant =
	{ ok: true; id: number; uid: string } | { ok: false; status: number; error: string };

export async function resolveTenantId(
	fetch: typeof globalThis.fetch,
	locals: App.Locals,
	uid: string
): Promise<ResolvedTenant> {
	const res = await fetch(`${apiUrl()}/admin/tenants/by-uid/${encodeURIComponent(uid)}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) {
		return {
			ok: false,
			// A 4xx is the API's answer; anything else is us failing to reach it.
			status: res.status >= 400 ? res.status : 502,
			error: 'Couldn’t resolve the firm, so nothing was changed. Try again.'
		};
	}
	const tenant = (await res.json()) as { id: number; uid: string };
	return { ok: true, id: tenant.id, uid: tenant.uid };
}
