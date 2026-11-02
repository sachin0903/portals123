import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, type Tenant } from '$lib/api';
import { can } from '$lib/permissions';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');

	const canConfigure = can(locals.permissions, locals.role, 'tenants:read');
	const canOperate = can(locals.permissions, locals.role, 'tenant_portal:read');

	// The firm's record, addressed by the uid the URL already carries. This used to call
	// `by-uid` to turn that uid into a numeric id, because the ~40 endpoints under this section
	// took an `int` — they take the uid now, so this reads the record and nothing else.
	if (canConfigure || canOperate) {
		const headers = authHeaders(locals.accessToken);
		const [res, iconUrl] = await Promise.all([
			fetch(`${apiUrl()}/admin/tenants/${params.uid}`, { headers }),
			// The firm's own mark, for the sidebar's scope header. Read alongside the record, never
			// instead of it: the branding read is `tenants:read` gated (an operate-only principal
			// gets nothing, and the header falls back to a glyph), and any failure here is a missing
			// picture, not a missing page.
			canConfigure ? tenantIconUrl(fetch, params.uid, headers) : null
		]);
		if (res.status === 403) error(403, 'Not authorised to access this tenant.');
		if (res.status === 404) error(404, 'Tenant not found.');
		if (!res.ok) error(res.status, await detail(res));

		const tenant: Tenant = await res.json();
		return {
			tenant,
			tenantIconUrl: iconUrl,
			canConfigure,
			canManage: can(locals.permissions, locals.role, 'tenants:write')
		};
	}

	// The one exception to tenants:read: a MODEL-ONLY operator (model:read/write, no tenants:read)
	// editing the model preset — the least-privilege path the email-template surface adds. The read
	// above is tenants:read gated and would 403 them, so serve the model tenant from /admin/model,
	// which is model:read gated and returns the full Tenant shape. Any non-model uid, or a caller
	// without model:read, falls through to 403 — this branch grants nothing beyond the model tenant.
	const modelRes = await fetch(`${apiUrl()}/admin/model`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!modelRes.ok) error(403, 'Not authorised');
	const model: Tenant = await modelRes.json();
	if (model.uid !== params.uid || !can(locals.permissions, locals.role, 'model:read')) {
		error(403, 'Not authorised');
	}
	return {
		tenant: model,
		tenantIconUrl: null,
		canConfigure: true,
		canManage: can(locals.permissions, locals.role, 'model:write')
	};
};

/**
 * The firm's icon from its PLATFORM-portal branding — the portal this section mirrors, so the
 * mark in our sidebar is the one the firm's own staff see in theirs. `null` for a firm that has
 * set none, and for any failure: this is decoration on the scope header, not data.
 */
async function tenantIconUrl(
	fetch: typeof globalThis.fetch,
	uid: string,
	headers: HeadersInit
): Promise<string | null> {
	try {
		const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/branding?portal=platform`, {
			headers
		});
		if (!res.ok) return null;
		const body = (await res.json()) as { branding?: { icon_url?: string | null } | null };
		const url = body.branding?.icon_url;
		return typeof url === 'string' && url.trim() ? url : null;
	} catch {
		return null;
	}
}
