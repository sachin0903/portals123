import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

export type StorageObject = { key: string; size: number; last_modified: string };

/** A sub-"folder" with its recursive rollup (API's `FolderOut`): total size + object count, the
 *  latest-modified timestamp, and the file extensions found within. `truncated` ⇒ the folder was
 *  larger than the scan cap, so size/count are a lower bound. */
export type StorageFolder = {
	prefix: string;
	size: number;
	count: number;
	last_modified: string | null;
	exts: string[];
	truncated: boolean;
	/** Human name when the folder is a document/attachment bucket; null ⇒ show the raw folder name. */
	label: string | null;
};

/** One page of the bucket browse (API's `ObjectPageOut`). `per_tenant` is false while this lists the
 *  shared documents bucket (today's interim), true once the tenant has its own provisioned bucket. */
export type ObjectPage = {
	bucket: string;
	prefix: string;
	per_tenant: boolean;
	folders: StorageFolder[];
	objects: StorageObject[];
	object_types: string[];
	/** Resolved names for the current prefix's segments (raw segment → name) — for the breadcrumb. */
	prefix_labels: Record<string, string>;
	next_token: string | null;
};

export const load: PageServerLoad = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The numeric id the `/admin/tenants/{tenant_id}` endpoints take. Resolved once by the
	// [uid] layout, so this costs no extra round trip.
	// `book:read`, not `tenants:read`: this browser lists a firm's CLIENT files (the API's
	// storage/objects endpoint moved to the same gate) — tenant administration does not imply
	// reading the firm's client data, exactly as with the /book section.
	if (!can(locals.permissions, locals.role, 'book:read'))
		error(403, 'Not authorised to browse tenant files.');

	// prefix = the "folder" being browsed; token = the S3 continuation token for the next page.
	const prefix = url.searchParams.get('prefix') ?? '';
	const token = url.searchParams.get('token') ?? '';
	const query = new URLSearchParams();
	if (prefix) query.set('prefix', prefix);
	if (token) query.set('token', token);

	let page: ObjectPage | null = null;
	let apiError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/admin/tenants/${params.uid}/storage/objects?${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) {
			// Tolerate the pre-rollup API shape (folders as bare strings): the API doesn't hot-reload
			// on Windows, so a not-yet-restarted server can still return strings — normalise them to
			// empty-stat folders rather than 500 the page. Real size/type stats appear once the API
			// serves `FolderOut` objects.
			const raw = (await res.json()) as Omit<ObjectPage, 'folders'> & {
				folders: (string | StorageFolder)[];
				object_types?: string[];
				prefix_labels?: Record<string, string>;
			};
			page = {
				...raw,
				folders: (raw.folders ?? []).map((f) =>
					typeof f === 'string'
						? {
								prefix: f,
								size: 0,
								count: 0,
								last_modified: null,
								exts: [],
								truncated: false,
								label: null
							}
						: f
				),
				object_types: raw.object_types ?? [],
				prefix_labels: raw.prefix_labels ?? {}
			};
		} else if (res.status === 503) apiError = 'Object storage is unavailable in this environment.';
		else apiError = await detail(res);
	} catch {
		apiError = 'The service is unavailable.';
	}

	// `token` is echoed back so the page can keep a stack of the tokens that led here (S3
	// continuation tokens are forward-only, so a Back arrow needs the trail, not just `next_token`).
	return { tenantUid: params.uid, prefix, token, page, apiError };
};
