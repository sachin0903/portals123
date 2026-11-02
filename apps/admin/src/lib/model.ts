import { apiUrl, authHeaders } from '$lib/api';

/**
 * The model tenant — the role + email preset every new firm clones on creation. Resolved via the
 * admin API because the model tenant has no logins of its own, so it can only be reached through the
 * cross-tenant door.
 *
 * Returns BOTH keys because callers genuinely need different ones and they are not interchangeable:
 * the API's own routes take the numeric `id` (`/admin/tenants/{id}/…`), while the app's tenant routes
 * are uid-addressed (`/tenants/{uid}/…`, enforced by `routes/tenants/tenant-links.test.ts`). Building
 * a link from the id 404s; calling the API with the uid does not resolve. One function returning both
 * beats three near-identical resolvers, which is what this replaced.
 */
export async function modelTenant(
	accessToken: string | null,
	fetch: typeof globalThis.fetch
): Promise<{ id: number; uid: string }> {
	const res = await fetch(`${apiUrl()}/admin/model`, { headers: authHeaders(accessToken) });
	if (!res.ok) throw new Error('model tenant not found');
	const m = await res.json();
	return { id: m.id as number, uid: m.uid as string };
}
