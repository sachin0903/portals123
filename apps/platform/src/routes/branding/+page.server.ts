import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { Branding } from '@graphene/auth';
import {
	cardProductTypes,
	type ClientClassification,
	type ClientGroup,
	type ShelfProduct
} from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

type BrandingResponse = {
	name: string;
	portal?: string;
	branding: Branding;
};

/** Everything the studio edits, in one streamed object — or why it cannot be shown. */
export type Studio = {
	/** The API refused (403) a caller the portal's own check let through — the same banner as
	 *  having no `branding:read` at all. */
	forbidden: boolean;
	/** A branding read failed (not a refusal); the studio shows the message instead of an editor
	 *  seeded with blanks, which would read as "no branding set" and invite a save over the top. */
	error: string | null;
	name: string;
	client: Branding;
	platform: Branding;
	classifications: ClientClassification[];
	groups: ClientGroup[];
	productTypes: ReturnType<typeof cardProductTypes>;
	productTypesUnavailable: boolean;
};

const EMPTY_STUDIO: Omit<Studio, 'forbidden' | 'error'> = {
	name: '',
	client: {} as Branding,
	platform: {} as Branding,
	classifications: [],
	groups: [],
	productTypes: [],
	productTypesUnavailable: false
};

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'branding:read')) {
		return { authorised: false, canManage: false, canEditPages: false, studio: null };
	}

	// The permission answers are SYNCHRONOUS and the studio is STREAMED: the heading renders the
	// moment the page is navigated to and the editor lands behind it, rather than the first byte
	// waiting on four API reads (`clients/+page.server.ts` is the pattern). This page already pays
	// more than the others before its loader runs — the hook bypasses the host-config cache here
	// on purpose, so a colour save paints on the next load — which is one more reason not to add
	// the reads to the same stretch. `redirect` stays out here.
	return {
		authorised: true,
		canManage: can(locals.permissions, locals.role, 'branding:write'),
		// Composing the client portal's PAGES is pages:write (the six-splits re-key): the API's
		// branding PUT diff-gates composition changes on it, so the studio's Pages view goes
		// read-only without it while the Design tabs stay editable on branding:write alone.
		canEditPages: can(locals.permissions, locals.role, 'pages:write'),
		studio: loadStudio(locals.accessToken, fetch)
	};
};

async function loadStudio(
	accessToken: string | null,
	fetch: typeof globalThis.fetch
): Promise<Studio> {
	const headers = authHeaders(accessToken);
	// Caught whole: a streamed promise that rejects is a broken page rather than a banner.
	try {
		// All four in ONE round trip. Both portals' branding side by side (the studio edits them
		// together), plus the client books and the product shelf — neither of which depends on the
		// branding, though they used to wait for it as a second pair.
		const [clientRes, platformRes, classRes, groupRes, productsRes] = await Promise.all([
			fetch(`${apiUrl()}/firm/branding?portal=client`, { headers }),
			fetch(`${apiUrl()}/firm/branding?portal=platform`, { headers }),
			fetch(`${apiUrl()}/crm/classifications`, { headers }),
			fetch(`${apiUrl()}/crm/groups`, { headers }),
			fetch(`${apiUrl()}/catalog/products`, { headers })
		]);

		// The API also enforces branding:read (403).
		if (clientRes.status === 403 || platformRes.status === 403) {
			return { ...EMPTY_STUDIO, forbidden: true, error: null };
		}
		if (!clientRes.ok || !platformRes.ok) {
			const bad = !clientRes.ok ? clientRes : platformRes;
			return { ...EMPTY_STUDIO, forbidden: false, error: await detail(bad) };
		}

		const client: BrandingResponse = await clientRes.json();
		const platform: BrandingResponse = await platformRes.json();
		const listed = classRes.ok ? await classRes.json() : [];
		const grouped = groupRes.ok ? await groupRes.json() : [];
		const shelf = productsRes.ok ? ((await productsRes.json()) as ShelfProduct[]) : null;
		return {
			forbidden: false,
			error: null,
			name: client.name || platform.name,
			client: client.branding ?? ({} as Branding),
			platform: platform.branding ?? ({} as Branding),
			classifications: Array.isArray(listed) ? (listed as ClientClassification[]) : [],
			groups: Array.isArray(grouped) ? (grouped as ClientGroup[]) : [],
			productTypes: Array.isArray(shelf) ? cardProductTypes(shelf) : [],
			productTypesUnavailable: !Array.isArray(shelf)
		};
	} catch {
		return {
			...EMPTY_STUDIO,
			forbidden: false,
			error: 'Could not load your branding. Check your connection and try again.'
		};
	}
}

// Must match the API's allowlist AND what BrandingEditor offers — `icon` was missing here, so every
// icon upload a firm made from its own branding tab failed with "Unknown asset kind" (the admin
// per-tenant editor had already been fixed; firm self-service had not).
const ASSET_KINDS = new Set(['logo', 'logo-dark', 'icon', 'favicon', 'card-art']);

/** Reject unless the caller can manage branding; keeps every action gated in one place. */
function requireManage(locals: App.Locals): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'branding:write')) redirect(303, '/branding');
}

export const actions: Actions = {
	save: async ({ request, locals, fetch }) => {
		requireManage(locals);
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

		// Persist both portals; the firm name is shared, so it goes on both writes.
		for (const [portal, branding] of [
			['client', client],
			['platform', platform]
		] as const) {
			const res = await fetch(`${apiUrl()}/firm/branding?portal=${portal}`, {
				method: 'PUT',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ name: name || null, branding })
			});
			if (!res.ok) return fail(res.status, { error: await detail(res) });
		}
		return { ok: true };
	},

	// Forwards a browser-uploaded file to the API asset endpoint with the server-held token, so the
	// API access token never reaches the browser. Returns an opaque same-origin asset source.
	upload: async ({ request, url, locals, fetch }) => {
		requireManage(locals);
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
		const res = await fetch(`${apiUrl()}/firm/branding/asset?kind=${encodeURIComponent(kind)}`, {
			method: 'POST',
			// No Content-Type — let fetch set the multipart boundary.
			headers: authHeaders(locals.accessToken),
			body
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const out: { src: string } = await res.json();
		return { uploaded: true, url: out.src };
	},

	saveClassification: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		let body: Record<string, unknown>;
		try {
			body = JSON.parse(String(data.get('classification') ?? '{}')) as Record<string, unknown>;
		} catch {
			return fail(400, { error: 'Invalid classification payload.' });
		}
		const uid = String(data.get('uid') ?? '').trim();
		const res = await fetch(
			uid ? `${apiUrl()}/crm/classifications/${uid}` : `${apiUrl()}/crm/classifications`,
			{
				method: uid ? 'PATCH' : 'POST',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify(body)
			}
		);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	saveGroup: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		let body: Record<string, unknown>;
		try {
			body = JSON.parse(String(data.get('group') ?? '{}')) as Record<string, unknown>;
		} catch {
			return fail(400, { error: 'Invalid group payload.' });
		}
		const uid = String(data.get('uid') ?? '').trim();
		const res = await fetch(uid ? `${apiUrl()}/crm/groups/${uid}` : `${apiUrl()}/crm/groups`, {
			method: uid ? 'PATCH' : 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	deleteGroup: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const uid = String((await request.formData()).get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No group specified.' });
		const res = await fetch(`${apiUrl()}/crm/groups/${uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	deleteClassification: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const uid = String((await request.formData()).get('uid') ?? '').trim();
		if (!uid) return fail(400, { error: 'No book specified.' });
		const res = await fetch(`${apiUrl()}/crm/classifications/${uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
