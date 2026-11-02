import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import { toForm, type ApiForm } from '$lib/forms';
import type { Form } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

/** The listing's rows, in one streamed object — or that the read failed. */
export type FormsBook = { forms: Form[]; loadError: boolean };

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// The search lives in the URL so the API answers it — across every row, not across
	// the ones already fetched — and so it survives a reload and can be linked.
	const query = url.searchParams.get('q')?.trim() ?? '';
	if (!can(locals.permissions, locals.role, 'forms:read')) {
		return {
			query,
			canManage: false,
			book: { forms: [], loadError: false } satisfies FormsBook
		};
	}
	// The query and the permission are resolved SYNCHRONOUSLY and the rows are STREAMED: the
	// toolbar renders the moment the page is navigated to (already showing the search in the URL)
	// and the table fills in when the API answers, rather than the first byte waiting on it
	// (`clients/+page.server.ts` is the pattern). `redirect` stays out here.
	return {
		query,
		canManage: can(locals.permissions, locals.role, 'forms:write'),
		book: loadBook(locals.accessToken, fetch, query)
	};
};

async function loadBook(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: string
): Promise<FormsBook> {
	// Include archived so the page can offer an Archived view + un-archive without a reload.
	const res = await fetchJson<ApiForm[]>(
		fetch,
		`${apiUrl()}/forms?include_archived=true&${new URLSearchParams(query ? { q: query } : {})}`,
		authHeaders(accessToken)
	);
	// A failed fetch surfaces as a retry banner rather than an empty page (a transient 401 at the
	// token-refresh boundary must not read as "no forms").
	return { forms: res.ok ? res.data.map(toForm) : [], loadError: !res.ok };
}

export const actions: Actions = {
	/** Create a form (initial draft). Returns the new uid so the page can jump into the builder. */
	create: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'forms:write')) {
			return fail(403, { error: 'You do not have permission to create forms.' });
		}
		const form = await request.formData();
		const name = String(form.get('name') ?? '').trim();
		const code = String(form.get('code') ?? '').trim();
		if (!name || !code) return fail(400, { error: 'Name and reference code are required.' });
		const documentType = String(form.get('document_type') ?? '').trim();
		const description = String(form.get('description') ?? '').trim();
		const res = await fetch(`${apiUrl()}/forms`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				name,
				code,
				entity_type: String(form.get('entity_type') ?? 'applicant'),
				document_type: documentType || null,
				description: description || null
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const created: ApiForm = await res.json();
		return { ok: true, uid: created.uid };
	}
};
