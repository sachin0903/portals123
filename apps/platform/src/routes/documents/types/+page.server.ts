import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { Actions, PageServerLoad } from './$types';

/** A row of the tenant's managed type registry (API's `DocumentTypeOut`). `kind` splits it into the
 *  two vocabularies the page manages: document classifications and supporting-file types. */
export type DocumentType = {
	id: number;
	/** The row's address — `/documents/types/{uid}` is what an edit PATCHes. */
	uid: string;
	kind: 'document' | 'file';
	slug: string;
	label: string;
	sort_order: number;
	archived: boolean;
};

/** The registry as the page renders it — the rows, or why there are none. One streamed object. */
export type Registry = {
	types: DocumentType[];
	/** The API's refusal or failure, for the page to show instead of an empty taxonomy. */
	error: string | null;
};

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// Viewing the taxonomy needs document_types:read; editing it (the add form, inline rename,
	// enable/disable, Save changes) additionally needs document_types:write — `canManage` is what
	// the page uses to render those controls only for someone who can actually use them, rather
	// than showing a button that redirects away when clicked.
	if (!can(locals.permissions, locals.role, 'document_types:read')) {
		return { authorised: false, canManage: false, registry: null };
	}
	const canManage = can(locals.permissions, locals.role, 'document_types:write');
	// The permission answers are SYNCHRONOUS and the registry is STREAMED: the heading and the two
	// sections' frames render the moment the page is navigated to and the rows land behind them,
	// rather than the response waiting on the API (`clients/+page.server.ts` is the pattern).
	// `redirect` stays out here — it has to be thrown before the response starts.
	return { authorised: true, canManage, registry: loadRegistry(locals.accessToken, fetch) };
};

async function loadRegistry(
	accessToken: string | null,
	fetch: typeof globalThis.fetch
): Promise<Registry> {
	// A failure is reported, never rendered as an empty taxonomy: "no types yet" to a firm with
	// twenty because the API 500'd is the worst thing this page can do. Caught here too, because a
	// streamed promise that rejects is a broken page rather than a banner.
	try {
		const res = await fetch(`${apiUrl()}/documents/types?include_archived=true`, {
			headers: authHeaders(accessToken)
		});
		if (!res.ok) return { types: [], error: await detail(res) };
		return { types: (await res.json()) as DocumentType[], error: null };
	} catch {
		return {
			types: [],
			error: 'Could not load the document types. Check your connection and try again.'
		};
	}
}

function requireManage(locals: App.Locals): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'document_types:write')) redirect(303, '/documents');
}

export const actions: Actions = {
	/** Add a document type to the taxonomy. */
	create: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const form = await request.formData();
		const slug = String(form.get('slug') ?? '').trim();
		const label = String(form.get('label') ?? '').trim();
		const kind = form.get('kind') === 'file' ? 'file' : 'document';
		if (!slug || !label) return fail(400, { error: 'A slug and a label are both required.' });
		const res = await fetch(`${apiUrl()}/documents/types`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ slug, label, kind })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Rename / reorder / archive-or-restore a type (slug is immutable). */
	update: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No type specified.' });
		const payload: Record<string, unknown> = {};
		if (form.get('label') !== null) payload.label = String(form.get('label')).trim();
		if (form.get('archived') !== null) payload.archived = form.get('archived') === 'true';
		const res = await fetch(`${apiUrl()}/documents/types/${uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(payload)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Batch-apply the label + enabled state of every row in one submit (the "Save changes"
	 *  button). Each row is PATCHed with its current label + archived flag; unchanged rows are a
	 *  harmless no-op, so the client can post the whole table. */
	saveAll: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const form = await request.formData();
		const uids = form.getAll('uid').map((v) => String(v));
		const labels = form.getAll('label').map((v) => String(v).trim());
		const archived = form.getAll('archived').map((v) => v === 'true');
		for (let i = 0; i < uids.length; i++) {
			if (!uids[i] || !labels[i]) continue;
			const res = await fetch(`${apiUrl()}/documents/types/${uids[i]}`, {
				method: 'PATCH',
				headers: jsonHeaders(locals.accessToken),
				body: JSON.stringify({ label: labels[i], archived: archived[i] })
			});
			if (!res.ok) return fail(res.status, { error: await detail(res) });
		}
		return { ok: true };
	}
};
