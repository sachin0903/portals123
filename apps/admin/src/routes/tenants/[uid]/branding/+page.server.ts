import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { targetJson } from '$lib/operate';
import { can } from '$lib/permissions';
import type { Branding } from '@graphene/auth';
import { cardProductTypes, type ShelfProduct } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

type BrandingResponse = {
	name: string;
	portal?: string;
	branding: Branding;
};

// Branding is stored + rendered per portal. The studio edits both at once, so we load and persist
// the client and platform (adviser) branding side by side.
async function loadPortal(
	fetch: typeof globalThis.fetch,
	uid: string,
	portal: 'client' | 'platform',
	token: string | null
): Promise<BrandingResponse> {
	const res = await fetch(`${apiUrl()}/admin/tenants/${uid}/branding?portal=${portal}`, {
		headers: authHeaders(token)
	});
	if (res.status === 404) error(404, 'Tenant not found.');
	if (!res.ok) error(res.status, await detail(res));
	return res.json();
}

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The numeric id the `/admin/tenants/{tenant_id}` endpoints take. Resolved once by the
	// [uid] layout, so this costs no extra round trip.
	if (!can(locals.permissions, locals.role, 'tenants:read')) error(403, 'Not authorised');

	const [client, platform, products] = await Promise.all([
		loadPortal(fetch, params.uid, 'client', locals.accessToken),
		loadPortal(fetch, params.uid, 'platform', locals.accessToken),
		targetJson<ShelfProduct[]>(fetch, locals.accessToken, params.uid, '/catalog/products')
	]);
	const shelf = products.ok && Array.isArray(products.data) ? products.data : null;

	return {
		name: client.name || platform.name,
		client: client.branding ?? {},
		platform: platform.branding ?? {},
		canManage: can(locals.permissions, locals.role, 'tenants:write'),
		// Recomposing a tenant's portal PAGES additionally needs pages:write (the six-splits
		// re-key): the admin branding PUT diff-gates composition changes on it, so the studio's
		// Pages view goes read-only without it while the Design tabs stay on tenants:write.
		canEditPages: can(locals.permissions, locals.role, 'pages:write'),
		productTypes: shelf ? cardProductTypes(shelf) : [],
		productTypesUnavailable: shelf === null
	};
};

// Must match the API's allowlist AND what BrandingEditor offers — `icon` was missing here, so
// every icon upload the editor offered failed with "Unknown asset kind".
const ASSET_KINDS = new Set(['logo', 'logo-dark', 'icon', 'favicon', 'card-art']);

export const actions: Actions = {
	save: async ({ request, params, locals, fetch }) => {
		const tenantUid = params.uid;
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'tenants:write')) error(403, 'Not authorised');

		const data = await request.formData();
		let client: Branding;
		let platform: Branding;
		try {
			client = JSON.parse(String(data.get('client') ?? '{}'));
			platform = JSON.parse(String(data.get('platform') ?? '{}'));
		} catch {
			return fail(400, { error: 'Invalid branding payload.' });
		}
		const name = String(data.get('name') ?? '').trim();

		// Persist both portals. The firm name is shared, so it goes on both writes.
		for (const [portal, branding] of [
			['client', client],
			['platform', platform]
		] as const) {
			const res = await fetch(`${apiUrl()}/admin/tenants/${tenantUid}/branding?portal=${portal}`, {
				method: 'PUT',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ name: name || null, branding })
			});
			if (!res.ok) return fail(res.status, { error: await detail(res) });
		}
		return { ok: true };
	},

	// Forwards a browser-uploaded file to the API asset endpoint with the server-held token, so the
	// API access token never reaches the browser. Returns the stored { url }.
	upload: async ({ request, params, url, locals, fetch }) => {
		const tenantUid = params.uid;
		if (!locals.user) redirect(303, '/login');
		if (!can(locals.permissions, locals.role, 'tenants:write')) error(403, 'Not authorised');

		const kind = url.searchParams.get('kind') ?? '';
		if (!ASSET_KINDS.has(kind)) return fail(400, { error: 'Unknown asset kind.' });

		const incoming = await request.formData();
		const file = incoming.get('file');
		if (!(file instanceof File)) return fail(400, { error: 'No file provided.' });

		const body = new FormData();
		body.append('file', file, file.name);
		// Optional: the PNG the studio drew of an SVG logo/icon, stored beside it for email.
		const emailCopy = incoming.get('email_copy');
		if (emailCopy instanceof File) body.append('email_copy', emailCopy, emailCopy.name);
		const res = await fetch(
			`${apiUrl()}/admin/tenants/${tenantUid}/branding/asset?kind=${encodeURIComponent(kind)}`,
			{
				method: 'POST',
				// No Content-Type — let fetch set the multipart boundary.
				headers: authHeaders(locals.accessToken),
				body
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const out: { src: string } = await res.json();
		return { uploaded: true, url: out.src };
	}
};
